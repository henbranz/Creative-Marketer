from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol
from uuid import UUID

from jsonschema import Draft202012Validator

from creative_marketer.agent_runtime.domain import ResearchSnapshot, canonical_digest
from creative_marketer.audit.application import AuditWriter
from creative_marketer.audit.builders import tenant_audit
from creative_marketer.audit.domain import AuditOutcome
from creative_marketer.audit.safety import safe_metadata
from creative_marketer.catalog.domain import ProductKnowledgeSnapshot
from creative_marketer.events.application import OutboxWriter
from creative_marketer.events.contracts import EventContractRegistry
from creative_marketer.events.domain import tenant_event
from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.identity.domain import MembershipRole, MembershipStatus

from .domain import (
    CreativeConcept,
    CreativeConceptDecision,
    CreativeConceptSet,
    CreativeDecisionState,
    CreativeNotFound,
    CreativePermissionDenied,
    CreativeStrategyContext,
    CreativeStrategyRequest,
    InvalidCreativeAssetReference,
    InvalidCreativeClaimReference,
    InvalidCreativeOutput,
    InvalidCreativeResearchReference,
    ProhibitedCreativeClaim,
    normalized_phrase,
    product_claim_refs,
)

CREATIVE_CONTRACT_KEY = "creative.creative_concept_set"
CREATIVE_CONTRACT_VERSION = 1


class CreativeRepository(Protocol):
    async def get_concept(
        self, concept_id: UUID, *, for_update: bool = False
    ) -> CreativeConcept | None: ...
    async def get_set(self, set_id: UUID) -> CreativeConceptSet | None: ...
    async def list_sets(self, product_id: UUID) -> tuple[CreativeConceptSet, ...]: ...
    async def add_decision(self, value: CreativeConceptDecision) -> None: ...
    async def current_decision(self, concept_id: UUID) -> CreativeConceptDecision | None: ...


class CreativeUnitOfWork(Protocol):
    creative: CreativeRepository
    audit: AuditWriter
    outbox: OutboxWriter

    async def __aenter__(self) -> CreativeUnitOfWork: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...
    async def commit(self) -> None: ...


class CreativeUnitOfWorkFactory(Protocol):
    def __call__(self, tenant_id: UUID) -> CreativeUnitOfWork: ...


def load_creative_output_schema() -> Mapping[str, object]:
    path = Path(__file__).with_name("schemas") / "creative.creative_concept_set.v1.json"
    return json.loads(path.read_text())  # type: ignore[no-any-return]


def build_creative_context(
    product: ProductKnowledgeSnapshot,
    research: ResearchSnapshot,
    request: CreativeStrategyRequest,
) -> CreativeStrategyContext:
    if product.schema_version != 2:
        raise InvalidCreativeOutput("Creative Strategist requires Product snapshot V2")
    assets_value = product.content.get("assets", ())
    assets = (
        tuple(dict(item) for item in assets_value if isinstance(item, Mapping))
        if isinstance(assets_value, (list, tuple))
        else ()
    )
    claims = product_claim_refs(product.digest, product.content)
    findings = tuple(item.semantic() for item in research.findings)
    document = {
        "schema_version": 1,
        "product_snapshot_id": str(product.id),
        "product_snapshot_digest": product.digest,
        "research_snapshot_id": str(research.id),
        "research_snapshot_digest": research.semantic_digest,
        "research_agent_run_id": str(research.agent_run_id),
        "asset_manifest": assets,
        "request": {
            "concept_count": request.concept_count,
            "channel_intent": request.channel_intent.value,
        },
    }
    return CreativeStrategyContext(
        product.id,
        product.digest,
        product.schema_version,
        research.id,
        research.semantic_digest,
        research.agent_run_id,
        dict(product.content),
        findings,
        research.research_gaps,
        assets,
        claims,
        request,
        canonical_digest(document),
    )


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        return [item for child in value.values() for item in _strings(child)]
    if isinstance(value, (list, tuple)):
        return [item for child in value for item in _strings(child)]
    return []


