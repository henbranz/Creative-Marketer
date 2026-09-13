"""Session-bound, operator-only live acceptance orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from creative_marketer.agent_runtime.application import (
    initial_creative_strategist_route,
    initial_researcher_route,
)
from creative_marketer.agent_runtime.domain import ModelRoute
from creative_marketer.production.application import (
    OPENAI_IMAGE_MODEL,
    SEEDANCE_MODEL,
    SeedancePricing,
    initial_producer_route,
)
from creative_marketer_api.config import REPOSITORY_ROOT, Settings

STATE_PATH = REPOSITORY_ROOT / ".creative-marketer" / "live-validation.json"


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
            raise RuntimeError(f"LOCAL_API_HTTP_{error.code}") from None
        except URLError:
            raise RuntimeError("LOCAL_API_UNREACHABLE") from None


@dataclass
class LiveState:
    version: int
    session_id: str
    tenant_id: str
    product_id: str
    researcher_run_id: str | None = None
    creative_run_id: str | None = None
    creative_concept_id: str | None = None
    producer_run_id: str | None = None
    production_plan_id: str | None = None
    image_job_ids: list[str] = field(default_factory=list)
    video_job_ids: list[str] = field(default_factory=list)
    assembly_plan_id: str | None = None
    final_creative_id: str | None = None


FIELDS = set(LiveState.__dataclass_fields__)
OPTIONAL_IDS = FIELDS - {
    "version",
    "session_id",
    "tenant_id",
    "product_id",
    "image_job_ids",
    "video_job_ids",
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
        if not isinstance(raw, dict) or set(raw) != FIELDS or raw.get("version") != 1:
            raise RuntimeError("LIVE_VALIDATION_STATE_INVALID_SCHEMA")
        for name in ("session_id", "tenant_id", "product_id", *OPTIONAL_IDS):
            if raw[name] is not None:
                raw[name] = valid_id(raw[name], name)
        for name in ("image_job_ids", "video_job_ids"):
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
        value = LiveState(1, str(uuid4()), str(UUID(api.tenant_id)), str(UUID(product)))
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
    if run.get("status") == "SUCCEEDED" and (
        run.get("resolved_provider") != "openai"
        or run.get("resolved_model") != route.model
        or run.get("model_route_version") != route.route_version
    ):
        raise RuntimeError("LIVE_SESSION_AGENT_RUN_ROUTE_MISMATCH")
    return run


def created_id(response: Any, label: str) -> str:
    if not isinstance(response, dict):
        raise RuntimeError(f"LIVE_{label.upper()}_CREATE_RESPONSE_INVALID")
    return valid_id(response.get("id"), f"{label}_id")


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
    run = exact_run(api, product, kind, identifier, route)
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


def openai_smoke(settings: Settings, store: StateStore | None = None) -> int:
    if settings.model_provider_backend != "openai":
        raise RuntimeError("LIVE_MODEL_PROVIDER_NOT_ENABLED")
    api = api_for(settings)
    product = str(settings.live_e2e_product_id)
    saved = store or StateStore()
    state = cast(LiveState, session(api, product, saved))
    api.request(f"/v1/products/{product}")
    stages: tuple[tuple[str, str, ModelRoute, str, str, dict[str, Any]], ...] = (
        (
            "researcher_run_id",
            "research",
            initial_researcher_route(),
            "Researcher",
            f"/v1/products/{product}/research/runs",
            {},
        ),
        (
            "creative_run_id",
            "creative",
            initial_creative_strategist_route(),
            "Creative Strategist",
            f"/v1/products/{product}/creative/runs",
            {"concept_count": 5, "channel_intent": "ORGANIC_SHORT_FORM"},
        ),
    )
    for field_name, kind, route, label, path, extra in stages:
        body = {"idempotency_key": f"live-{kind}-{state.session_id}", **extra}
        result = advance_run(api, product, state, saved, field_name, kind, route, label, path, body)
        if result:
            return result
    concept_sets = [
        s
        for s in items(api, f"/v1/products/{product}/creative/concept-sets")
        if s.get("agent_run_id") == state.creative_run_id
    ]
    concepts = [concept for value in concept_sets for concept in value.get("concepts", [])]
    if state.creative_concept_id is None:
        approved = next(
            (c for c in concepts if c.get("decision_state") == "APPROVED_FOR_PRODUCTION"), None
        )
        if approved is None:
            print("PAUSED: approve a Concept from this session's Creative Concept Set in the UI.")
            return 3
        state.creative_concept_id = valid_id(approved.get("id"), "creative_concept_id")
        saved.save(state)
    approved = exact(concepts, state.creative_concept_id, "creative_concept")
    if approved.get("decision_state") != "APPROVED_FOR_PRODUCTION":
        print("PAUSED: the session-bound Creative Concept is not approved for production.")
        return 3
    result = advance_run(
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
    if result:
        return result
    plan = bound_plan(api, product, state, saved)
    if plan is None:
        print("Production Plan is not materialized yet. Re-run after the worker completes.")
        return 3
    if plan.get("concept_id") != state.creative_concept_id:
        raise RuntimeError("LIVE_SESSION_PRODUCTION_PLAN_CONCEPT_MISMATCH")
    print(f"Production Plan bound to session: {plan['id']}")
    return 0


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
    print("Live E2E uses the existing governed APIs and workers.")
    saved = store or StateStore()
    result = openai_smoke(settings, saved)
    if result:
        return result
    for kind in ("IMAGE", "VIDEO"):
        result = media_smoke(settings, kind, saved)
        if result:
            return result
    api = api_for(settings)
    state = cast(LiveState, session(api, str(settings.live_e2e_product_id), saved))
    return assembly(api, state, saved)


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
    total = Decimal()
    for label, identifier in (
        ("Researcher", state.researcher_run_id),
        ("Creative Strategist", state.creative_run_id),
        ("Producer", state.producer_run_id),
    ):
        if identifier is None:
            print(f"{label}: NOT STARTED")
        else:
            run = cast(dict[str, Any], api.request(f"/v1/agent-runs/{identifier}"))
            print(f"{label}: {run.get('status', 'UNKNOWN')}")
            total += Decimal(str(run.get("estimated_cost", 0)))
    for label, identifiers in (("Image", state.image_job_ids), ("Video", state.video_job_ids)):
        values = [
            cast(dict[str, Any], api.request(f"/v1/production/jobs/{i}")) for i in identifiers
        ]
        stage_status = (
            ", ".join(str(v.get("status", "UNKNOWN")) for v in values) if values else "NOT STARTED"
        )
        print(f"{label}: {stage_status}")
        total += sum(
            (
                Decimal(str(v.get("reserved_cost", 0)))
                if Decimal(str(v.get("unknown_cost", 0))) > 0
                else Decimal(str(v.get("actual_cost", 0)))
                for v in values
            ),
            Decimal(),
        )
    if state.assembly_plan_id:
        value = cast(dict[str, Any], api.request(f"/v1/assembly/plans/{state.assembly_plan_id}"))
        print(f"Assembly: {value.get('job', {}).get('status', 'UNKNOWN')}")
    else:
        print("Assembly: NOT STARTED")
    print(f"Final Creative: {'BOUND' if state.final_creative_id else 'NOT STARTED'}")
    print(f"Session-bound live acceptance cost: {total} USD")
    return 0
