"""Session-bound, operator-only live acceptance orchestration."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.application import (
    initial_agent_model_routes,
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelRoute
from creative_marketer.creative.domain import ChannelIntent
from creative_marketer.production.application import (
    OPENAI_IMAGE_MODEL,
    PRODUCTION_CONTRACT_KEY,
    PRODUCTION_CONTRACT_VERSION,
    SEEDANCE_MODEL,
    SeedancePricing,
    initial_producer_route,
)
from creative_marketer_api.config import REPOSITORY_ROOT, Settings
from scripts.bootstrap_creative_strategist import creative_strategist_configuration
from scripts.bootstrap_producer import producer_configuration
from scripts.bootstrap_researcher import researcher_configuration

STATE_PATH = REPOSITORY_ROOT / ".creative-marketer" / "live-validation.json"
SAFE_HTTP_DETAIL_MAX_LENGTH = 128
SAFE_HTTP_BODY_MAX_BYTES = 4096
SAFE_HTTP_DETAIL = re.compile(r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+")


@dataclass(frozen=True)
class LocalApi:
    base_url: str
    tenant_id: str
    credential: str

    def request(self, path: str, *, method: str = "GET", body: object | None = None) -> Any:
        request = Request(
            self.base_url.rstrip("/") + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": f"Bearer {self.credential}",
                "X-Tenant-ID": self.tenant_id,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:
                return json.loads(response.read())
        except HTTPError as error:
            failure = f"LOCAL_API_HTTP_{error.code}"
            try:
                payload = error.read(SAFE_HTTP_BODY_MAX_BYTES + 1)
                if len(payload) <= SAFE_HTTP_BODY_MAX_BYTES:
                    value = json.loads(payload.decode("utf-8"))
                    detail = value.get("detail") if isinstance(value, dict) else None
                    if (
                        isinstance(detail, str)
                        and len(detail) <= SAFE_HTTP_DETAIL_MAX_LENGTH
                        and SAFE_HTTP_DETAIL.fullmatch(detail)
                        and (not self.credential or self.credential not in detail)
                    ):
                        failure = f"{failure}: {detail}"
            except (AttributeError, OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass
            raise RuntimeError(failure) from None
        except URLError:
            raise RuntimeError("LOCAL_API_UNREACHABLE") from None


@dataclass
class LiveState:
    version: int
    session_id: str
    tenant_id: str
    product_id: str
    researcher_run_id: str | None = None
    researcher_history_run_ids: list[str] = field(default_factory=list)
    creative_run_id: str | None = None
    creative_history_run_ids: list[str] = field(default_factory=list)
    creative_concept_id: str | None = None
    creative_revalidation_id: str | None = None
    producer_run_id: str | None = None
    production_plan_id: str | None = None
    image_job_ids: list[str] = field(default_factory=list)
    video_job_ids: list[str] = field(default_factory=list)
    assembly_plan_id: str | None = None
    final_creative_id: str | None = None


FIELDS = set(LiveState.__dataclass_fields__)
FIELDS_V1 = FIELDS - {
    "creative_history_run_ids",
    "researcher_history_run_ids",
    "creative_revalidation_id",
}
FIELDS_V2 = FIELDS - {"researcher_history_run_ids", "creative_revalidation_id"}
OPTIONAL_IDS = FIELDS - {
    "version",
    "session_id",
    "tenant_id",
    "product_id",
    "image_job_ids",
    "video_job_ids",
    "creative_history_run_ids",
    "researcher_history_run_ids",
}


def valid_id(value: object, name: str) -> str:
    try:
        return str(UUID(cast(str, value)))
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError(f"LIVE_VALIDATION_STATE_INVALID_{name.upper()}") from None


class StateStore:
    """Atomic checkpoint store; never an authority for product state."""

    def __init__(self, path: Path = STATE_PATH) -> None:
        self.path = path

    def load(self) -> LiveState | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise RuntimeError("LIVE_VALIDATION_STATE_INVALID") from None
        if not isinstance(raw, dict):
            raise RuntimeError("LIVE_VALIDATION_STATE_INVALID_SCHEMA")
        if set(raw) == FIELDS_V1 and raw.get("version") == 1:
            raw["version"] = 3
            raw["creative_history_run_ids"] = []
            raw["researcher_history_run_ids"] = []
            raw["creative_revalidation_id"] = None
        elif set(raw) == FIELDS_V2 and raw.get("version") in {1, 2}:
            raw["version"] = 3
            raw["researcher_history_run_ids"] = []
            raw["creative_revalidation_id"] = None
        elif set(raw) == FIELDS and raw.get("version") in {1, 2}:
            raw["version"] = 3
        elif set(raw) != FIELDS or raw.get("version") != 3:
            raise RuntimeError("LIVE_VALIDATION_STATE_INVALID_SCHEMA")
        for name in ("session_id", "tenant_id", "product_id", *OPTIONAL_IDS):
            if raw[name] is not None:
                raw[name] = valid_id(raw[name], name)
        for name in (
            "image_job_ids",
            "video_job_ids",
            "creative_history_run_ids",
            "researcher_history_run_ids",
        ):
            if not isinstance(raw[name], list):
                raise RuntimeError(f"LIVE_VALIDATION_STATE_INVALID_{name.upper()}")
            raw[name] = [valid_id(item, name) for item in raw[name]]
        return LiveState(**raw)

    def save(self, state: LiveState) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        try:
            temporary.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")
            temporary.chmod(0o600)
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise RuntimeError("LIVE_VALIDATION_STATE_WRITE_FAILED") from None

    def reset(self) -> bool:
        existed = self.path.exists() or self.path.is_symlink()
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise RuntimeError("LIVE_VALIDATION_STATE_RESET_FAILED") from None
        return existed


def api_for(settings: Settings, *, paid: bool = True) -> LocalApi:
    if paid:
        settings.require_live_spend_authorization()
    if settings.live_e2e_product_id is None:
        raise RuntimeError("LIVE_E2E_PRODUCT_ID is required")
    tenant = os.getenv("CM_TENANT_ID", "").strip()
    token = os.getenv("CM_API_TOKEN", "").strip()
    if not tenant or not token:
        raise RuntimeError("CM_TENANT_ID and CM_API_TOKEN are required")
    valid_id(tenant, "tenant_id")
    return LocalApi(os.getenv("CM_API_BASE_URL", "http://localhost:8000"), tenant, token)


def session(
    api: LocalApi, product: str, store: StateStore, *, create: bool = True
) -> LiveState | None:
    value = store.load()
    if value is None and create:
        value = LiveState(3, str(uuid4()), str(UUID(api.tenant_id)), str(UUID(product)))
        store.save(value)
        print(f"Live acceptance session started: {value.session_id}")
    if value is not None and (
        value.tenant_id != str(UUID(api.tenant_id)) or value.product_id != str(UUID(product))
    ):
        raise RuntimeError("LIVE_VALIDATION_SESSION_BINDING_MISMATCH; run make live-e2e-reset")
    return value


def items(api: LocalApi, path: str) -> list[dict[str, Any]]:
    return cast(list[dict[str, Any]], api.request(path))


def exact(values: list[dict[str, Any]], identifier: str, label: str) -> dict[str, Any]:
    value = next((item for item in values if item.get("id") == identifier), None)
    if value is None:
        raise RuntimeError(f"LIVE_SESSION_{label.upper()}_NOT_FOUND")
    return value


def exact_run(
    api: LocalApi, product: str, kind: str, identifier: str, route: ModelRoute
) -> dict[str, Any]:
    run = exact(items(api, f"/v1/products/{product}/{kind}/runs"), identifier, f"{kind}_run")
    validate_run_route(run, route)
    return run


def validate_run_route(run: dict[str, Any], route: ModelRoute) -> None:
    if (
        run.get("model_profile_key") is not None
        and run.get("model_profile_key") != route.profile_key
    ):
        raise RuntimeError("LIVE_SESSION_AGENT_RUN_ROUTE_MISMATCH")
    route_values = (
        run.get("resolved_provider"),
        run.get("resolved_model"),
        run.get("model_route_version"),
        run.get("pricing_version"),
    )
    if any(value is not None for value in route_values) and route_values != (
        route.provider,
        route.model,
        route.route_version,
        route.pricing.version,
    ):
        raise RuntimeError("LIVE_SESSION_AGENT_RUN_ROUTE_MISMATCH")


def frozen_route(run: dict[str, Any], fallback: ModelRoute) -> ModelRoute:
    """Resolve current or historical immutable route provenance."""

    profile_key = run.get("model_profile_key")
    if profile_key is None:
        validate_run_route(run, fallback)
        return fallback
    candidates = [
        route for route in initial_agent_model_routes() if route.profile_key == profile_key
    ]
    if len(candidates) != 1:
        raise RuntimeError("LIVE_SESSION_AGENT_RUN_ROUTE_MISMATCH")
    route = candidates[0]
    validate_run_route(run, route)
    if fallback.provider != route.provider or fallback.model != route.model:
        raise RuntimeError("LIVE_SESSION_AGENT_RUN_ROUTE_MISMATCH")
    return route


RECOVERY_PROVENANCE_FIELDS = (
    "product_id",
    "requested_agent_definition_id",
    "resolved_agent_definition_id",
    "agent_version_id",
    "agent_version_number",
    "agent_configuration_digest",
    "prompt_revision",
    "agent_type",
    "input_context_kind",
    "input_context_schema_version",
    "input_context_digest",
    "model_profile_key",
    "product_snapshot_id",
    "product_snapshot_digest",
    "research_context_digest",
    "context_digest",
)

PRODUCER_UPGRADE_LINEAGE_FIELDS = (
    "tenant_id",
    "product_id",
    "requested_agent_definition_id",
    "resolved_agent_definition_id",
    "agent_type",
    "input_context_kind",
    "input_context_schema_version",
    "model_profile_key",
    "output_contract_key",
    "product_snapshot_id",
    "product_snapshot_digest",
    "research_context_digest",
)
PRODUCER_SUCCESSOR_STATUSES = {
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "CANCELLED",
    "BLOCKED_BUDGET",
}


def validate_producer_upgrade_successor(
    predecessor: dict[str, Any],
    successor: dict[str, Any],
    *,
    require_pending: bool,
) -> str:
    """Validate the exposed immutable lineage of one Producer contract upgrade."""

    predecessor_id = valid_id(predecessor.get("id"), "producer_predecessor_run_id")
    successor_id = valid_id(successor.get("id"), "producer_replacement_run_id")
    predecessor_contract_version = predecessor.get("output_contract_version")
    successor_contract_version = successor.get("output_contract_version")
    if (
        successor_id == predecessor_id
        or successor.get("recovery_of_run_id") != predecessor_id
        or any(
            successor.get(field) != predecessor.get(field)
            for field in PRODUCER_UPGRADE_LINEAGE_FIELDS
        )
        or not isinstance(predecessor_contract_version, int)
        or not isinstance(successor_contract_version, int)
        or predecessor.get("output_contract_key") != PRODUCTION_CONTRACT_KEY
        or successor_contract_version <= predecessor_contract_version
        or successor_contract_version > PRODUCTION_CONTRACT_VERSION
        or successor.get("agent_version_id") == predecessor.get("agent_version_id")
        or successor.get("status") not in PRODUCER_SUCCESSOR_STATUSES
        or (require_pending and successor.get("status") != "PENDING")
    ):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_PROVENANCE_MISMATCH")
    return successor_id


def bind_recovery_successor(
    api: LocalApi,
    product: str,
    state: LiveState,
    store: StateStore,
    field_name: str,
    kind: str,
    route: ModelRoute,
    label: str,
) -> dict[str, Any]:
    """Adopt only the unique immutable successor of the exact session-bound run."""

    identifier = cast(str, getattr(state, field_name))
    values = items(api, f"/v1/products/{product}/{kind}/runs")
    predecessor = exact(values, identifier, f"{kind}_run")
    route = frozen_route(predecessor, route)
    successors = [value for value in values if value.get("recovery_of_run_id") == identifier]
    if not successors:
        validate_run_route(predecessor, route)
        return predecessor
    if len(successors) != 1:
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_AMBIGUOUS")
    successor = successors[0]
    if predecessor.get("tenant_id") != api.tenant_id or successor.get("tenant_id") != api.tenant_id:
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_TENANT_MISMATCH")
    if any(successor.get(field) != predecessor.get(field) for field in RECOVERY_PROVENANCE_FIELDS):
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_PROVENANCE_MISMATCH")
    if predecessor.get("product_id") != product:
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_PRODUCT_MISMATCH")
    # The endpoint is authenticated and RLS-scoped; revalidate its explicit tenant and all
    # exposed immutable provenance before changing the local checkpoint.
    predecessor_route = (
        predecessor.get("resolved_provider"),
        predecessor.get("resolved_model"),
        predecessor.get("model_route_version"),
        predecessor.get("pricing_version"),
    )
    expected_route = (route.provider, route.model, route.route_version, route.pricing.version)
    if predecessor_route != expected_route:
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_ROUTE_MISMATCH")
    successor_route = (
        successor.get("resolved_provider"),
        successor.get("resolved_model"),
        successor.get("model_route_version"),
        successor.get("pricing_version"),
    )
    if any(value is not None for value in successor_route) and successor_route != expected_route:
        raise RuntimeError("LIVE_SESSION_RECOVERY_SUCCESSOR_ROUTE_MISMATCH")
    successor_id = valid_id(successor.get("id"), "recovery_successor_id")
    setattr(state, field_name, successor_id)
    store.save(state)
    print(f"{label}: adopted recovery successor {successor_id} for {identifier}")
    return successor


def refresh_research(settings: Settings, store: StateStore | None = None) -> int:
    """Admit one governed Research refresh and preserve the previous successful run."""

    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved, create=False))
    if state is None or state.researcher_run_id is None:
        raise RuntimeError("LIVE_RESEARCH_REFRESH_STAGE_NOT_READY")
    prior = exact_run(api, product, "research", state.researcher_run_id, initial_researcher_route())
    if prior.get("status") in {"PENDING", "RUNNING"}:
        print(f"Research refresh already bound: {prior['id']} ({prior['status']})")
        return 0
    if (
        prior.get("status") != "SUCCEEDED"
        or prior.get("tenant_id") != state.tenant_id
        or prior.get("product_id") != state.product_id
    ):
        raise RuntimeError("LIVE_RESEARCH_REFRESH_REQUIRES_SUCCEEDED_RUN")
    prior_snapshots = [
        item
        for item in items(api, f"/v1/products/{product}/research/snapshots")
        if item.get("agent_run_id") == state.researcher_run_id
    ]
    if len(prior_snapshots) != 1:
        raise RuntimeError("LIVE_RESEARCH_REFRESH_SNAPSHOT_AMBIGUOUS")
    if prior_snapshots[0].get("freshness") == "current":
        raise RuntimeError("LIVE_RESEARCH_REFRESH_NOT_REQUIRED")
    created = api.request(
        f"/v1/products/{product}/research/runs",
        method="POST",
        body={
            "idempotency_key": (
                f"live-research-refresh-{state.session_id}-"
                f"{len(state.researcher_history_run_ids) + 1}"
            )
        },
    )
    refresh_id = created_id(created, "research_refresh_run")
    if (
        not isinstance(created, dict)
        or created.get("tenant_id") != state.tenant_id
        or created.get("product_id") != state.product_id
        or created.get("agent_type") != "researcher"
        or refresh_id == state.researcher_run_id
    ):
        raise RuntimeError("LIVE_RESEARCH_REFRESH_PROVENANCE_MISMATCH")
    historical = state.researcher_run_id
    state.researcher_history_run_ids.append(historical)
    state.researcher_run_id = refresh_id
    state.creative_revalidation_id = None
    saved.save(state)
    print(f"Research refresh requested: {refresh_id}")
    print(f"Historical successful Researcher retained: {historical}")
    print("Creative Strategist and Producer were not started by this command.")
    return 0


def revalidate_creative_concept(settings: Settings, store: StateStore | None = None) -> int:
    """Run the explicit deterministic revalidation transition; never call a provider."""

    api = api_for(settings, paid=False)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved, create=False))
    if state is None or state.researcher_run_id is None or state.creative_concept_id is None:
        raise RuntimeError("LIVE_CREATIVE_REVALIDATION_STAGE_NOT_READY")
    researcher = exact_run(
        api, product, "research", state.researcher_run_id, initial_researcher_route()
    )
    if researcher.get("status") != "SUCCEEDED":
        raise RuntimeError("LIVE_CREATIVE_REVALIDATION_RESEARCH_NOT_SUCCEEDED")
    current_snapshots = [
        item
        for item in items(api, f"/v1/products/{product}/research/snapshots")
        if item.get("agent_run_id") == state.researcher_run_id
        and item.get("freshness") == "current"
    ]
    if len(current_snapshots) != 1:
        raise RuntimeError("LIVE_CREATIVE_REVALIDATION_CURRENT_RESEARCH_MISSING")
    values = items(api, f"/v1/creative/concepts/{state.creative_concept_id}/revalidations")
    if state.creative_revalidation_id is not None:
        existing = exact(values, state.creative_revalidation_id, "creative_revalidation")
        print(f"Creative Concept: {existing['result']} ({existing['id']})")
        return 0 if existing.get("result") == "REVALIDATED_FOR_PRODUCTION" else 3
    result = api.request(
        f"/v1/creative/concepts/{state.creative_concept_id}/revalidate",
        method="POST",
    )
    if not isinstance(result, dict):
        raise RuntimeError("LIVE_CREATIVE_REVALIDATION_RESPONSE_INVALID")
    identifier = valid_id(result.get("id"), "creative_revalidation_id")
    if (
        result.get("concept_id") != state.creative_concept_id
        or result.get("product_id") != state.product_id
        or result.get("current_research_snapshot_id") != current_snapshots[0].get("id")
        or result.get("result") not in {"REVALIDATED_FOR_PRODUCTION", "REQUIRES_RESTRATEGY"}
    ):
        raise RuntimeError("LIVE_CREATIVE_REVALIDATION_PROVENANCE_MISMATCH")
    state.creative_revalidation_id = identifier
    saved.save(state)
    print(f"Creative Concept: {result['result']}")
    print(f"  original Research: {result['original_research_snapshot_id']}")
    print(f"  current Research authority: {result['current_research_snapshot_id']}")
    print("No Creative or Producer provider execution was started.")
    return 0 if result["result"] == "REVALIDATED_FOR_PRODUCTION" else 3


def _restrategy_request(
    api: LocalApi,
    product: str,
    historical_creative_run: dict[str, Any],
    historical_creative_run_id: str,
    concept_id: str,
) -> tuple[int, str]:
    """Recover the immutable request shape from the historical validated ConceptSet."""

    sets = [
        item
        for item in items(api, f"/v1/products/{product}/creative/concept-sets")
        if item.get("agent_run_id") == historical_creative_run_id
    ]
    if len(sets) != 1:
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_CONCEPT_SET_AMBIGUOUS")
    concept_set = sets[0]
    concepts = concept_set.get("concepts")
    concept_count = historical_creative_run.get("creative_concept_count")
    channel = historical_creative_run.get("creative_channel_intent")
    if (
        concept_set.get("product_id") != product
        or not isinstance(concepts, list)
        or not isinstance(concept_count, int)
        or isinstance(concept_count, bool)
        or not 3 <= concept_count <= 5
        or len(concepts) != concept_count
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_CONCEPT_SET_INVALID")
    selected = [
        item for item in concepts if isinstance(item, dict) and item.get("id") == concept_id
    ]
    if len(selected) != 1 or not isinstance(selected[0].get("payload"), dict):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_CONCEPT_PROVENANCE_MISMATCH")
    try:
        channel_intent = ChannelIntent(str(channel)).value
    except ValueError:
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_CHANNEL_INTENT_INVALID") from None
    return concept_count, channel_intent


def _validate_restrategy_run(
    run: dict[str, Any],
    *,
    state: LiveState,
    historical_creative_run_id: str,
    current_research_snapshot: dict[str, Any],
    concept_id: str,
    revalidation_id: str,
    concept_count: int,
    channel_intent: str,
    require_pending: bool,
) -> str:
    identifier = valid_id(run.get("id"), "creative_restrategy_run_id")
    allowed_statuses = {"PENDING"} if require_pending else {"PENDING", "RUNNING"}
    try:
        ChannelIntent(channel_intent)
    except ValueError:
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RUN_PROVENANCE_MISMATCH") from None
    if (
        not 3 <= concept_count <= 5
        or identifier == historical_creative_run_id
        or run.get("tenant_id") != state.tenant_id
        or run.get("product_id") != state.product_id
        or run.get("agent_type") != "creative_strategist"
        or run.get("status") not in allowed_statuses
        or run.get("recovery_of_run_id") is not None
        or run.get("creative_concept_count") != concept_count
        or run.get("creative_channel_intent") != channel_intent
        or run.get("restrategy_of_concept_id") != concept_id
        or run.get("restrategy_historical_creative_run_id") != historical_creative_run_id
        or run.get("restrategy_trigger_kind") != "creative_revalidation"
        or run.get("restrategy_trigger_id") != revalidation_id
        or run.get("restrategy_current_research_snapshot_id") != current_research_snapshot.get("id")
        or run.get("model_profile_key") != initial_creative_strategist_route().profile_key
        or run.get("research_context_digest")
        != current_research_snapshot.get("research_context_digest")
        or run.get("product_snapshot_id") != current_research_snapshot.get("product_snapshot_id")
        or run.get("product_snapshot_digest")
        != current_research_snapshot.get("product_snapshot_digest")
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RUN_PROVENANCE_MISMATCH")
    if require_pending and any(
        run.get(field) not in {None, 0, "0", "0.000000"}
        for field in (
            "resolved_provider",
            "resolved_model",
            "model_route_version",
            "pricing_version",
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "result_ref",
            "failure_code",
        )
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RUN_NOT_PENDING")
    return identifier


def _restrategy_key(state: LiveState, resolution: dict[str, Any]) -> str:
    material = ":".join(
        (
            state.session_id,
            valid_id(resolution.get("research_snapshot_id"), "research_snapshot_id"),
            valid_id(resolution.get("creative_revalidation_id"), "creative_revalidation_id"),
            valid_id(resolution.get("concept_id"), "concept_id"),
        )
    )
    return f"live-creative-restrategy-{sha256(material.encode()).hexdigest()[:48]}"


def restrategy_creative_concept(settings: Settings, store: StateStore | None = None) -> int:
    """Admit one normal Creative restrategy run; never execute a provider here."""

    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved, create=False))
    if state is None:
        raise RuntimeError("LIVE_ACCEPTANCE_SESSION_NOT_STARTED")
    initial_ready = all(
        value is not None
        for value in (
            state.researcher_run_id,
            state.creative_run_id,
            state.creative_concept_id,
            state.creative_revalidation_id,
            state.producer_run_id,
        )
    )
    # A bound pending restrategy is the one intentional exception: it has already
    # cleared Concept, revalidation, and active Producer checkpoints.
    bound_pending = bool(
        state.researcher_run_id is not None
        and state.creative_run_id is not None
        and state.creative_history_run_ids
        and state.creative_concept_id is None
        and state.creative_revalidation_id is None
        and state.producer_run_id is None
    )
    if not initial_ready and not bound_pending:
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_STAGE_NOT_READY")
    if (
        state.production_plan_id is not None
        or state.image_job_ids
        or state.video_job_ids
        or state.assembly_plan_id is not None
        or state.final_creative_id is not None
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_STAGE_ALREADY_ADVANCED")

    resolution = resolve_live_pipeline(api, product, state)
    action = resolution.get("next_action")
    if action == "WAIT_FOR_CREATIVE" and state.creative_history_run_ids:
        if (
            resolution.get("research_state") != "SUCCEEDED_CURRENT"
            or resolution.get("creative_state") not in {"PENDING", "RUNNING"}
            or resolution.get("producer_state") != "NOT_STARTED"
            or resolution.get("research_run_id") != state.researcher_run_id
            or resolution.get("creative_run_id") != state.creative_run_id
            or any(
                resolution.get(field) is not None
                for field in (
                    "concept_id",
                    "creative_revalidation_id",
                    "producer_run_id",
                    "production_plan_id",
                )
            )
        ):
            raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RESOLUTION_MISMATCH")
        snapshots = items(api, f"/v1/products/{product}/research/snapshots")
        snapshot = exact(
            snapshots,
            valid_id(resolution.get("research_snapshot_id"), "research_snapshot_id"),
            "research_snapshot",
        )
        creative_runs = items(api, f"/v1/products/{product}/creative/runs")
        active = [item for item in creative_runs if item.get("status") in {"PENDING", "RUNNING"}]
        if len(active) != 1 or active[0].get("id") != state.creative_run_id:
            raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_ACTIVE_RUN_AMBIGUOUS")
        active_concept_id = valid_id(
            active[0].get("restrategy_of_concept_id"), "restrategy_of_concept_id"
        )
        active_revalidation_id = valid_id(
            active[0].get("restrategy_trigger_id"), "restrategy_trigger_id"
        )
        active_count = active[0].get("creative_concept_count")
        active_channel = active[0].get("creative_channel_intent")
        if (
            not isinstance(active_count, int)
            or isinstance(active_count, bool)
            or not 3 <= active_count <= 5
            or not isinstance(active_channel, str)
        ):
            raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RUN_PROVENANCE_MISMATCH")
        _validate_restrategy_run(
            active[0],
            state=state,
            historical_creative_run_id=state.creative_history_run_ids[-1],
            current_research_snapshot=snapshot,
            concept_id=active_concept_id,
            revalidation_id=active_revalidation_id,
            concept_count=active_count,
            channel_intent=active_channel,
            require_pending=False,
        )
        print(f"Creative restrategy already bound: {state.creative_run_id} ({active[0]['status']})")
        print(f"Historical Creative retained: {state.creative_history_run_ids[-1]}")
        print("No provider execution was started by this command.")
        return 0
    if (
        action != "RESTRATEGIZE_CREATIVE"
        or resolution.get("current_stage") != "CREATIVE_RESTRATEGY"
        or resolution.get("blocking_reason") != "CURRENT_AUTHORITY_MATERIALLY_DIFFERS"
        or resolution.get("research_state") != "SUCCEEDED_CURRENT"
        or resolution.get("creative_state") != "REQUIRES_RESTRATEGY"
        or resolution.get("producer_state") != "NOT_STARTED"
        or resolution.get("research_run_id") != state.researcher_run_id
        or resolution.get("creative_run_id") != state.creative_run_id
        or resolution.get("concept_id") != state.creative_concept_id
        or resolution.get("creative_revalidation_id") != state.creative_revalidation_id
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_NOT_ELIGIBLE")

    research_run_id = cast(str, state.researcher_run_id)
    historical_creative_run_id = cast(str, state.creative_run_id)
    concept_id = cast(str, state.creative_concept_id)
    revalidation_id = cast(str, state.creative_revalidation_id)
    historical_producer_run_id = cast(str, state.producer_run_id)
    researcher = exact_run(api, product, "research", research_run_id, initial_researcher_route())
    if (
        researcher.get("status") != "SUCCEEDED"
        or researcher.get("tenant_id") != state.tenant_id
        or researcher.get("product_id") != state.product_id
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RESEARCH_MISMATCH")
    snapshots = items(api, f"/v1/products/{product}/research/snapshots")
    current_snapshot = exact(
        snapshots,
        valid_id(resolution.get("research_snapshot_id"), "research_snapshot_id"),
        "research_snapshot",
    )
    if (
        current_snapshot.get("agent_run_id") != research_run_id
        or current_snapshot.get("product_id") != state.product_id
        or str(current_snapshot.get("freshness", "")).lower() != "current"
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RESEARCH_SNAPSHOT_MISMATCH")

    creative_runs = items(api, f"/v1/products/{product}/creative/runs")
    historical_creative = exact(
        creative_runs, historical_creative_run_id, "historical_creative_run"
    )
    if (
        historical_creative.get("tenant_id") != state.tenant_id
        or historical_creative.get("product_id") != state.product_id
        or historical_creative.get("agent_type") != "creative_strategist"
        or historical_creative.get("status") != "SUCCEEDED"
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_HISTORICAL_RUN_MISMATCH")
    active = [item for item in creative_runs if item.get("status") in {"PENDING", "RUNNING"}]
    if len(active) > 1:
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_ACTIVE_RUN_AMBIGUOUS")

    concept_count, channel_intent = _restrategy_request(
        api, product, historical_creative, historical_creative_run_id, concept_id
    )
    revalidation = exact(
        items(api, f"/v1/creative/concepts/{concept_id}/revalidations"),
        revalidation_id,
        "creative_revalidation",
    )
    if (
        revalidation.get("product_id") != state.product_id
        or revalidation.get("concept_id") != concept_id
        or revalidation.get("current_research_snapshot_id") != current_snapshot.get("id")
        or revalidation.get("result") != "REQUIRES_RESTRATEGY"
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_REVALIDATION_MISMATCH")

    producer_runs = items(api, f"/v1/products/{product}/production/runs")
    historical_producer = exact(
        producer_runs, historical_producer_run_id, "historical_producer_run"
    )
    if (
        historical_producer.get("tenant_id") != state.tenant_id
        or historical_producer.get("product_id") != state.product_id
        or historical_producer.get("agent_type") != "producer"
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_PRODUCER_MISMATCH")

    adopted = bool(active)
    replacement = (
        active[0]
        if active
        else api.request(
            f"/v1/products/{product}/creative/runs",
            method="POST",
            body={
                "idempotency_key": _restrategy_key(state, resolution),
                "concept_count": concept_count,
                "channel_intent": channel_intent,
                "restrategy_of_concept_id": concept_id,
            },
        )
    )
    if not isinstance(replacement, dict):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_RESPONSE_INVALID")
    replacement_id = _validate_restrategy_run(
        replacement,
        state=state,
        historical_creative_run_id=historical_creative_run_id,
        current_research_snapshot=current_snapshot,
        concept_id=concept_id,
        revalidation_id=revalidation_id,
        concept_count=concept_count,
        channel_intent=channel_intent,
        require_pending=not adopted,
    )

    historical_creative_id = historical_creative_run_id
    historical_producer_id = historical_producer_run_id
    candidate = LiveState(**asdict(state))
    if historical_creative_id not in candidate.creative_history_run_ids:
        candidate.creative_history_run_ids.append(historical_creative_id)
    candidate.creative_run_id = replacement_id
    candidate.creative_concept_id = None
    candidate.creative_revalidation_id = None
    candidate.producer_run_id = None
    candidate.production_plan_id = None
    after = resolve_live_pipeline(api, product, candidate)
    if (
        after.get("current_stage") != "CREATIVE"
        or after.get("next_action") != "WAIT_FOR_CREATIVE"
        or after.get("research_state") != "SUCCEEDED_CURRENT"
        or after.get("creative_state") != replacement.get("status")
        or after.get("producer_state") != "NOT_STARTED"
        or after.get("research_run_id") != candidate.researcher_run_id
        or after.get("creative_run_id") != replacement_id
        or any(
            after.get(field) is not None
            for field in (
                "concept_id",
                "creative_revalidation_id",
                "producer_run_id",
                "production_plan_id",
            )
        )
    ):
        raise RuntimeError("LIVE_CREATIVE_RESTRATEGY_POST_ADMISSION_STATE_MISMATCH")
    saved.save(candidate)
    if adopted:
        print(f"Existing Creative restrategy adopted: {replacement_id}")
    print(f"Creative restrategy requested: {replacement_id}")
    print(f"Historical Creative retained: {historical_creative_id}")
    print(
        "Historical Producer lineage retained but unbound from active session: "
        f"{historical_producer_id}"
    )
    print("No provider execution was started by this command.")
    return 0


def replace_output_limited_creative(settings: Settings, store: StateStore | None = None) -> int:
    """Explicitly admit one fresh Creative run without replaying Researcher."""

    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved, create=False))
    if state is None:
        raise RuntimeError("LIVE_ACCEPTANCE_SESSION_NOT_STARTED")
    if state.researcher_run_id is None or state.creative_run_id is None:
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_STAGE_NOT_READY")
    if (
        any(
            value
            for value in (
                state.creative_concept_id,
                state.producer_run_id,
                state.production_plan_id,
                state.assembly_plan_id,
                state.final_creative_id,
            )
        )
        or state.image_job_ids
        or state.video_job_ids
    ):
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_STAGE_ALREADY_ADVANCED")
    researcher = exact_run(
        api, product, "research", state.researcher_run_id, initial_researcher_route()
    )
    if (
        researcher.get("status") != "SUCCEEDED"
        or researcher.get("tenant_id") != state.tenant_id
        or researcher.get("product_id") != state.product_id
    ):
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_RESEARCH_NOT_SUCCEEDED")
    if state.creative_history_run_ids:
        current = exact_run(
            api,
            product,
            "creative",
            state.creative_run_id,
            initial_creative_strategist_route(),
        )
        if (
            current.get("tenant_id") != state.tenant_id
            or current.get("product_id") != state.product_id
            or current.get("agent_type") != "creative_strategist"
            or current.get("recovery_of_run_id") is not None
        ):
            raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_PROVENANCE_MISMATCH")
        print(f"Creative replacement already bound: {current['id']} ({current['status']})")
        return 0
    failed = exact_run(
        api,
        product,
        "creative",
        state.creative_run_id,
        frozen_route(
            cast(
                dict[str, Any],
                api.request(f"/v1/agent-runs/{state.creative_run_id}"),
            ),
            initial_creative_strategist_route(),
        ),
    )
    if (
        failed.get("status") != "FAILED"
        or failed.get("tenant_id") != state.tenant_id
        or failed.get("product_id") != state.product_id
        or failed.get("agent_type") != "creative_strategist"
    ):
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_REQUIRES_FAILED_RUN")
    replacement = api.request(
        f"/v1/products/{product}/creative/runs/{state.creative_run_id}/replacement",
        method="POST",
        body={"transition_id": state.session_id},
    )
    if not isinstance(replacement, dict):
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_RESPONSE_INVALID")
    replacement_id = valid_id(replacement.get("id"), "creative_replacement_run_id")
    if (
        replacement_id == state.creative_run_id
        or replacement.get("tenant_id") != state.tenant_id
        or replacement.get("product_id") != state.product_id
        or replacement.get("agent_type") != "creative_strategist"
        or replacement.get("recovery_of_run_id") is not None
        or replacement.get("agent_version_id") == failed.get("agent_version_id")
        or replacement.get("model_profile_key") != initial_creative_strategist_route().profile_key
    ):
        raise RuntimeError("LIVE_CREATIVE_REPLACEMENT_PROVENANCE_MISMATCH")
    validate_run_route(replacement, initial_creative_strategist_route())
    state.creative_history_run_ids.append(state.creative_run_id)
    state.creative_run_id = replacement_id
    saved.save(state)
    print(f"Creative replacement requested: {replacement_id}")
    print(f"Historical failed Creative retained: {state.creative_history_run_ids[-1]}")
    print("Researcher was not rerun.")
    return 0


def replace_invalid_producer(settings: Settings, store: StateStore | None = None) -> int:
    """Explicitly admit one contract-upgrade successor; never execute it here."""

    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved, create=False))
    if state is None:
        raise RuntimeError("LIVE_ACCEPTANCE_SESSION_NOT_STARTED")
    if state.creative_concept_id is None or state.producer_run_id is None:
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_STAGE_NOT_READY")
    if (
        state.production_plan_id is not None
        or state.image_job_ids
        or state.video_job_ids
        or state.assembly_plan_id is not None
        or state.final_creative_id is not None
    ):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_STAGE_ALREADY_ADVANCED")
    if state.researcher_history_run_ids:
        if state.creative_revalidation_id is None:
            raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_REVALIDATION_REQUIRED")
        revalidation = exact(
            items(
                api,
                f"/v1/creative/concepts/{state.creative_concept_id}/revalidations",
            ),
            state.creative_revalidation_id,
            "creative_revalidation",
        )
        if revalidation.get("result") != "REVALIDATED_FOR_PRODUCTION":
            raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_RESTRATEGY_REQUIRED")
    runs = items(api, f"/v1/products/{product}/production/runs")
    failed = exact(runs, state.producer_run_id, "production_run")
    failed_contract_version = failed.get("output_contract_version")
    if (
        failed_contract_version == PRODUCTION_CONTRACT_VERSION
        and failed.get("recovery_of_run_id") is not None
    ):
        if (
            failed.get("tenant_id") != state.tenant_id
            or failed.get("product_id") != state.product_id
            or failed.get("agent_type") != "producer"
        ):
            raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_PROVENANCE_MISMATCH")
        predecessor = exact(
            runs,
            valid_id(failed.get("recovery_of_run_id"), "producer_predecessor_run_id"),
            "production_run",
        )
        validate_producer_upgrade_successor(predecessor, failed, require_pending=False)
        print(f"Producer replacement already bound: {failed['id']} ({failed['status']})")
        return 0
    failed_failure_code = failed.get("failure_code")
    if not isinstance(failed_contract_version, int):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_REQUIRES_ELIGIBLE_FAILED_RUN")
    eligible_contract_failure = (
        failed_contract_version == 1 and failed_failure_code == "MODEL_INVALID_OUTPUT"
    ) or (failed_contract_version >= 2 and failed_failure_code == "PRODUCTION_PLAN_INVALID")
    if (
        failed.get("status") != "FAILED"
        or not eligible_contract_failure
        or failed.get("tenant_id") != state.tenant_id
        or failed.get("product_id") != state.product_id
        or failed.get("agent_type") != "producer"
        or failed.get("output_contract_key") != PRODUCTION_CONTRACT_KEY
        or failed_contract_version >= PRODUCTION_CONTRACT_VERSION
        or Decimal(str(failed.get("unknown_cost", 0))) != 0
    ):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_REQUIRES_ELIGIBLE_FAILED_RUN")
    successors = [
        candidate
        for candidate in runs
        if candidate.get("recovery_of_run_id") == state.producer_run_id
    ]
    if len(successors) > 1:
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_SUCCESSOR_AMBIGUOUS")
    if successors:
        successor_id = validate_producer_upgrade_successor(
            failed, successors[0], require_pending=False
        )
        historical_id = state.producer_run_id
        state.producer_run_id = successor_id
        saved.save(state)
        print(f"Producer replacement already existed and was adopted: {successor_id}")
        print(f"Historical failed Producer retained: {historical_id}")
        print("No provider execution was started by this command.")
        return 0
    replacement = api.request(
        f"/v1/products/{product}/production/runs/{state.producer_run_id}/replacement",
        method="POST",
        body={"transition_id": state.session_id},
    )
    if not isinstance(replacement, dict):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_RESPONSE_INVALID")
    replacement_id = validate_producer_upgrade_successor(failed, replacement, require_pending=True)
    replacement_contract_version = replacement.get("output_contract_version")
    if (
        not isinstance(replacement_contract_version, int)
        or replacement_contract_version != PRODUCTION_CONTRACT_VERSION
    ):
        raise RuntimeError("LIVE_PRODUCER_REPLACEMENT_PROVENANCE_MISMATCH")
    historical_id = state.producer_run_id
    state.producer_run_id = replacement_id
    saved.save(state)
    print(f"Producer replacement requested and left PENDING: {replacement_id}")
    print(f"Historical failed Producer retained: {historical_id}")
    print("No provider execution was started by this command.")
    return 0


def created_id(response: Any, label: str) -> str:
    if not isinstance(response, dict):
        raise RuntimeError(f"LIVE_{label.upper()}_CREATE_RESPONSE_INVALID")
    return valid_id(response.get("id"), f"{label}_id")


def prepare_product_snapshot(api: LocalApi, product: str) -> dict[str, Any]:
    """Ensure the session Product has a snapshot at its current Brief revision."""

    product_id = valid_id(product, "product_id")
    tenant_id = valid_id(api.tenant_id, "tenant_id")
    workspace = api.request(f"/v1/products/{product_id}")
    if not isinstance(workspace, dict):
        raise RuntimeError("LIVE_PRODUCT_WORKSPACE_INVALID")
    workspace_product = workspace.get("product")
    brief = workspace.get("brief")
    latest = workspace.get("latest_snapshot")
    if not isinstance(workspace_product, dict) or not isinstance(brief, dict):
        raise RuntimeError("LIVE_PRODUCT_WORKSPACE_INVALID")
    if (
        valid_id(workspace_product.get("id"), "workspace_product_id") != product_id
        or valid_id(workspace_product.get("tenant_id"), "workspace_tenant_id") != tenant_id
    ):
        raise RuntimeError("LIVE_PRODUCT_WORKSPACE_BINDING_MISMATCH")
    revision = brief.get("revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise RuntimeError("LIVE_PRODUCT_BRIEF_REVISION_INVALID")
    if latest is not None and not isinstance(latest, dict):
        raise RuntimeError("LIVE_PRODUCT_SNAPSHOT_INVALID")
    snapshot = (
        api.request(f"/v1/products/{product_id}/snapshots", method="POST")
        if latest is None or latest.get("source_revision") != revision
        else latest
    )
    if not isinstance(snapshot, dict):
        raise RuntimeError("LIVE_PRODUCT_SNAPSHOT_INVALID")
    valid_id(snapshot.get("id"), "snapshot_id")
    if (
        valid_id(snapshot.get("product_id"), "snapshot_product_id") != product_id
        or snapshot.get("source_revision") != revision
    ):
        raise RuntimeError("LIVE_PRODUCT_SNAPSHOT_BINDING_MISMATCH")
    return snapshot


def report_run(label: str, run: dict[str, Any]) -> None:
    print(f"{label}: PASS")
    print(f"  model: {run['resolved_model']}")
    print(f"  route version: {run['model_route_version']}")
    usage = f"{run['input_tokens']} input / {run['output_tokens']} output"
    print(f"  usage: {usage} / {run['total_tokens']} total")
    print(f"  session-bound cost: {run['estimated_cost']} {run['currency']}")
    print("  schema validation: PASS")


def advance_run(
    api: LocalApi,
    product: str,
    state: LiveState,
    store: StateStore,
    field_name: str,
    kind: str,
    route: ModelRoute,
    label: str,
    path: str,
    body: dict[str, Any],
) -> int:
    identifier = cast(str | None, getattr(state, field_name))
    if identifier is None:
        setattr(
            state,
            field_name,
            created_id(api.request(path, method="POST", body=body), f"{kind}_run"),
        )
        store.save(state)
        print(
            f"{label} requested for session {state.session_id}. Re-run after the worker completes."
        )
        return 3
    run = bind_recovery_successor(api, product, state, store, field_name, kind, route, label)
    if run.get("status") == "FAILED":
        print(f"{label}: FAIL ({run.get('failure_code') or 'AGENT_RUN_FAILED'})")
        return 2
    if run.get("status") != "SUCCEEDED":
        print(f"{label} session run is {run.get('status')}. Re-run after the worker completes.")
        return 3
    report_run(label, run)
    return 0


def bound_plan(
    api: LocalApi, product: str, state: LiveState, store: StateStore
) -> dict[str, Any] | None:
    if state.producer_run_id is None:
        return None
    matching = [
        p
        for p in items(api, f"/v1/products/{product}/production/plans")
        if p.get("agent_run_id") == state.producer_run_id
    ]
    if state.production_plan_id is None:
        if not matching:
            return None
        if len(matching) != 1:
            raise RuntimeError("LIVE_SESSION_PRODUCTION_PLAN_AMBIGUOUS")
        state.production_plan_id = valid_id(matching[0].get("id"), "production_plan_id")
        store.save(state)
    plan = exact(matching, state.production_plan_id, "production_plan")
    if plan.get("agent_run_id") != state.producer_run_id:
        raise RuntimeError("LIVE_SESSION_PRODUCTION_PLAN_PROVENANCE_MISMATCH")
    return plan


def resolve_live_pipeline(api: LocalApi, product: str, state: LiveState | None) -> dict[str, Any]:
    value = api.request(
        f"/v1/products/{product}/pipeline/next-action",
        method="POST",
        body=_pipeline_locator_body(state),
    )
    if not isinstance(value, dict) or not isinstance(value.get("next_action"), str):
        raise RuntimeError("LIVE_PIPELINE_RESOLUTION_INVALID")
    return value


def _pipeline_locator_body(state: LiveState | None) -> dict[str, Any]:
    return {
        "research_run_id": state.researcher_run_id if state else None,
        "creative_run_id": state.creative_run_id if state else None,
        "concept_id": state.creative_concept_id if state else None,
        "creative_revalidation_id": state.creative_revalidation_id if state else None,
        "producer_run_id": state.producer_run_id if state else None,
        "production_plan_id": state.production_plan_id if state else None,
        "assembly_plan_id": state.assembly_plan_id if state else None,
        "final_creative_id": state.final_creative_id if state else None,
        # A checkpoint binds exact immutable lineage. Without one, inspect the
        # product's latest canonical state instead of reporting a false start.
        "use_latest_when_unbound": state is None,
    }


def _paid_action_exposure(action: str) -> tuple[str, str, Decimal, str]:
    if action in {"RUN_RESEARCH", "REFRESH_RESEARCH", "RERUN_RESEARCH"}:
        route = initial_researcher_route()
        budget = researcher_configuration().run_budget_policy
    elif action in {
        "RUN_CREATIVE",
        "RERUN_CREATIVE",
        "REPLACE_OUTPUT_LIMITED_CREATIVE",
        "RESTRATEGIZE_CREATIVE",
    }:
        route = initial_creative_strategist_route()
        budget = creative_strategist_configuration().run_budget_policy
    elif action in {"RUN_PRODUCER", "RERUN_PRODUCER", "UPGRADE_PRODUCER_CONTRACT"}:
        route = initial_producer_route()
        budget = producer_configuration().run_budget_policy
    else:
        raise RuntimeError("LIVE_PIPELINE_PAID_ACTION_CONFIGURATION_MISSING")
    return route.provider, route.model, budget.max_cost, budget.currency


def _bind_execution_locator(
    state: LiveState,
    locator: dict[str, Any],
    action: str,
) -> None:
    if valid_id(locator.get("product_id"), "product_id") != state.product_id:
        raise RuntimeError("LIVE_PIPELINE_EXECUTION_PROVENANCE_MISMATCH")
    values = {
        "researcher_run_id": locator.get("research_run_id"),
        "creative_run_id": locator.get("creative_run_id"),
        "creative_concept_id": locator.get("concept_id"),
        "creative_revalidation_id": locator.get("creative_revalidation_id"),
        "producer_run_id": locator.get("producer_run_id"),
        "production_plan_id": locator.get("production_plan_id"),
        "assembly_plan_id": locator.get("assembly_plan_id"),
        "final_creative_id": locator.get("final_creative_id"),
    }
    normalized = {
        field: valid_id(value, field) if value is not None else None
        for field, value in values.items()
    }
    new_research = normalized["researcher_run_id"]
    if state.researcher_run_id is not None and new_research != state.researcher_run_id:
        if action not in {"REFRESH_RESEARCH", "RERUN_RESEARCH"}:
            raise RuntimeError("LIVE_PIPELINE_EXECUTION_PROVENANCE_MISMATCH")
        if state.researcher_run_id not in state.researcher_history_run_ids:
            state.researcher_history_run_ids.append(state.researcher_run_id)
    new_creative = normalized["creative_run_id"]
    if state.creative_run_id is not None and new_creative != state.creative_run_id:
        if action not in {
            "RERUN_CREATIVE",
            "REPLACE_OUTPUT_LIMITED_CREATIVE",
            "RESTRATEGIZE_CREATIVE",
        }:
            raise RuntimeError("LIVE_PIPELINE_EXECUTION_PROVENANCE_MISMATCH")
        if state.creative_run_id not in state.creative_history_run_ids:
            state.creative_history_run_ids.append(state.creative_run_id)
    for field_name, value in normalized.items():
        setattr(state, field_name, value)


def live_next(
    settings: Settings,
    explicit_approval: str | None = None,
    store: StateStore | None = None,
) -> int:
    """Resolve and execute only the one authoritative normal pipeline action."""

    api = api_for(settings, paid=False)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved))
    resolution = resolve_live_pipeline(api, product, state)
    required = {
        "current_stage",
        "next_action",
        "execution_behavior",
        "canonical_operation",
        "api_boundary",
        "provider_cost",
        "human_approval_required",
        "provider_execution_permitted",
    }
    if not required.issubset(resolution):
        raise RuntimeError("LIVE_PIPELINE_RESOLUTION_INVALID")
    action = str(resolution["next_action"])
    behavior = str(resolution["execution_behavior"])
    print(f"Current stage: {resolution['current_stage']}")
    print(f"Next action: {action}")
    print(f"Behavior: {behavior}")
    print(f"Blocking reason: {resolution.get('blocking_reason') or 'none'}")
    print(f"Canonical operation: {resolution['canonical_operation']}")
    print(f"Action surface: {resolution['api_boundary']}")
    print(f"Provider cost: {'yes' if resolution['provider_cost'] else 'no'}")
    print(f"Human approval required: {'yes' if resolution['human_approval_required'] else 'no'}")
    print(
        "Provider execution permitted: "
        f"{'yes' if resolution['provider_execution_permitted'] else 'no'}"
    )

    checkpoint_changed = False
    for field_name, response_name in (
        ("assembly_plan_id", "assembly_plan_id"),
        ("final_creative_id", "final_creative_id"),
    ):
        identifier = resolution.get(response_name)
        current = getattr(state, field_name)
        if current is None and identifier is not None:
            setattr(state, field_name, valid_id(identifier, response_name))
            checkpoint_changed = True
        elif current is not None and identifier is not None and current != str(identifier):
            raise RuntimeError("LIVE_PIPELINE_RESOLUTION_PROVENANCE_MISMATCH")
    if checkpoint_changed:
        saved.save(state)

    if behavior != "EXECUTE":
        if explicit_approval is not None:
            raise RuntimeError("LIVE_PIPELINE_APPROVAL_NOT_APPLICABLE")
        print("No transition was executed.")
        return 0 if behavior == "TERMINAL" else 3

    if resolution["provider_cost"]:
        provider, model, maximum, currency = _paid_action_exposure(action)
        print(f"Provider/model: {provider} / {model}")
        print(f"Maximum reserved exposure: {maximum} {currency}")
        print("Unresolved provider operation: no (authoritative resolver admitted EXECUTE)")
        required_approval = f"I_APPROVE_{action}"
        if explicit_approval is None:
            print(f"Approval required: make live-next-approved APPROVAL={required_approval}")
            print("No AgentRun was admitted and no provider execution occurred.")
            return 3
        if explicit_approval != required_approval:
            raise RuntimeError("LIVE_PIPELINE_ACTION_APPROVAL_MISMATCH")
        settings.require_live_spend_authorization()
    elif explicit_approval is not None:
        raise RuntimeError("LIVE_PIPELINE_APPROVAL_NOT_APPLICABLE")

    body = _pipeline_locator_body(state)
    body.update(
        {
            "expected_action": action,
            "explicit_approval": explicit_approval,
        }
    )
    result = api.request(
        f"/v1/products/{product}/pipeline/execute-next",
        method="POST",
        body=body,
    )
    if (
        not isinstance(result, dict)
        or result.get("outcome") != "EXECUTED"
        or result.get("execution_behavior") != "EXECUTE"
        or result.get("provider_execution_occurred") is not False
        or not isinstance(result.get("locator"), dict)
        or not isinstance(result.get("before"), dict)
        or result["before"].get("next_action") != action
    ):
        raise RuntimeError("LIVE_PIPELINE_EXECUTION_RESPONSE_INVALID")
    _bind_execution_locator(state, result["locator"], action)
    saved.save(state)
    print(f"Transition admitted: {result.get('resource_type')} {result.get('resource_id')}")
    if resolution["provider_cost"]:
        print("A governed PENDING AgentRun was admitted; provider execution did not occur here.")
    else:
        print("The deterministic free transition completed; no provider execution occurred.")
    return 0


def openai_smoke(settings: Settings, store: StateStore | None = None) -> int:
    if settings.model_provider_backend != "openai":
        raise RuntimeError("LIVE_MODEL_PROVIDER_NOT_ENABLED")
    if settings.agent_workload_id == "local-fake-agent-worker":
        raise RuntimeError("LIVE_AGENT_WORKLOAD_IS_FAKE")
    if (
        settings.app_env in {"development", "test"}
        and settings.agent_workload_id != "local-live-agent-worker"
    ):
        raise RuntimeError("LIVE_AGENT_WORKLOAD_NOT_LIVE")
    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved))
    prepare_product_snapshot(api, product)
    resolution = resolve_live_pipeline(api, product, state)
    action = str(resolution["next_action"])

    # Bind only authority returned for the exact session locator. The API resolver,
    # not this dispatcher, decides which transition is valid.
    changed = False
    for field_name, response_name in (
        ("researcher_run_id", "research_run_id"),
        ("creative_run_id", "creative_run_id"),
        ("creative_concept_id", "concept_id"),
        ("creative_revalidation_id", "creative_revalidation_id"),
        ("producer_run_id", "producer_run_id"),
        ("production_plan_id", "production_plan_id"),
        ("assembly_plan_id", "assembly_plan_id"),
        ("final_creative_id", "final_creative_id"),
    ):
        identifier = resolution.get(response_name)
        current = getattr(state, field_name)
        if current is None and identifier is not None:
            setattr(state, field_name, valid_id(identifier, response_name))
            changed = True
        elif current is not None and identifier is not None and current != str(identifier):
            raise RuntimeError("LIVE_PIPELINE_RESOLUTION_PROVENANCE_MISMATCH")
    if changed:
        saved.save(state)

    if action == "RUN_RESEARCH":
        return advance_run(
            api,
            product,
            state,
            saved,
            "researcher_run_id",
            "research",
            initial_researcher_route(),
            "Researcher",
            f"/v1/products/{product}/research/runs",
            {"idempotency_key": f"live-research-{state.session_id}"},
        )
    if action == "WAIT_FOR_RESEARCH":
        return (
            advance_run(
                api,
                product,
                state,
                saved,
                "researcher_run_id",
                "research",
                initial_researcher_route(),
                "Researcher",
                f"/v1/products/{product}/research/runs",
                {},
            )
            or 3
        )
    if action == "RUN_CREATIVE":
        return advance_run(
            api,
            product,
            state,
            saved,
            "creative_run_id",
            "creative",
            initial_creative_strategist_route(),
            "Creative Strategist",
            f"/v1/products/{product}/creative/runs",
            {
                "idempotency_key": f"live-creative-{state.session_id}",
                "concept_count": 5,
                "channel_intent": "ORGANIC_SHORT_FORM",
            },
        )
    if action == "WAIT_FOR_CREATIVE":
        return (
            advance_run(
                api,
                product,
                state,
                saved,
                "creative_run_id",
                "creative",
                initial_creative_strategist_route(),
                "Creative Strategist",
                f"/v1/products/{product}/creative/runs",
                {},
            )
            or 3
        )
    if action == "RUN_PRODUCER":
        if state.creative_concept_id is None:
            raise RuntimeError("LIVE_PIPELINE_RESOLUTION_CONCEPT_MISSING")
        return advance_run(
            api,
            product,
            state,
            saved,
            "producer_run_id",
            "production",
            initial_producer_route(),
            "Producer",
            f"/v1/creative/concepts/{state.creative_concept_id}/production/runs",
            {"idempotency_key": f"live-production-{state.session_id}"},
        )
    if action == "WAIT_FOR_PRODUCER":
        return (
            advance_run(
                api,
                product,
                state,
                saved,
                "producer_run_id",
                "production",
                initial_producer_route(),
                "Producer",
                f"/v1/products/{product}/production/runs",
                {},
            )
            or 3
        )
    if action == "READY_FOR_GENERATION":
        print("Research, Creative approval, Producer, and Production Plan approval are complete.")
        print("Media generation was not started.")
        return 0

    print(f"PAUSED: authoritative next action is {action}.")
    print(f"Blocking reason: {resolution.get('blocking_reason') or 'none'}")
    print("No provider execution was started.")
    return 3


def bound_jobs(
    api: LocalApi, plan: dict[str, Any], state: LiveState, store: StateStore, kind: str
) -> list[dict[str, Any]]:
    jobs = items(api, f"/v1/production/plans/{plan['id']}/jobs")
    model, provider = (
        (OPENAI_IMAGE_MODEL, "openai") if kind == "IMAGE" else (SEEDANCE_MODEL, "byteplus")
    )
    candidates = [
        j
        for j in jobs
        if j.get("kind") == kind
        and j.get("model") == model
        and j.get("provider") == provider
        and j.get("local_demo_provider") is False
    ]
    field_name = "image_job_ids" if kind == "IMAGE" else "video_job_ids"
    identifiers = cast(list[str], getattr(state, field_name))
    if not identifiers:
        if not candidates:
            raise RuntimeError(f"LIVE_SESSION_{kind}_JOB_NOT_FOUND")
        identifiers.extend(valid_id(j.get("id"), f"{kind.lower()}_job_id") for j in candidates)
        store.save(state)
    return [exact(candidates, identifier, f"{kind.lower()}_job") for identifier in identifiers]


def media_smoke(settings: Settings, kind: str, store: StateStore | None = None) -> int:
    enabled = (
        settings.media_image_provider == "openai"
        if kind == "IMAGE"
        else settings.media_video_provider == "byteplus"
    )
    if not enabled:
        raise RuntimeError(f"LIVE_{kind}_PROVIDER_NOT_ENABLED")
    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved))
    if state.producer_run_id is None:
        print("PAUSED: complete live-openai-smoke for this session first.")
        return 3
    producer = exact_run(
        api, product, "production", state.producer_run_id, initial_producer_route()
    )
    if producer.get("status") != "SUCCEEDED":
        print("PAUSED: the session-bound Producer run has not succeeded.")
        return 3
    plan = bound_plan(api, product, state, saved)
    if plan is None:
        return 3
    if plan.get("status") == "UNREVIEWED":
        if kind == "VIDEO":
            preview = SeedancePricing().cost(output_duration_seconds=4, resolution="720p")
            print(f"Seedance 4s 720p no-input-video preview: {preview} USD")
        print("PAUSED: review and approve the exact session-bound Production Plan in the UI.")
        return 3
    selected = bound_jobs(api, plan, state, saved, kind)
    if any(j.get("status") == "FAILED" for j in selected):
        print(f"{kind.title()} FAIL: a session-bound GenerationJob failed")
        return 2
    if not all(j.get("status") == "SUCCEEDED" for j in selected):
        print(
            "Generation is governed and approved. Keep the Production worker active, then re-run."
        )
        return 3
    for job in selected:
        if not job.get("output_asset_id"):
            raise RuntimeError(f"LIVE_SESSION_{kind}_OUTPUT_ASSET_MISSING")
        if Decimal(str(job.get("unknown_cost", 0))) > 0:
            cost = f"reserved {job['reserved_cost']} {job['currency']} / actual unknown"
        else:
            cost = f"actual {job['actual_cost']} {job['currency']}"
        print(f"{kind.title()} PASS: {job['model']} / session-bound cost {cost}")
        print(f"  private Asset {job['output_asset_id']}")
    return 0


def assembly(api: LocalApi, state: LiveState, store: StateStore) -> int:
    plans = items(api, f"/v1/production/plans/{state.production_plan_id}/assembly-plans")
    if state.assembly_plan_id is None:
        readiness = api.request(
            f"/v1/production/plans/{state.production_plan_id}/assembly-readiness"
        )
        if not readiness["ready"]:
            print("PAUSED: bind any required manual source media in the UI.")
            return 3
        created = api.request(
            f"/v1/production/plans/{state.production_plan_id}/assembly-plans", method="POST"
        )
        state.assembly_plan_id = created_id(created, "assembly_plan")
        store.save(state)
        print("Final Assembly requested for this session. Re-run after the worker completes.")
        return 3
    value = exact(plans, state.assembly_plan_id, "assembly_plan")
    if value.get("production_plan_id") != state.production_plan_id:
        raise RuntimeError("LIVE_SESSION_ASSEMBLY_PLAN_PROVENANCE_MISMATCH")
    jobs = [
        cast(dict[str, Any], api.request(f"/v1/production/jobs/{identifier}"))
        for identifier in state.image_job_ids + state.video_job_ids
    ]
    live_assets = {j.get("output_asset_id") for j in jobs if j.get("output_asset_id")}
    if not live_assets.issubset({i.get("source_asset_id") for i in value.get("items", [])}):
        raise RuntimeError("LIVE_SESSION_ASSEMBLY_ASSET_PROVENANCE_MISMATCH")
    if value["job"]["status"] != "SUCCEEDED":
        print("Final Assembly is pending. Keep the Assembly worker active, then re-run.")
        return 3
    final_id = valid_id(value["job"].get("final_creative_id"), "final_creative_id")
    if state.final_creative_id is None:
        state.final_creative_id = final_id
        store.save(state)
    elif state.final_creative_id != final_id:
        raise RuntimeError("LIVE_SESSION_FINAL_CREATIVE_CHANGED")
    final = cast(dict[str, Any], api.request(f"/v1/final-creatives/{state.final_creative_id}"))
    if (
        final.get("assembly_plan_id") != state.assembly_plan_id
        or final.get("production_plan_id") != state.production_plan_id
        or final.get("creative_concept_id") != state.creative_concept_id
    ):
        raise RuntimeError("LIVE_SESSION_FINAL_CREATIVE_PROVENANCE_MISMATCH")
    if final.get("decision_state") != "APPROVED_FOR_PUBLISHING":
        print("PAUSED: play and approve this session's final MP4 in the UI.")
        return 3
    print(f"Final Creative PASS: {final['id']} / private Asset {final['output_asset_id']}")
    return 0


def e2e(settings: Settings, store: StateStore | None = None) -> int:
    """Inspect the authoritative Product-to-FinalCreative state without causing effects."""

    saved = store or StateStore()
    api = api_for(settings, paid=False)
    product = str(settings.live_e2e_product_id)
    state = session(api, product, saved, create=False)
    result = resolve_live_pipeline(api, product, state)
    if not isinstance(result, dict):
        raise RuntimeError("LIVE_PIPELINE_RESOLUTION_INVALID")
    required = {
        "current_stage",
        "next_action",
        "provider_cost",
        "human_approval_required",
        "provider_execution_permitted",
        "research_state",
        "creative_state",
        "producer_state",
    }
    if not required.issubset(result) or any(
        not isinstance(result[key], bool)
        for key in (
            "provider_cost",
            "human_approval_required",
            "provider_execution_permitted",
        )
    ):
        raise RuntimeError("LIVE_PIPELINE_RESOLUTION_INVALID")
    print(f"Current stage: {result['current_stage']}")
    print(f"Next action: {result['next_action']}")
    print(f"Blocking reason: {result.get('blocking_reason') or 'none'}")
    print(f"Provider cost: {'yes' if result['provider_cost'] else 'no'}")
    print(f"Human approval required: {'yes' if result['human_approval_required'] else 'no'}")
    print(
        f"Provider execution permitted: {'yes' if result['provider_execution_permitted'] else 'no'}"
    )
    print(
        "State: "
        f"Research={result['research_state']} / "
        f"Creative={result['creative_state']} / Producer={result['producer_state']}"
    )
    print("No provider, worker, generation, or database mutation was started.")
    return 0


def reset_session(store: StateStore | None = None) -> int:
    removed = (store or StateStore()).reset()
    status = "removed" if removed else "already absent"
    print(f"Live acceptance session state {status}.")
    print("Canonical database records were not changed.")
    return 0


def session_status(settings: Settings, store: StateStore | None = None) -> int:
    saved = store or StateStore()
    state = saved.load()
    if state is None:
        print("Live acceptance session: NOT STARTED")
        return 0
    api = api_for(settings, paid=False)
    session(api, str(settings.live_e2e_product_id), saved, create=False)
    print(f"Live acceptance session: {state.session_id}")
    print(f"Tenant: {state.tenant_id}")
    print(f"Product: {state.product_id}")
    actual = Decimal()
    reserved = Decimal()
    unknown = Decimal()
    for label, field_name, kind, route in (
        ("Researcher", "researcher_run_id", "research", initial_researcher_route()),
        (
            "Creative Strategist",
            "creative_run_id",
            "creative",
            initial_creative_strategist_route(),
        ),
        ("Producer", "producer_run_id", "production", initial_producer_route()),
    ):
        identifier = cast(str | None, getattr(state, field_name))
        if identifier is None:
            print(f"{label}: NOT STARTED")
        else:
            run = bind_recovery_successor(
                api, state.product_id, state, saved, field_name, kind, route, label
            )
            status = str(run.get("status", "UNKNOWN"))
            if run.get("operational_status") == "recovery_required":
                status += " / RECOVERY_REQUIRED"
            print(f"{label}: {status}")
            lineage = [run]
            seen = {str(run.get("id"))}
            pending_predecessors = [run.get("recovery_of_run_id")]
            if field_name == "researcher_run_id":
                pending_predecessors.extend(state.researcher_history_run_ids)
            if field_name == "creative_run_id":
                pending_predecessors.extend(state.creative_history_run_ids)
            while pending_predecessors:
                predecessor_ref = pending_predecessors.pop(0)
                if predecessor_ref is None:
                    continue
                predecessor_id = valid_id(predecessor_ref, "recovery_predecessor_id")
                if predecessor_id in seen or len(seen) >= 16:
                    raise RuntimeError("LIVE_SESSION_RECOVERY_LINEAGE_INVALID")
                seen.add(predecessor_id)
                predecessor = cast(dict[str, Any], api.request(f"/v1/agent-runs/{predecessor_id}"))
                if (
                    predecessor.get("tenant_id") != state.tenant_id
                    or predecessor.get("product_id") != state.product_id
                    or predecessor.get("agent_type") != run.get("agent_type")
                ):
                    raise RuntimeError("LIVE_SESSION_HISTORICAL_RUN_PROVENANCE_MISMATCH")
                lineage.append(predecessor)
                pending_predecessors.append(predecessor.get("recovery_of_run_id"))
            if field_name == "creative_run_id" and len(lineage) > 1:
                print(
                    "  historical Creative lineage: "
                    + ", ".join(str(value.get("id")) for value in lineage[1:])
                )
            if field_name == "researcher_run_id" and len(lineage) > 1:
                print(
                    "  historical Research lineage: "
                    + ", ".join(str(value.get("id")) for value in lineage[1:])
                )
            original_unknown = sum(
                (
                    Decimal(str(value.get("original_unknown_cost", value.get("unknown_cost", 0))))
                    for value in lineage
                ),
                Decimal(),
            )
            reconciled_actual = sum(
                (Decimal(str(value.get("reconciled_actual_cost", 0))) for value in lineage),
                Decimal(),
            )
            stage_unknown = sum(
                (
                    Decimal(str(value.get("remaining_unknown_cost", value.get("unknown_cost", 0))))
                    for value in lineage
                ),
                Decimal(),
            )
            if original_unknown:
                print(
                    f"  immutable original unknown cost: {original_unknown} "
                    f"{run.get('currency', 'USD')}"
                )
            if reconciled_actual or original_unknown != stage_unknown:
                print(f"  reconciled actual cost: {reconciled_actual} {run.get('currency', 'USD')}")
            if stage_unknown:
                print(
                    f"  remaining unknown potential cost: {stage_unknown} "
                    f"{run.get('currency', 'USD')}"
                )
            actual += (
                sum((Decimal(str(value.get("estimated_cost", 0))) for value in lineage), Decimal())
                + reconciled_actual
            )
            reserved += sum(
                (
                    Decimal(str(value.get("reserved_cost", 0)))
                    for value in lineage
                    if value.get("status") in {"PENDING", "RUNNING"}
                ),
                Decimal(),
            )
            unknown += stage_unknown
    if state.creative_revalidation_id is not None and state.creative_concept_id is not None:
        value = exact(
            items(
                api,
                f"/v1/creative/concepts/{state.creative_concept_id}/revalidations",
            ),
            state.creative_revalidation_id,
            "creative_revalidation",
        )
        print(f"Creative Concept: {value.get('result', 'UNKNOWN')}")
        print(f"  original Research: {value.get('original_research_snapshot_id')}")
        print(f"  current Research authority: {value.get('current_research_snapshot_id')}")
    elif state.creative_concept_id is not None:
        print("Creative Concept: NOT REVALIDATED")
    for label, identifiers in (("Image", state.image_job_ids), ("Video", state.video_job_ids)):
        values = [
            cast(dict[str, Any], api.request(f"/v1/production/jobs/{i}")) for i in identifiers
        ]
        stage_status = (
            ", ".join(str(v.get("status", "UNKNOWN")) for v in values) if values else "NOT STARTED"
        )
        print(f"{label}: {stage_status}")
        actual += sum((Decimal(str(v.get("actual_cost", 0))) for v in values), Decimal())
        reserved += sum(
            (
                Decimal(str(v.get("reserved_cost", 0)))
                for v in values
                if v.get("status") not in {"SUCCEEDED", "FAILED", "CANCELLED"}
            ),
            Decimal(),
        )
        unknown += sum((Decimal(str(v.get("unknown_cost", 0))) for v in values), Decimal())
    if state.assembly_plan_id:
        value = cast(dict[str, Any], api.request(f"/v1/assembly/plans/{state.assembly_plan_id}"))
        print(f"Assembly: {value.get('job', {}).get('status', 'UNKNOWN')}")
    else:
        print("Assembly: NOT STARTED")
    print(f"Final Creative: {'BOUND' if state.final_creative_id else 'NOT STARTED'}")
    print(f"Session-bound actual cost: {actual} USD")
    print(f"Session-bound reserved cost: {reserved} USD")
    print(f"Session-bound unknown potential cost: {unknown} USD")
    return 0
