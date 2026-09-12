from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from creative_marketer.identity.application.authentication import ExecutionContext
from creative_marketer.infrastructure.database.agent_governance_schema import (
    agent_activations,
    agent_definitions,
    agent_versions,
)
from creative_marketer.infrastructure.database.agent_runtime_schema import (
    agent_runs,
    research_snapshots,
)
from creative_marketer.infrastructure.database.catalog_schema import (
    assets,
    brands,
    product_knowledge_snapshots,
    products,
)
from creative_marketer.infrastructure.database.creative_schema import (
    concept_decisions,
    concept_sets,
    concepts,
)
from creative_marketer.infrastructure.database.knowledge_schema import (
    projection_changes,
    projection_nodes,
)
from creative_marketer.infrastructure.database.production_schema import (
    asset_lineage,
    generation_jobs,
    generation_segments,
    plan_decisions,
    production_plans,
    production_shots,
)
from creative_marketer.infrastructure.database.research_schema import evidence_snapshots, sources
from creative_marketer.knowledge.domain import (
    KnowledgeChange,
    KnowledgeGraph,
    KnowledgeNode,
    KnowledgeNodeRef,
    KnowledgeNodeType,
    KnowledgeRelationship,
)


def _json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json(child) for key, child in value.items()}
    if isinstance(value, list | tuple):
        return [_json(child) for child in value]
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return value


def _rel(
    node_type: KnowledgeNodeType, canonical_id: object, relationship: str
) -> KnowledgeRelationship:
    return KnowledgeRelationship(KnowledgeNodeRef(node_type, str(canonical_id)), relationship)


def _time(row: Mapping[str, Any], key: str, fallback: str = "created_at") -> datetime:
    return cast(datetime, row.get(key) or row[fallback])


def _finding_id(snapshot_id: object, key: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"creative-marketer:research-finding:{snapshot_id}:{key}"))