def validate_creative_output(
    output: Mapping[str, object],
    *,
    tenant_id: UUID,
    product_id: UUID,
    agent_run_id: UUID,
    context: CreativeStrategyContext,
) -> CreativeConceptSet:
    try:
        Draft202012Validator(load_creative_output_schema()).validate(dict(output))
    except Exception as error:
        raise InvalidCreativeOutput("provider output does not match creative contract") from error
    raw_concepts = output["concepts"]
    assert isinstance(raw_concepts, list)
    if len(raw_concepts) != context.request.concept_count:
        raise InvalidCreativeOutput("provider returned the wrong concept count")
    keys = [str(item["concept_key"]) for item in raw_concepts]
    titles = [normalized_phrase(str(item["title"])) for item in raw_concepts]
    hooks = [canonical_digest(item["hook"]) for item in raw_concepts]
    scenes = [canonical_digest(item["scenes"]) for item in raw_concepts]
    if any(len(set(values)) != len(values) for values in (keys, titles, hooks, scenes)):
        raise InvalidCreativeOutput("concept set is not deterministically diverse")
    finding_keys = {str(item["key"]) for item in context.research_findings}
    claim_refs = {item.key: item.text for item in context.product_claims}
    assets = {
        str(item.get("asset_id")): item
        for item in context.asset_manifest
        if item.get("status") == "ready" or item.get("status") == "READY"
    }
    prohibited = _prohibited_claims(context.product_context)
    required_disclaimers = _required_disclaimers(context.product_context)
    set_id = UUID(
        bytes=__import__("hashlib").sha256(f"{agent_run_id}:concept-set".encode()).digest()[:16],
        version=4,
    )
    concepts: list[CreativeConcept] = []
    for ordinal, raw in enumerate(raw_concepts, 1):
        assert isinstance(raw, dict)
        refs = raw["supporting_research_refs"]
        if any(
            str(ref["research_snapshot_id"]) != str(context.research_snapshot_id)
            or str(ref["finding_key"]) not in finding_keys
            for ref in refs
        ):
            raise InvalidCreativeResearchReference(
                "concept references research outside frozen snapshot"
            )
        all_asset_requirements = list(raw["required_assets"])
        for scene in raw["scenes"]:
            all_asset_requirements.extend(scene["asset_requirements"])
        for requirement in all_asset_requirements:
            if requirement["kind"] == "EXISTING_ASSET":
                asset = assets.get(str(requirement["asset_id"]))
                uses = asset.get("allowed_uses", ()) if asset else ()
                if not isinstance(uses, (list, tuple)):
                    uses = ()
                if asset is None or "generation_input" not in {
                    str(item).casefold() for item in uses
                }:
                    raise InvalidCreativeAssetReference(
                        "existing asset is absent or lacks generation-input rights"
                    )
        used_claims: set[str] = set()
        for point in raw["message_points"]:
            reference = point["product_claim_ref"]
            if point["kind"] == "PRODUCT_FACT":
                if reference not in claim_refs:
                    raise InvalidCreativeClaimReference(
                        "PRODUCT_FACT lacks frozen Product authority"
                    )
                used_claims.add(str(reference))
            elif reference is not None:
                raise InvalidCreativeClaimReference("only PRODUCT_FACT may bind a Product claim")
        normalized_text = normalized_phrase(" ".join(_strings(raw)))
        if any(phrase and phrase in normalized_text for phrase in prohibited):
            raise ProhibitedCreativeClaim("concept contains a prohibited normalized phrase")
        if used_claims and not required_disclaimers.issubset(
            {normalized_phrase(str(item)) for item in raw["required_disclaimers"]}
        ):
            raise InvalidCreativeClaimReference("required Product disclaimers are missing")
        duration = sum(int(scene["estimated_duration_seconds"]) for scene in raw["scenes"])
        if duration != int(raw["estimated_duration_seconds"]):
            raise InvalidCreativeOutput("scene durations must sum to concept duration")
        payload = dict(raw)
        concepts.append(
            CreativeConcept(
                tenant_id,
                product_id,
                set_id,
                keys[ordinal - 1],
                ordinal,
                payload,
                canonical_digest(payload),
            )
        )
    semantic = {
        "schema_version": 1,
        "product_snapshot_id": str(context.product_snapshot_id),
        "product_snapshot_digest": context.product_snapshot_digest,
        "research_snapshot_id": str(context.research_snapshot_id),
        "research_snapshot_digest": context.research_snapshot_digest,
        "input_context_digest": context.context_digest,
        "concepts": [dict(item.payload) for item in concepts],
    }
    return CreativeConceptSet(
        tenant_id,
        product_id,
        agent_run_id,
        context.product_snapshot_id,
        context.product_snapshot_digest,
        context.research_snapshot_id,
        context.research_snapshot_digest,
        context.context_digest,
        tuple(concepts),
        canonical_digest(semantic),
        id=set_id,
    )