class SqlAlchemyCanonicalKnowledgeReader:
    """Cross-context read adapter. It never writes canonical context tables."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def graph(self, context: ExecutionContext) -> KnowledgeGraph:
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            tenant = context.tenant_id
            rows: dict[str, list[Mapping[str, Any]]] = {}
            statements = {
                "brands": select(brands).where(brands.c.tenant_id == tenant),
                "products": select(products).where(products.c.tenant_id == tenant),
                "product_snapshots": select(product_knowledge_snapshots).where(
                    product_knowledge_snapshots.c.tenant_id == tenant
                ),
                "assets": select(assets).where(assets.c.tenant_id == tenant),
                "definitions": select(agent_definitions).where(
                    or_(
                        agent_definitions.c.tenant_id == tenant,
                        agent_definitions.c.tenant_id.is_(None),
                    )
                ),
                "versions": select(agent_versions).where(
                    or_(agent_versions.c.tenant_id == tenant, agent_versions.c.tenant_id.is_(None))
                ),
                "activations": select(agent_activations).where(
                    or_(
                        agent_activations.c.tenant_id == tenant,
                        agent_activations.c.tenant_id.is_(None),
                    )
                ),
                "runs": select(agent_runs).where(agent_runs.c.tenant_id == tenant),
                "sources": select(sources).where(sources.c.tenant_id == tenant),
                "evidence": select(evidence_snapshots).where(
                    evidence_snapshots.c.tenant_id == tenant
                ),
                "research_snapshots": select(research_snapshots).where(
                    research_snapshots.c.tenant_id == tenant
                ),
                "concept_sets": select(concept_sets).where(concept_sets.c.tenant_id == tenant),
                "concepts": select(concepts).where(concepts.c.tenant_id == tenant),
                "decisions": select(concept_decisions).where(
                    concept_decisions.c.tenant_id == tenant
                ),
                "production_plans": select(production_plans).where(
                    production_plans.c.tenant_id == tenant
                ),
                "production_shots": select(production_shots).where(
                    production_shots.c.tenant_id == tenant
                ),
                "generation_segments": select(generation_segments).where(
                    generation_segments.c.tenant_id == tenant
                ),
                "plan_decisions": select(plan_decisions).where(
                    plan_decisions.c.tenant_id == tenant
                ),
                "generation_jobs": select(generation_jobs).where(
                    generation_jobs.c.tenant_id == tenant
                ),
                "asset_lineage": select(asset_lineage).where(asset_lineage.c.tenant_id == tenant),
            }
            for key, statement in statements.items():
                result = (await session.execute(statement)).mappings()
                rows[key] = [cast(Mapping[str, Any], row) for row in result]
        return self._build(rows)

    def _build(self, rows: Mapping[str, list[Mapping[str, Any]]]) -> KnowledgeGraph:
        nodes: list[KnowledgeNode] = []
        product_names = {row["id"]: row["name"] for row in rows["products"]}
        active_versions = {
            row["definition_id"]: row["active_version_id"] for row in rows["activations"]
        }
        decisions_by_concept: dict[object, list[Mapping[str, Any]]] = {}
        for row in rows["decisions"]:
            decisions_by_concept.setdefault(row["concept_id"], []).append(row)
        concept_sets_by_id = {row["id"]: row for row in rows["concept_sets"]}
        evidence_by_finding: dict[tuple[str, str], tuple[str, ...]] = {}
        for snapshot in rows["research_snapshots"]:
            for finding in snapshot["findings"] or []:
                evidence_by_finding[(str(snapshot["id"]), finding["key"])] = tuple(
                    citation["evidence_snapshot_id"] for citation in finding.get("citations", [])
                )

        for row in rows["brands"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.BRAND,
                    str(row["id"]),
                    row["name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {"slug": row["slug"], "website_url": row["website_url"]},
                )
            )
        decisions_by_plan: dict[object, list[Mapping[str, Any]]] = {}
        for decision in rows.get("plan_decisions", []):
            decisions_by_plan.setdefault(decision["production_plan_id"], []).append(decision)
        for row in rows.get("production_plans", []):
            latest = max(
                decisions_by_plan.get(row["id"], []),
                key=lambda item: item["created_at"],
                default=None,
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCTION_PLAN,
                    str(row["id"]),
                    f"Production Plan {str(row['id'])[:8]}",
                    latest["state"] if latest else "UNREVIEWED",
                    row["created_at"],
                    latest["created_at"] if latest else row["created_at"],
                    {
                        "strategy": row["strategy"],
                        "estimated_max_video_cost": str(row["estimated_max_video_cost"]),
                        "estimated_max_image_cost": str(row["estimated_max_image_cost"]),
                        "currency": row["currency"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.CREATIVE_CONCEPT,
                            row["concept_id"],
                            "planned_from_concept",
                        ),
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                        _rel(
                            KnowledgeNodeType.PRODUCT, row["product_id"], "production_for_product"
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        segments_by_plan: dict[object, list[Mapping[str, Any]]] = {}
        for segment in rows.get("generation_segments", []):
            segments_by_plan.setdefault(segment["production_plan_id"], []).append(segment)
            relationships = [
                _rel(
                    KnowledgeNodeType.PRODUCTION_PLAN,
                    segment["production_plan_id"],
                    "segment_of_plan",
                )
            ]
            relationships.extend(
                _rel(
                    KnowledgeNodeType.PRODUCTION_SHOT,
                    f"{segment['production_plan_id']}:{key}",
                    "executes_shot",
                )
                for key in segment["shot_keys"]
            )
            relationships.extend(
                _rel(
                    KnowledgeNodeType.ASSET,
                    value["asset_id"] if isinstance(value, dict) else value,
                    "uses_reference_asset",
                )
                for value in segment["reference_assets"]
                if (isinstance(value, str) or (isinstance(value, dict) and value.get("asset_id")))
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.GENERATION_SEGMENT,
                    str(segment["id"]),
                    segment["segment_key"],
                    "immutable",
                    segment["created_at"],
                    segment["created_at"],
                    {
                        "media_kind": segment["media_kind"],
                        "duration_seconds": segment["duration_seconds"],
                    },
                    tuple(relationships),
                    segment["generation_spec_digest"],
                )
            )
        for shot in rows.get("production_shots", []):
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCTION_SHOT,
                    f"{shot['production_plan_id']}:{shot['shot_key']}",
                    shot["shot_key"],
                    shot["source_strategy"],
                    shot["created_at"],
                    shot["created_at"],
                    {
                        "source_strategy": shot["source_strategy"],
                        "specification": shot["specification"],
                    },
                    (
                        _rel(
                            KnowledgeNodeType.PRODUCTION_PLAN,
                            shot["production_plan_id"],
                            "shot_of_plan",
                        ),
                    ),
                )
            )
        for job in rows.get("generation_jobs", []):
            relationships = [
                _rel(KnowledgeNodeType.PRODUCTION_PLAN, job["production_plan_id"], "job_for_plan"),
                _rel(
                    KnowledgeNodeType.GENERATION_SEGMENT,
                    job["generation_segment_id"],
                    "executes_segment",
                ),
            ]
            if job["output_asset_id"]:
                relationships.append(
                    _rel(KnowledgeNodeType.ASSET, job["output_asset_id"], "produced_asset")
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.GENERATION_JOB,
                    str(job["id"]),
                    f"{job['kind'].title()} Generation {str(job['id'])[:8]}",
                    job["status"],
                    job["created_at"],
                    job["updated_at"],
                    {
                        "kind": job["kind"],
                        "provider": job["provider"],
                        "model": job["model"],
                        "route_version": job["route_version"],
                        "pricing_version": job["pricing_version"],
                        "actual_cost": str(job["actual_cost"]),
                        "unknown_cost": str(job["unknown_cost"]),
                        "currency": job["currency"],
                    },
                    tuple(relationships),
                )
            )
        for row in rows["products"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCT,
                    str(row["id"]),
                    row["name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "category": row["category"],
                        "sku": row["sku"],
                        "description": row["short_description"],
                    },
                    (_rel(KnowledgeNodeType.BRAND, row["brand_id"], "belongs_to_brand"),),
                )
            )
        for row in rows["product_snapshots"]:
            product_name = product_names.get(row["product_id"], "Product")
            title = f"{product_name} Knowledge v{row['source_revision']}"
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    str(row["id"]),
                    title,
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {
                        "schema_version": row["schema_version"],
                        "source_revision": row["source_revision"],
                        "content": row["content"],
                    },
                    (_rel(KnowledgeNodeType.PRODUCT, row["product_id"], "snapshot_of"),),
                    row["digest"],
                )
            )
        for row in rows["assets"]:
            relationships = [_rel(KnowledgeNodeType.BRAND, row["brand_id"], "belongs_to_brand")]
            if row["product_id"]:
                relationships.append(
                    _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "belongs_to_product")
                )
            for lineage in rows.get("asset_lineage", []):
                if lineage["child_asset_id"] == row["id"]:
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.ASSET,
                            lineage["parent_asset_id"],
                            lineage["relationship_type"].casefold(),
                        )
                    )
            generating_job = next(
                (
                    job
                    for job in rows.get("generation_jobs", [])
                    if job["output_asset_id"] == row["id"]
                ),
                None,
            )
            if generating_job is not None:
                relationships.extend(
                    (
                        _rel(
                            KnowledgeNodeType.GENERATION_JOB,
                            generating_job["id"],
                            "generated_by_job",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCTION_PLAN,
                            generating_job["production_plan_id"],
                            "generated_for_plan",
                        ),
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.ASSET,
                    str(row["id"]),
                    row["original_filename"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "kind": row["kind"],
                        "role": row["role"],
                        "rights_status": row["rights_status"],
                        "allowed_uses": row["allowed_uses"],
                        "byte_size": row["byte_size"],
                        "digest": row["digest"],
                    },
                    tuple(relationships),
                    row["digest"],
                )
            )
        for row in rows["definitions"]:
            active_version = next(
                (
                    version
                    for version in rows["versions"]
                    if version["id"] == active_versions.get(row["id"])
                ),
                None,
            )
            relations = tuple(
                _rel(KnowledgeNodeType.AGENT_VERSION, version["id"], "has_version")
                for version in rows["versions"]
                if version["definition_id"] == row["id"]
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_DEFINITION,
                    str(row["id"]),
                    row["agent_key"].replace("_", " ").title(),
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "agent_key": row["agent_key"],
                        "agent_type": row["agent_type"],
                        "scope": row["scope_kind"],
                        "active_version_id": active_versions.get(row["id"]),
                        "mission": active_version["mission"] if active_version else None,
                        "version": active_version["version_number"] if active_version else None,
                        "model_profile": (
                            (active_version["model_policy"] or {}).get("profile_key")
                            if active_version
                            else None
                        ),
                        "inputs_consumed": (
                            active_version["read_scopes"] if active_version else []
                        ),
                        "outputs_produced": (
                            active_version["output_contract_key"] if active_version else None
                        ),
                        "downstream_capabilities": {
                            "researcher": ["creative_strategist"],
                            "creative_strategist": ["producer"],
                        }.get(row["agent_type"], []),
                    },
                    relations,
                )
            )
        for row in rows["versions"]:
            policy = row["model_policy"] or {}
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_VERSION,
                    str(row["id"]),
                    f"{row['display_name']} v{row['version_number']}",
                    "active"
                    if active_versions.get(row["definition_id"]) == row["id"]
                    else "historical",
                    row["created_at"],
                    row["created_at"],
                    {
                        "version": row["version_number"],
                        "mission": row["mission"],
                        "responsibilities": row["responsibilities"],
                        "model_profile": policy.get("profile_key"),
                        "required_capabilities": policy.get("required_capabilities", []),
                        "inputs_consumed": row["read_scopes"],
                        "outputs_produced": {
                            "contract": row["output_contract_key"],
                            "version": row["output_contract_version"],
                        },
                        "memory_scopes": row["memory_scopes"],
                        "downstream_capabilities": {
                            "researcher": ["creative_strategist"],
                            "creative_strategist": ["producer"],
                        }.get(
                            next(
                                (
                                    definition["agent_type"]
                                    for definition in rows["definitions"]
                                    if definition["id"] == row["definition_id"]
                                ),
                                "",
                            ),
                            [],
                        ),
                    },
                    (_rel(KnowledgeNodeType.AGENT_DEFINITION, row["definition_id"], "version_of"),),
                    row["configuration_digest"],
                )
            )
        for row in rows["runs"]:
            relationships = [
                _rel(KnowledgeNodeType.AGENT_VERSION, row["agent_version_id"], "executed_as"),
                _rel(
                    KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                    row["product_snapshot_id"],
                    "consumed_product_snapshot",
                ),
            ]
            for ref in row["input_context_refs"] or []:
                if ref.get("kind") == "research_snapshot" and ref.get("id"):
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.RESEARCH_SNAPSHOT,
                            ref["id"],
                            "consumed_research_snapshot",
                        )
                    )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.AGENT_RUN,
                    str(row["id"]),
                    f"{str(row['agent_type']).replace('_', ' ').title()} Run {str(row['id'])[:8]}",
                    row["status"],
                    row["created_at"],
                    _time(row, "completed_at"),
                    {
                        "agent_type": row["agent_type"],
                        "agent_version_number": row["agent_version_number"],
                        "model_profile": row["model_profile_key"],
                        "provider": row["resolved_provider"],
                        "model": row["resolved_model"],
                        "started_at": row["started_at"],
                        "completed_at": row["completed_at"],
                        "token_usage": {
                            "input": row["input_tokens"],
                            "output": row["output_tokens"],
                            "total": row["total_tokens"],
                        },
                        "estimated_cost": format(row["estimated_cost"], "f"),
                        "currency": row["currency"],
                        "inputs": row["input_context_refs"],
                        "result_ref": row["result_ref"],
                    },
                    tuple(relationships),
                )
            )
        for row in rows["sources"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.RESEARCH_SOURCE,
                    str(row["id"]),
                    row["display_name"],
                    row["status"],
                    row["created_at"],
                    row["updated_at"],
                    {
                        "category": row["category"],
                        "source_type": row["source_type"],
                        "url": row["canonical_url"],
                    },
                    (_rel(KnowledgeNodeType.PRODUCT, row["product_id"], "researches_product"),),
                )
            )
        for row in rows["evidence"]:
            title = row["title"] or f"Evidence {str(row['id'])[:8]}"
            blocks = [
                {
                    "kind": block.get("kind"),
                    "ordinal": block.get("ordinal"),
                    "text": block.get("text"),
                }
                for block in (row["content_blocks"] or [])
            ]
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.EVIDENCE_SNAPSHOT,
                    str(row["id"]),
                    title,
                    "immutable",
                    row["captured_at"],
                    row["captured_at"],
                    {
                        "url": row["final_url"],
                        "captured_at": row["captured_at"],
                        "extracted_text": blocks,
                        "instruction_like_content": row["instruction_like_content"],
                    },
                    (
                        _rel(KnowledgeNodeType.RESEARCH_SOURCE, row["source_id"], "evidence_from"),
                        _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "evidence_for_product"),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows["research_snapshots"]:
            findings = row["findings"] or []
            finding_rels = tuple(
                _rel(
                    KnowledgeNodeType.RESEARCH_FINDING,
                    _finding_id(row["id"], item["key"]),
                    "contains_finding",
                )
                for item in findings
            )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.RESEARCH_SNAPSHOT,
                    str(row["id"]),
                    f"Research Snapshot {str(row['id'])[:8]}",
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {
                        "schema_version": row["schema_version"],
                        "valid_until": row["valid_until"],
                        "research_gaps": row["research_gaps"],
                        "recommended_next_sources": row["recommended_next_sources"],
                    },
                    (
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            row["product_snapshot_id"],
                            "researches_snapshot",
                        ),
                        *finding_rels,
                    ),
                    row["semantic_digest"],
                )
            )
            for finding in findings:
                evidence_rels = tuple(
                    _rel(
                        KnowledgeNodeType.EVIDENCE_SNAPSHOT,
                        citation["evidence_snapshot_id"],
                        "supported_by_evidence",
                    )
                    for citation in finding.get("citations", [])
                )
                nodes.append(
                    KnowledgeNode(
                        KnowledgeNodeType.RESEARCH_FINDING,
                        _finding_id(row["id"], finding["key"]),
                        finding["statement"][:160],
                        "current",
                        row["created_at"],
                        row["created_at"],
                        {
                            "finding_key": finding["key"],
                            "category": finding["category"],
                            "statement": finding["statement"],
                            "confidence": finding["confidence"],
                            "scope": finding["scope"],
                            "implication": finding.get("implication"),
                            "citations": finding.get("citations", []),
                        },
                        (
                            _rel(KnowledgeNodeType.RESEARCH_SNAPSHOT, row["id"], "finding_of"),
                            *evidence_rels,
                        ),
                    )
                )
        for row in rows["concept_sets"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT_SET,
                    str(row["id"]),
                    f"Creative Concept Set {str(row['id'])[:8]}",
                    "immutable",
                    row["created_at"],
                    row["created_at"],
                    {"schema_version": row["schema_version"]},
                    (
                        _rel(KnowledgeNodeType.AGENT_RUN, row["agent_run_id"], "produced_by_run"),
                        _rel(
                            KnowledgeNodeType.RESEARCH_SNAPSHOT,
                            row["research_snapshot_id"],
                            "consumed_research_snapshot",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            row["product_snapshot_id"],
                            "consumed_product_snapshot",
                        ),
                    ),
                    row["semantic_digest"],
                )
            )
        for row in rows["concepts"]:
            payload = row["concept_payload"] or {}
            latest = max(
                decisions_by_concept.get(row["id"], []),
                key=lambda item: item["created_at"],
                default=None,
            )
            relationships = [
                _rel(
                    KnowledgeNodeType.CREATIVE_CONCEPT_SET,
                    row["concept_set_id"],
                    "belongs_to_concept_set",
                ),
                _rel(KnowledgeNodeType.PRODUCT, row["product_id"], "concept_for_product"),
            ]
            concept_set = concept_sets_by_id.get(row["concept_set_id"])
            if concept_set:
                relationships.extend(
                    (
                        _rel(
                            KnowledgeNodeType.AGENT_RUN,
                            concept_set["agent_run_id"],
                            "produced_by_run",
                        ),
                        _rel(
                            KnowledgeNodeType.RESEARCH_SNAPSHOT,
                            concept_set["research_snapshot_id"],
                            "uses_research_snapshot",
                        ),
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            concept_set["product_snapshot_id"],
                            "uses_product_snapshot",
                        ),
                    )
                )
            for ref in payload.get("supporting_research_refs", []):
                relationships.append(
                    _rel(
                        KnowledgeNodeType.RESEARCH_FINDING,
                        _finding_id(ref["research_snapshot_id"], ref["finding_key"]),
                        "supported_by_finding",
                    )
                )
                relationships.extend(
                    _rel(KnowledgeNodeType.EVIDENCE_SNAPSHOT, evidence_id, "supported_by_evidence")
                    for evidence_id in evidence_by_finding.get(
                        (str(ref["research_snapshot_id"]), ref["finding_key"]), ()
                    )
                )
            requirements = list(payload.get("required_assets", []))
            for scene in payload.get("scenes", []):
                requirements.extend(scene.get("asset_requirements", []))
            for requirement in requirements:
                if requirement.get("kind") == "EXISTING_ASSET" and requirement.get("asset_id"):
                    relationships.append(
                        _rel(KnowledgeNodeType.ASSET, requirement["asset_id"], "requires_asset")
                    )
            for message in payload.get("message_points", []):
                if message.get("product_claim_ref") and concept_set:
                    relationships.append(
                        _rel(
                            KnowledgeNodeType.PRODUCT_KNOWLEDGE_SNAPSHOT,
                            concept_set["product_snapshot_id"],
                            "claim_provenance",
                        )
                    )
            for decision in decisions_by_concept.get(row["id"], []):
                relationships.append(
                    _rel(
                        KnowledgeNodeType.CREATIVE_CONCEPT_DECISION, decision["id"], "has_decision"
                    )
                )
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT,
                    str(row["id"]),
                    payload.get("title") or row["concept_key"],
                    latest["state"] if latest else "UNREVIEWED",
                    row["created_at"],
                    latest["created_at"] if latest else row["created_at"],
                    payload,
                    tuple(relationships),
                    row["semantic_digest"],
                )
            )
        for row in rows["decisions"]:
            nodes.append(
                KnowledgeNode(
                    KnowledgeNodeType.CREATIVE_CONCEPT_DECISION,
                    str(row["id"]),
                    f"{row['state'].replace('_', ' ').title()} Decision",
                    row["state"],
                    row["created_at"],
                    row["created_at"],
                    {"reason_code": row["reason_code"], "note": row["note"]},
                    (_rel(KnowledgeNodeType.CREATIVE_CONCEPT, row["concept_id"], "decision_for"),),
                )
            )
        by_ref = {node.ref: node for node in nodes}
        reverse: dict[KnowledgeNodeRef, list[KnowledgeRelationship]] = {}
        for node in nodes:
            for relationship in node.relationships:
                if relationship.target in by_ref:
                    reverse.setdefault(relationship.target, []).append(
                        KnowledgeRelationship(node.ref, "linked_from")
                    )
        nodes = [
            replace(node, relationships=(*node.relationships, *reverse.get(node.ref, ())))
            for node in nodes
        ]
        nodes.sort(key=lambda node: (node.node_type.value, node.canonical_id))
        return KnowledgeGraph(tuple(nodes))


def _node_from_payload(payload: Mapping[str, Any]) -> KnowledgeNode:
    return KnowledgeNode(
        KnowledgeNodeType(payload["node_type"]),
        payload["canonical_id"],
        payload["title"],
        payload["status"],
        datetime.fromisoformat(payload["created_at"]),
        datetime.fromisoformat(payload["updated_at"]),
        payload.get("properties", {}),
        tuple(
            KnowledgeRelationship(
                KnowledgeNodeRef(
                    KnowledgeNodeType(item["target_node_type"]), item["target_canonical_id"]
                ),
                item["relationship_type"],
            )
            for item in payload.get("relationships", [])
        ),
        payload.get("semantic_digest"),
    )


class SqlAlchemyKnowledgeProjectionStore:
    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def refresh(self, context: ExecutionContext, graph: KnowledgeGraph) -> int:
        now = datetime.now(UTC)
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:tenant_id, 0))"),
                {"tenant_id": str(context.tenant_id)},
            )
            existing = {
                row["node_key"]: row
                for row in (
                    await session.execute(
                        select(projection_nodes)
                        .where(projection_nodes.c.tenant_id == context.tenant_id)
                        .with_for_update()
                    )
                ).mappings()
            }
            incoming = {f"{node.node_type.value}:{node.canonical_id}": node for node in graph.nodes}
            for key, node in incoming.items():
                payload = cast(dict[str, object], _json(node.primitive()))
                digest = node.projection_digest
                if key not in existing:
                    await session.execute(
                        insert(projection_nodes).values(
                            tenant_id=context.tenant_id,
                            node_key=key,
                            node_type=node.node_type.value,
                            canonical_id=node.canonical_id,
                            projection_digest=digest,
                            payload=payload,
                            updated_at=now,
                        )
                    )
                elif existing[key]["projection_digest"] != digest:
                    await session.execute(
                        update(projection_nodes)
                        .where(
                            projection_nodes.c.tenant_id == context.tenant_id,
                            projection_nodes.c.node_key == key,
                        )
                        .values(projection_digest=digest, payload=payload, updated_at=now)
                    )
                else:
                    continue
                await session.execute(
                    insert(projection_changes).values(
                        tenant_id=context.tenant_id,
                        node_key=key,
                        node_type=node.node_type.value,
                        canonical_id=node.canonical_id,
                        operation="upsert",
                        payload=payload,
                        created_at=now,
                    )
                )
            for key, row in existing.items():
                if key in incoming:
                    continue
                await session.execute(
                    delete(projection_nodes).where(
                        projection_nodes.c.tenant_id == context.tenant_id,
                        projection_nodes.c.node_key == key,
                    )
                )
                await session.execute(
                    insert(projection_changes).values(
                        tenant_id=context.tenant_id,
                        node_key=key,
                        node_type=row["node_type"],
                        canonical_id=row["canonical_id"],
                        operation="delete",
                        payload=None,
                        created_at=now,
                    )
                )
            latest = await session.scalar(
                select(func.coalesce(func.max(projection_changes.c.revision), 0)).where(
                    projection_changes.c.tenant_id == context.tenant_id
                )
            )
            return int(latest or 0)

    async def changes(
        self, context: ExecutionContext, *, after_revision: int, limit: int
    ) -> tuple[tuple[KnowledgeChange, ...], int]:
        async with self._factory() as session, session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(context.tenant_id)},
            )
            result = (
                await session.execute(
                    select(projection_changes)
                    .where(
                        projection_changes.c.tenant_id == context.tenant_id,
                        projection_changes.c.revision > after_revision,
                    )
                    .order_by(projection_changes.c.revision)
                    .limit(limit)
                )
            ).mappings()
            changes = tuple(
                KnowledgeChange(
                    int(row["revision"]),
                    _node_from_payload(row["payload"]) if row["payload"] else None,
                    None
                    if row["payload"]
                    else KnowledgeNodeRef(KnowledgeNodeType(row["node_type"]), row["canonical_id"]),
                )
                for row in result
            )
            latest = await session.scalar(
                select(func.coalesce(func.max(projection_changes.c.revision), 0)).where(
                    projection_changes.c.tenant_id == context.tenant_id
                )
            )
            return changes, int(latest or 0)