def _prohibited_claims(content: Mapping[str, object]) -> set[str]:
    values: list[str] = []
    for section, keys in (
        ("brand_profile", ("prohibited_claims",)),
        ("profile", ("prohibited_claims",)),
        ("brief", ("prohibited_messaging",)),
    ):
        value = content.get(section)
        if isinstance(value, Mapping):
            for key in keys:
                raw = value.get(key, ())
                if isinstance(raw, (list, tuple)):
                    values.extend(str(item) for item in raw)
    return {normalized_phrase(item) for item in values if normalized_phrase(item)}


def _required_disclaimers(content: Mapping[str, object]) -> set[str]:
    brief = content.get("brief")
    raw = brief.get("required_disclaimers", ()) if isinstance(brief, Mapping) else ()
    return (
        {normalized_phrase(str(item)) for item in raw} if isinstance(raw, (list, tuple)) else set()
    )


@dataclass(slots=True)
class CreativeService:
    uow_factory: CreativeUnitOfWorkFactory

    async def list_sets(
        self, context: ExecutionContext, product_id: UUID
    ) -> tuple[CreativeConceptSet, ...]:
        async with self.uow_factory(context.tenant_id) as uow:
            return await uow.creative.list_sets(product_id)

    async def get_set(self, context: ExecutionContext, set_id: UUID) -> CreativeConceptSet:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.creative.get_set(set_id)
            if value is None:
                raise CreativeNotFound("CreativeConceptSet not found")
            return value

    async def get_concept(
        self, context: ExecutionContext, concept_id: UUID
    ) -> tuple[CreativeConcept, CreativeConceptDecision | None]:
        async with self.uow_factory(context.tenant_id) as uow:
            value = await uow.creative.get_concept(concept_id)
            if value is None:
                raise CreativeNotFound("CreativeConcept not found")
            return value, await uow.creative.current_decision(concept_id)

    async def decide(
        self,
        context: ExecutionContext,
        concept_id: UUID,
        state: CreativeDecisionState,
        *,
        reason_code: str | None = None,
        note: str | None = None,
    ) -> CreativeConceptDecision:
        if (
            context.membership_status is not MembershipStatus.ACTIVE
            or context.membership_role not in {MembershipRole.OWNER, MembershipRole.ADMIN}
            or context.user_id is None
        ):
            raise CreativePermissionDenied("Creative decisions require owner or admin")
        async with self.uow_factory(context.tenant_id) as uow:
            concept = await uow.creative.get_concept(concept_id, for_update=True)
            if concept is None:
                raise CreativeNotFound("CreativeConcept not found")
            decision = CreativeConceptDecision(
                context.tenant_id,
                concept.product_id,
                concept.id,
                state,
                context.user_id,
                reason_code,
                note,
            )
            await uow.creative.add_decision(decision)
            action = {
                CreativeDecisionState.SHORTLISTED: "creative.concept.shortlisted",
                CreativeDecisionState.APPROVED_FOR_PRODUCTION: (
                    "creative.concept.approved_for_production"
                ),
                CreativeDecisionState.REJECTED: "creative.concept.rejected",
            }[state]
            await uow.audit.append(
                tenant_audit(
                    context,
                    action=action,
                    outcome=AuditOutcome.SUCCESS,
                    resource_type="creative_concept",
                    resource_id=str(concept.id),
                    metadata=safe_metadata(
                        {
                            "concept_id": str(concept.id),
                            "concept_digest": concept.semantic_digest,
                            "decision_id": str(decision.id),
                            "decision": state.value,
                        }
                    ),
                )
            )
            if state is CreativeDecisionState.APPROVED_FOR_PRODUCTION:
                event_type = "creative.concept.approved_for_production.v1"
                contracts = EventContractRegistry()
                await uow.outbox.append(
                    tenant_event(
                        context,
                        event_type=event_type,
                        schema_version=1,
                        aggregate_type="creative_concept",
                        aggregate_id=concept.id,
                        payload={
                            "concept_id": str(concept.id),
                            "concept_set_id": str(concept.concept_set_id),
                            "product_id": str(concept.product_id),
                            "concept_digest": concept.semantic_digest,
                            "decision_id": str(decision.id),
                        },
                        payload_schema_digest=contracts.schema_digest(event_type),
                        occurred_at=datetime.now(UTC),
                    )
                )
            await uow.commit()
            return decision
