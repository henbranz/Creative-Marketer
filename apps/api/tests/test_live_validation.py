# mypy: disable-error-code="arg-type,attr-defined,no-untyped-call,no-untyped-def,union-attr"

import json
from dataclasses import replace
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from typing import cast
from urllib.error import HTTPError
from uuid import UUID

import pytest

import scripts.live_acceptance as acceptance
import scripts.live_validation as live
from creative_marketer.agent_runtime.domain import AgentRunStatus
from creative_marketer_api.config import Settings
from creative_marketer_api.research_routes import _agent_run
from tests.test_agent_runtime_domain import run

DATABASE = "postgresql+psycopg://test:test@localhost:5432/test"


@pytest.mark.asyncio
async def test_provider_preflight_checks_models_without_generation(monkeypatch, capsys) -> None:
    secret = "unit-secret-sentinel-never-print"
    retrieve = []

    class Models:
        async def retrieve(self, model):
            retrieve.append(model)
            return SimpleNamespace(id=model)

    client = SimpleNamespace(models=Models())
    monkeypatch.setattr("scripts.live_validation.socket.getaddrinfo", lambda *_args: [(object(),)])
    settings = Settings(
        database_url=DATABASE,
        openai_api_key=secret,
        byteplus_las_api_key=secret,
    )
    assert await live.preflight(settings, client) == 0
    assert retrieve == ["gpt-5.6-sol", "gpt-image-2.5-sunburst-2026-09-08"]
    output = capsys.readouterr().out
    assert secret not in output
    assert "no inference or media-generation requests" in output


@pytest.mark.asyncio
async def test_live_cli_dispatches_explicit_creative_restrategy(monkeypatch) -> None:
    configured = settings()
    called = []

    def restrategy(value):
        called.append(value)
        return 0

    monkeypatch.setattr(live, "Settings", lambda: configured)
    monkeypatch.setattr(live.acceptance, "restrategy_creative_concept", restrategy)
    monkeypatch.setattr("sys.argv", ["live_validation", "creative-restrategy"])

    assert await live._main() == 0
    assert called == [configured]


def test_live_api_requires_acknowledgement_product_and_local_identity(monkeypatch) -> None:
    settings = Settings(database_url=DATABASE)
    with pytest.raises(RuntimeError, match="ALLOW_BILLABLE_MEDIA"):
        acceptance.api_for(settings)
    authorized = Settings(
        database_url=DATABASE,
        allow_billable_media=True,
        run_live_e2e=live.ACK,
    )
    with pytest.raises(RuntimeError, match="LIVE_E2E_PRODUCT_ID"):
        acceptance.api_for(authorized)
    configured = Settings(
        database_url=DATABASE,
        allow_billable_media=True,
        run_live_e2e=live.ACK,
        live_e2e_product_id="10000000-0000-0000-0000-000000000001",
    )
    monkeypatch.delenv("CM_TENANT_ID", raising=False)
    monkeypatch.delenv("CM_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="CM_TENANT_ID"):
        acceptance.api_for(configured)


TENANT = "10000000-0000-0000-0000-000000000001"
PRODUCT = "20000000-0000-0000-0000-000000000002"
RESEARCH = "30000000-0000-0000-0000-000000000003"
CREATIVE = "40000000-0000-0000-0000-000000000004"
CONCEPT = "50000000-0000-0000-0000-000000000005"
PRODUCER = "60000000-0000-0000-0000-000000000006"
PLAN = "70000000-0000-0000-0000-000000000007"
HISTORICAL = "80000000-0000-0000-0000-000000000008"
SNAPSHOT = "90000000-0000-0000-0000-000000000009"
SUCCESSOR = "a0000000-0000-0000-0000-00000000000a"
UNRELATED = "b0000000-0000-0000-0000-00000000000b"
REVALIDATION = "c0000000-0000-0000-0000-00000000000c"
PRODUCT_SNAPSHOT = "d0000000-0000-0000-0000-00000000000d"
NEW_CONCEPT = "e0000000-0000-0000-0000-00000000000e"
NEW_PRODUCER = "f0000000-0000-0000-0000-00000000000f"


class FakeApi:
    tenant_id = TENANT
    base_url = "http://local.invalid"
    credential = "not-printed"

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, path, *, method="GET", body=None):
        self.calls.append((method, path, body))
        value = self.responses[(method, path)]
        return value() if callable(value) else value


def settings(**values) -> Settings:
    return Settings(
        database_url=DATABASE,
        model_provider_backend="openai",
        allow_billable_media=True,
        run_live_e2e=live.ACK,
        live_e2e_product_id=PRODUCT,
        **values,
    )


def succeeded(identifier, route):
    return {
        "id": identifier,
        "tenant_id": TENANT,
        "product_id": PRODUCT,
        "status": "SUCCEEDED",
        "resolved_provider": "openai",
        "resolved_model": route.model,
        "model_route_version": route.route_version,
        "pricing_version": route.pricing.version,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
        "estimated_cost": "0.01",
        "currency": "USD",
    }


def recoverable_run(identifier, route, **changes):
    value = {
        **succeeded(identifier, route),
        "product_id": PRODUCT,
        "requested_agent_definition_id": str(UUID(int=21)),
        "resolved_agent_definition_id": str(UUID(int=22)),
        "agent_version_id": str(UUID(int=23)),
        "agent_version_number": 1,
        "agent_configuration_digest": "sha256:" + "a" * 64,
        "prompt_revision": "creative-v1",
        "agent_type": "creative_strategist",
        "input_context_kind": "creative_strategy.v1",
        "input_context_schema_version": 1,
        "input_context_digest": "sha256:" + "b" * 64,
        "model_profile_key": route.profile_key,
        "product_snapshot_id": SNAPSHOT,
        "product_snapshot_digest": "sha256:" + "c" * 64,
        "research_context_digest": "sha256:" + "d" * 64,
        "context_digest": "sha256:" + "e" * 64,
        "pricing_version": route.pricing.version,
        "recovery_of_run_id": None,
        "operational_status": "normal",
        "reserved_cost": "0.256000",
        "unknown_cost": "0",
    }
    value.update(changes)
    return value


def producer_run(
    identifier: str,
    *,
    contract_version: int,
    status: AgentRunStatus,
    failure_code: str | None,
    recovery_of_run_id: str | None = None,
    agent_version: int = 23,
) -> dict[str, object]:
    value = replace(
        run(),
        id=UUID(identifier),
        tenant_id=UUID(TENANT),
        product_id=UUID(PRODUCT),
        requested_agent_definition_id=UUID(int=31),
        resolved_agent_definition_id=UUID(int=32),
        product_snapshot_id=UUID(int=33),
        agent_type="producer",
        input_context_kind="production_planning.v2",
        selected_evidence=(),
        model_profile_key="production_deep",
        output_contract_key="production.production_plan",
        output_contract_version=contract_version,
        status=status,
        failure_code=failure_code,
        recovery_of_run_id=(None if recovery_of_run_id is None else UUID(recovery_of_run_id)),
        estimated_cost=Decimal("0.119128") if status is AgentRunStatus.FAILED else Decimal("0"),
        unknown_cost=Decimal("0"),
        agent_version_id=UUID(int=agent_version),
        resolved_provider=None if status is AgentRunStatus.PENDING else "openai",
        resolved_model=None if status is AgentRunStatus.PENDING else "gpt-5.6-sol",
        resolved_model_route_version=(
            None if status is AgentRunStatus.PENDING else "producer-route"
        ),
        pricing_version=None if status is AgentRunStatus.PENDING else "producer-pricing",
    )
    return cast(dict[str, object], _agent_run(value).model_dump(mode="json"))


def test_agent_run_response_exposes_only_bounded_creative_restrategy_metadata() -> None:
    value = replace(
        run(),
        agent_type="creative_strategist",
        input_context_kind="creative_strategy.v1",
        input_context_refs=(
            {
                "kind": "strategy_request",
                "concept_count": 3,
                "channel_intent": "TIKTOK",
            },
            {
                "kind": "creative_restrategy",
                "concept_id": CONCEPT,
                "historical_creative_run_id": CREATIVE,
                "trigger_kind": "creative_revalidation",
                "trigger_id": REVALIDATION,
                "current_research_snapshot_id": SNAPSHOT,
                "concept_digest": "sha256:" + "1" * 64,
                "trigger_digest": "sha256:" + "2" * 64,
            },
        ),
    )

    response = _agent_run(value).model_dump(mode="json")

    assert response["creative_concept_count"] == 3
    assert response["creative_channel_intent"] == "TIKTOK"
    assert response["restrategy_of_concept_id"] == CONCEPT
    assert response["restrategy_historical_creative_run_id"] == CREATIVE
    assert response["restrategy_trigger_kind"] == "creative_revalidation"
    assert response["restrategy_trigger_id"] == REVALIDATION
    assert response["restrategy_current_research_snapshot_id"] == SNAPSHOT
    assert "concept_digest" not in response
    assert "trigger_digest" not in response
    assert "input_context_refs" not in response


def saved_state(store, **values):
    state = acceptance.LiveState(1, str(UUID(int=99)), TENANT, PRODUCT, **values)
    store.save(state)
    return state


def workspace(
    *,
    snapshot_revision=2,
    product_id=PRODUCT,
    tenant_id=TENANT,
    brief_revision=2,
):
    snapshot = (
        None
        if snapshot_revision is None
        else {
            "id": SNAPSHOT,
            "product_id": product_id,
            "source_revision": snapshot_revision,
        }
    )
    return {
        "product": {"id": product_id, "tenant_id": tenant_id},
        "brief": {"revision": brief_revision},
        "latest_snapshot": snapshot,
    }


def created_snapshot(*, product_id=PRODUCT, source_revision=2):
    return {
        "id": SNAPSHOT,
        "product_id": product_id,
        "source_revision": source_revision,
    }


def http_error(body: bytes, code: int = 409, headers=None) -> HTTPError:
    return HTTPError("http://local.invalid", code, "failure", headers or {}, BytesIO(body))


def test_missing_snapshot_is_created_before_new_researcher_run(monkeypatch, tmp_path) -> None:
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(snapshot_revision=None),
            ("POST", f"/v1/products/{PRODUCT}/snapshots"): created_snapshot(),
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): {
                "next_action": "RUN_RESEARCH",
                "blocking_reason": None,
            },
            ("POST", f"/v1/products/{PRODUCT}/research/runs"): {"id": RESEARCH},
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)
    store = acceptance.StateStore(tmp_path / "live-validation.json")

    assert acceptance.openai_smoke(settings(), store) == 3
    assert store.load().researcher_run_id == RESEARCH
    assert fake.calls == [
        ("GET", f"/v1/products/{PRODUCT}", None),
        ("POST", f"/v1/products/{PRODUCT}/snapshots", None),
        (
            "POST",
            f"/v1/products/{PRODUCT}/pipeline/next-action",
            {
                "research_run_id": None,
                "creative_run_id": None,
                "concept_id": None,
                "creative_revalidation_id": None,
                "producer_run_id": None,
                "production_plan_id": None,
                "use_latest_when_unbound": False,
            },
        ),
        (
            "POST",
            f"/v1/products/{PRODUCT}/research/runs",
            {"idempotency_key": f"live-research-{store.load().session_id}"},
        ),
    ]


def test_current_snapshot_is_reused_without_duplicate_creation() -> None:
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}"): workspace()})

    assert acceptance.prepare_product_snapshot(fake, PRODUCT)["id"] == SNAPSHOT
    assert fake.calls == [("GET", f"/v1/products/{PRODUCT}", None)]


def test_stale_snapshot_is_replaced_through_canonical_api() -> None:
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(snapshot_revision=1),
            ("POST", f"/v1/products/{PRODUCT}/snapshots"): created_snapshot(),
        }
    )

    assert acceptance.prepare_product_snapshot(fake, PRODUCT)["source_revision"] == 2
    assert fake.calls == [
        ("GET", f"/v1/products/{PRODUCT}", None),
        ("POST", f"/v1/products/{PRODUCT}/snapshots", None),
    ]


@pytest.mark.parametrize(
    "workspace_value",
    [
        workspace(product_id=str(UUID(int=222))),
        workspace(tenant_id=str(UUID(int=333))),
    ],
)
def test_snapshot_preparation_fails_closed_for_wrong_product_or_tenant(workspace_value) -> None:
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}"): workspace_value})

    with pytest.raises(RuntimeError, match="WORKSPACE_BINDING_MISMATCH"):
        acceptance.prepare_product_snapshot(fake, PRODUCT)
    assert fake.calls == [("GET", f"/v1/products/{PRODUCT}", None)]


def test_snapshot_preparation_rejects_created_snapshot_for_wrong_product() -> None:
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(snapshot_revision=None),
            ("POST", f"/v1/products/{PRODUCT}/snapshots"): created_snapshot(
                product_id=str(UUID(int=444))
            ),
        }
    )

    with pytest.raises(RuntimeError, match="SNAPSHOT_BINDING_MISMATCH"):
        acceptance.prepare_product_snapshot(fake, PRODUCT)
    assert all("/runs" not in path for _method, path, _body in fake.calls)


def test_snapshot_creation_has_no_provider_or_agent_run_call() -> None:
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(snapshot_revision=None),
            ("POST", f"/v1/products/{PRODUCT}/snapshots"): created_snapshot(),
        }
    )

    acceptance.prepare_product_snapshot(fake, PRODUCT)

    assert all("/runs" not in path for _method, path, _body in fake.calls)


def test_local_api_surfaces_bounded_safe_http_detail(monkeypatch) -> None:
    header_secret = "header-secret-never-print"

    def fail(*_args, **_kwargs):
        raise http_error(
            json.dumps({"detail": "agent_run_not_ready"}).encode(),
            headers={"Set-Cookie": header_secret, "Authorization": header_secret},
        )

    monkeypatch.setattr(acceptance, "urlopen", fail)
    api = acceptance.LocalApi("http://local.invalid", TENANT, "credential_never_print")

    with pytest.raises(RuntimeError) as caught:
        api.request("/v1/failure")
    assert str(caught.value) == "LOCAL_API_HTTP_409: agent_run_not_ready"
    assert header_secret not in str(caught.value)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json secret_never_print",
        json.dumps({"detail": ["secret_never_print"]}).encode(),
        json.dumps({"detail": {"message": "secret_never_print"}}).encode(),
        json.dumps({"detail": "arbitrary response secret_never_print"}).encode(),
        json.dumps({"detail": "secret_never_print"}).encode(),
        json.dumps({"detail": "a_" + ("x" * 200)}).encode(),
    ],
)
def test_local_api_never_leaks_malformed_arbitrary_or_secret_detail(monkeypatch, body) -> None:
    def fail(*_args, **_kwargs):
        raise http_error(body)

    monkeypatch.setattr(acceptance, "urlopen", fail)
    api = acceptance.LocalApi("http://local.invalid", TENANT, "secret_never_print")

    with pytest.raises(RuntimeError) as caught:
        api.request("/v1/failure")
    assert str(caught.value) == "LOCAL_API_HTTP_409"
    assert "secret_never_print" not in str(caught.value)


def test_openai_smoke_fails_before_api_when_live_model_provider_is_disabled(
    monkeypatch, tmp_path
) -> None:
    called = False

    def unexpected_api(_settings):
        nonlocal called
        called = True

    monkeypatch.setattr(acceptance, "api_for", unexpected_api)
    disabled = Settings(
        database_url=DATABASE,
        allow_billable_media=True,
        run_live_e2e=live.ACK,
        live_e2e_product_id=PRODUCT,
    )

    with pytest.raises(RuntimeError, match="LIVE_MODEL_PROVIDER_NOT_ENABLED"):
        acceptance.openai_smoke(disabled, acceptance.StateStore(tmp_path / "live-validation.json"))
    assert called is False


def test_openai_smoke_rejects_known_fake_workload_before_api(monkeypatch, tmp_path) -> None:
    called = False

    def unexpected_api(_settings):
        nonlocal called
        called = True

    monkeypatch.setattr(acceptance, "api_for", unexpected_api)
    configured = settings(agent_workload_id="local-fake-agent-worker")

    with pytest.raises(RuntimeError, match="LIVE_AGENT_WORKLOAD_IS_FAKE"):
        acceptance.openai_smoke(
            configured, acceptance.StateStore(tmp_path / "live-validation.json")
        )
    assert called is False


def test_openai_smoke_rejects_legacy_local_workload_before_api(monkeypatch, tmp_path) -> None:
    called = False

    def unexpected_api(_settings):
        nonlocal called
        called = True

    monkeypatch.setattr(acceptance, "api_for", unexpected_api)
    configured = settings(agent_workload_id="local-agent-worker")

    with pytest.raises(RuntimeError, match="LIVE_AGENT_WORKLOAD_NOT_LIVE"):
        acceptance.openai_smoke(
            configured, acceptance.StateStore(tmp_path / "live-validation.json")
        )
    assert called is False


def test_session_tenant_or_product_mismatch_requires_explicit_reset(tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(store)
    other_tenant = acceptance.LocalApi("http://local.invalid", str(UUID(int=123)), "not-printed")

    with pytest.raises(RuntimeError, match="SESSION_BINDING_MISMATCH"):
        acceptance.session(other_tenant, PRODUCT, store)
    assert store.load().tenant_id == TENANT


def test_historical_approved_concept_is_ignored(monkeypatch, tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(store, researcher_run_id=RESEARCH, creative_run_id=CREATIVE)
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(),
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): {
                "next_action": "APPROVE_CREATIVE",
                "blocking_reason": "CREATIVE_CONCEPT_SELECTION_REQUIRED",
                "research_run_id": RESEARCH,
                "creative_run_id": CREATIVE,
                "concept_id": None,
            },
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    assert acceptance.openai_smoke(settings(), store) == 3
    assert store.load().creative_concept_id is None
    assert not any("/production/runs" in call[1] for call in fake.calls)


def test_historical_producer_plan_is_ignored(tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, producer_run_id=PRODUCER)
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/production/plans"): [
                {"id": PLAN, "agent_run_id": HISTORICAL}
            ]
        }
    )

    assert acceptance.bound_plan(fake, PRODUCT, state, store) is None
    assert store.load().production_plan_id is None


@pytest.mark.parametrize(
    ("kind", "model", "provider", "field_name"),
    [
        ("IMAGE", acceptance.OPENAI_IMAGE_MODEL, "openai", "image_job_ids"),
        ("VIDEO", acceptance.SEEDANCE_MODEL, "byteplus", "video_job_ids"),
    ],
)
def test_historical_fake_media_job_is_ignored(tmp_path, kind, model, provider, field_name) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, producer_run_id=PRODUCER, production_plan_id=PLAN)
    live_job = str(UUID(int=10 if kind == "IMAGE" else 11))
    fake = FakeApi(
        {
            ("GET", f"/v1/production/plans/{PLAN}/jobs"): [
                {
                    "id": HISTORICAL,
                    "kind": kind,
                    "model": model,
                    "provider": "fake",
                    "local_demo_provider": True,
                },
                {
                    "id": live_job,
                    "kind": kind,
                    "model": model,
                    "provider": provider,
                    "local_demo_provider": False,
                },
            ]
        }
    )

    assert [job["id"] for job in acceptance.bound_jobs(fake, {"id": PLAN}, state, store, kind)] == [
        live_job
    ]
    assert getattr(store.load(), field_name) == [live_job]


def test_session_resume_inspects_exact_run_without_duplicate(tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, researcher_run_id=RESEARCH)
    route = acceptance.initial_researcher_route()
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/research/runs"): [succeeded(RESEARCH, route)]})

    assert (
        acceptance.advance_run(
            fake,
            PRODUCT,
            state,
            store,
            "researcher_run_id",
            "research",
            route,
            "Researcher",
            f"/v1/products/{PRODUCT}/research/runs",
            {"idempotency_key": "unused"},
        )
        == 0
    )
    assert all(method == "GET" for method, _path, _body in fake.calls)


def test_live_session_adopts_exactly_one_valid_recovery_successor(tmp_path, capsys) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, creative_run_id=CREATIVE)
    route = acceptance.initial_creative_strategist_route()
    predecessor = recoverable_run(
        CREATIVE,
        route,
        status="FAILED",
        failure_code="STRANDED_PROVIDER_OUTCOME_UNKNOWN",
    )
    successor = recoverable_run(
        SUCCESSOR,
        route,
        status="PENDING",
        recovery_of_run_id=CREATIVE,
        resolved_provider=None,
        resolved_model=None,
        model_route_version=None,
        pricing_version=None,
    )
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/creative/runs"): [predecessor, successor]})

    selected = acceptance.bind_recovery_successor(
        fake, PRODUCT, state, store, "creative_run_id", "creative", route, "Creative Strategist"
    )

    assert selected["id"] == SUCCESSOR
    assert store.load().creative_run_id == SUCCESSOR
    output = capsys.readouterr().out
    assert SUCCESSOR in output and CREATIVE in output


def test_live_creative_replacement_is_explicit_idempotent_and_preserves_history(
    monkeypatch, tmp_path
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, researcher_run_id=RESEARCH, creative_run_id=CREATIVE)
    historical_route = acceptance.frozen_route(
        recoverable_run(
            CREATIVE,
            next(
                route
                for route in acceptance.initial_agent_model_routes()
                if route.profile_key == "creative_balanced"
            ),
        ),
        acceptance.initial_creative_strategist_route(),
    )
    failed = recoverable_run(
        CREATIVE,
        historical_route,
        status="FAILED",
        failure_code="MODEL_PROVIDER_INCOMPLETE_RESPONSE",
        estimated_cost="0.178964",
        reserved_cost="0.200000",
    )
    current_route = acceptance.initial_creative_strategist_route()
    replacement = recoverable_run(
        SUCCESSOR,
        current_route,
        status="PENDING",
        resolved_provider=None,
        resolved_model=None,
        model_route_version=None,
        pricing_version=None,
        agent_version_id=str(UUID(int=24)),
        agent_version_number=2,
        reserved_cost="0.416000",
    )
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/agent-runs/{CREATIVE}"): failed,
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): [failed, replacement],
            (
                "POST",
                f"/v1/products/{PRODUCT}/creative/runs/{CREATIVE}/replacement",
            ): replacement,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    assert acceptance.replace_output_limited_creative(settings(), store) == 0
    assert store.load().creative_run_id == SUCCESSOR
    assert store.load().creative_history_run_ids == [CREATIVE]
    assert acceptance.replace_output_limited_creative(settings(), store) == 0
    posts = [call for call in fake.calls if call[0] == "POST"]
    assert posts == [
        (
            "POST",
            f"/v1/products/{PRODUCT}/creative/runs/{CREATIVE}/replacement",
            {"transition_id": state.session_id},
        )
    ]
    assert not any(path.endswith("/research/runs") for method, path, _ in posts)


def test_research_refresh_preserves_history_without_starting_creative_or_producer(
    monkeypatch, tmp_path
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    state = saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    refresh = {
        **recoverable_run(SUCCESSOR, acceptance.initial_researcher_route()),
        "status": "PENDING",
        "agent_type": "researcher",
        "resolved_provider": None,
        "resolved_model": None,
        "model_route_version": None,
        "pricing_version": None,
    }
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/research/snapshots"): [
                {"id": SNAPSHOT, "agent_run_id": RESEARCH, "freshness": "stale"}
            ],
            ("POST", f"/v1/products/{PRODUCT}/research/runs"): refresh,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)
    assert acceptance.refresh_research(settings(), store) == 0
    saved = store.load()
    assert saved is not None
    assert saved.researcher_run_id == SUCCESSOR
    assert saved.researcher_history_run_ids == [RESEARCH]
    assert saved.creative_run_id == CREATIVE
    assert saved.creative_concept_id == CONCEPT
    assert saved.producer_run_id == PRODUCER
    assert saved.creative_revalidation_id is None
    assert not any(
        method == "POST" and ("/creative/" in path or "/production/" in path)
        for method, path, _ in fake.calls
    )
    assert state.researcher_run_id == RESEARCH


def test_live_deterministic_revalidation_binds_current_research_without_generation(
    monkeypatch, tmp_path
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=SUCCESSOR,
        researcher_history_run_ids=[RESEARCH],
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    response = {
        "id": REVALIDATION,
        "product_id": PRODUCT,
        "concept_id": CONCEPT,
        "original_research_snapshot_id": SNAPSHOT,
        "current_research_snapshot_id": UNRELATED,
        "result": "REVALIDATED_FOR_PRODUCTION",
    }
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(SUCCESSOR, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/research/snapshots"): [
                {"id": UNRELATED, "agent_run_id": SUCCESSOR, "freshness": "current"}
            ],
            ("GET", f"/v1/creative/concepts/{CONCEPT}/revalidations"): [],
            ("POST", f"/v1/creative/concepts/{CONCEPT}/revalidate"): response,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)
    assert acceptance.revalidate_creative_concept(settings(), store) == 0
    state = store.load()
    assert state is not None and state.creative_revalidation_id == REVALIDATION
    posts = [(path, body) for method, path, body in fake.calls if method == "POST"]
    assert posts == [(f"/v1/creative/concepts/{CONCEPT}/revalidate", None)]


def _restrategy_resolution(**changes):
    value = {
        "current_stage": "CREATIVE_RESTRATEGY",
        "next_action": "RESTRATEGIZE_CREATIVE",
        "blocking_reason": "CURRENT_AUTHORITY_MATERIALLY_DIFFERS",
        "provider_cost": True,
        "human_approval_required": True,
        "provider_execution_permitted": True,
        "research_state": "SUCCEEDED_CURRENT",
        "creative_state": "REQUIRES_RESTRATEGY",
        "producer_state": "NOT_STARTED",
        "research_run_id": RESEARCH,
        "research_snapshot_id": SNAPSHOT,
        "creative_run_id": CREATIVE,
        "concept_id": CONCEPT,
        "creative_revalidation_id": REVALIDATION,
        "producer_run_id": None,
        "production_plan_id": None,
    }
    value.update(changes)
    return value


def _current_research_snapshot():
    return {
        "id": SNAPSHOT,
        "product_id": PRODUCT,
        "agent_run_id": RESEARCH,
        "product_snapshot_id": PRODUCT_SNAPSHOT,
        "product_snapshot_digest": "sha256:" + "c" * 64,
        "research_context_digest": "sha256:" + "d" * 64,
        "freshness": "current",
    }


def _historical_concept_set():
    return {
        "id": str(UUID(int=41)),
        "product_id": PRODUCT,
        "agent_run_id": CREATIVE,
        "concepts": [
            {
                "id": identifier,
                "payload": {"channel_intent": "TIKTOK"},
            }
            for identifier in (CONCEPT, str(UUID(int=42)), str(UUID(int=43)))
        ],
    }


def _pending_restrategy_run():
    return recoverable_run(
        SUCCESSOR,
        acceptance.initial_creative_strategist_route(),
        status="PENDING",
        resolved_provider=None,
        resolved_model=None,
        model_route_version=None,
        pricing_version=None,
        product_snapshot_id=PRODUCT_SNAPSHOT,
        input_tokens=0,
        output_tokens=0,
        total_tokens=0,
        estimated_cost="0",
        creative_concept_count=3,
        creative_channel_intent="TIKTOK",
        restrategy_of_concept_id=CONCEPT,
        restrategy_historical_creative_run_id=CREATIVE,
        restrategy_trigger_kind="creative_revalidation",
        restrategy_trigger_id=REVALIDATION,
        restrategy_current_research_snapshot_id=SNAPSHOT,
    )


def test_live_creative_restrategy_admits_once_preserves_history_and_reuses_pending_run(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        creative_revalidation_id=REVALIDATION,
        producer_run_id=PRODUCER,
    )
    historical_creative = recoverable_run(
        CREATIVE,
        acceptance.initial_creative_strategist_route(),
        status="SUCCEEDED",
        creative_concept_count=3,
        creative_channel_intent="TIKTOK",
    )
    pending = _pending_restrategy_run()
    historical_producer = recoverable_run(
        PRODUCER,
        acceptance.initial_producer_route(),
        status="FAILED",
        agent_type="producer",
    )
    created = False
    resolutions = iter(
        [
            _restrategy_resolution(),
            _restrategy_resolution(
                current_stage="CREATIVE",
                next_action="WAIT_FOR_CREATIVE",
                blocking_reason="CREATIVE_RUN_PENDING",
                creative_state="PENDING",
                creative_run_id=SUCCESSOR,
                concept_id=None,
                creative_revalidation_id=None,
            ),
            _restrategy_resolution(
                current_stage="CREATIVE",
                next_action="WAIT_FOR_CREATIVE",
                blocking_reason="CREATIVE_RUN_PENDING",
                creative_state="PENDING",
                creative_run_id=SUCCESSOR,
                concept_id=None,
                creative_revalidation_id=None,
            ),
        ]
    )

    def creative_runs():
        return [historical_creative, pending] if created else [historical_creative]

    def create_restrategy():
        nonlocal created
        created = True
        return pending

    revalidation = {
        "id": REVALIDATION,
        "product_id": PRODUCT,
        "concept_id": CONCEPT,
        "current_research_snapshot_id": SNAPSHOT,
        "result": "REQUIRES_RESTRATEGY",
    }
    fake = FakeApi(
        {
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): lambda: next(resolutions),
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/research/snapshots"): [_current_research_snapshot()],
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): creative_runs,
            ("GET", f"/v1/products/{PRODUCT}/creative/concept-sets"): [_historical_concept_set()],
            ("GET", f"/v1/creative/concepts/{CONCEPT}/revalidations"): [revalidation],
            ("GET", f"/v1/products/{PRODUCT}/production/runs"): [historical_producer],
            ("POST", f"/v1/products/{PRODUCT}/creative/runs"): create_restrategy,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.restrategy_creative_concept(settings(), store) == 0
    state = store.load()
    assert state is not None
    assert state.creative_run_id == SUCCESSOR
    assert state.creative_history_run_ids == [CREATIVE]
    assert state.creative_concept_id is None
    assert state.creative_revalidation_id is None
    assert state.producer_run_id is None
    assert state.production_plan_id is None
    assert historical_producer["id"] == PRODUCER
    assert pending["recovery_of_run_id"] is None

    assert acceptance.restrategy_creative_concept(settings(), store) == 0
    posts = [call for call in fake.calls if call[0] == "POST"]
    creative_posts = [call for call in posts if call[1].endswith("/creative/runs")]
    assert len(creative_posts) == 1
    body = creative_posts[0][2]
    assert body["concept_count"] == 3
    assert body["channel_intent"] == "TIKTOK"
    assert body["restrategy_of_concept_id"] == CONCEPT
    assert body["idempotency_key"].startswith("live-creative-restrategy-")
    assert len(body["idempotency_key"]) <= 128
    assert not any("responses" in path or "worker" in path for _, path, _ in fake.calls)
    output = capsys.readouterr().out
    assert f"Creative restrategy requested: {SUCCESSOR}" in output
    assert f"Historical Creative retained: {CREATIVE}" in output
    assert f"retained but unbound from active session: {PRODUCER}" in output
    assert "Creative restrategy already bound" in output
    assert output.count("No provider execution was started by this command.") == 2


def test_live_creative_restrategy_wrong_action_has_zero_mutation(monkeypatch, tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        creative_revalidation_id=REVALIDATION,
        producer_run_id=PRODUCER,
    )
    before = store.path.read_text()
    fake = FakeApi(
        {
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): _restrategy_resolution(
                current_stage="CREATIVE_APPROVAL",
                next_action="APPROVE_CREATIVE",
                blocking_reason="CREATIVE_APPROVAL_REQUIRED",
                creative_state="APPROVAL_REQUIRED",
            )
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    with pytest.raises(RuntimeError, match="LIVE_CREATIVE_RESTRATEGY_NOT_ELIGIBLE"):
        acceptance.restrategy_creative_concept(settings(), store)

    assert store.path.read_text() == before
    assert len(fake.calls) == 1


def test_live_creative_restrategy_adopts_exact_preexisting_run_after_checkpoint_gap(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        creative_revalidation_id=REVALIDATION,
        producer_run_id=PRODUCER,
    )
    historical = recoverable_run(
        CREATIVE,
        acceptance.initial_creative_strategist_route(),
        status="SUCCEEDED",
        creative_concept_count=3,
        creative_channel_intent="TIKTOK",
    )
    pending = _pending_restrategy_run()
    producer = recoverable_run(
        PRODUCER,
        acceptance.initial_producer_route(),
        status="FAILED",
        agent_type="producer",
    )
    resolutions = iter(
        [
            _restrategy_resolution(),
            _restrategy_resolution(
                current_stage="CREATIVE",
                next_action="WAIT_FOR_CREATIVE",
                blocking_reason="CREATIVE_RUN_PENDING",
                creative_state="PENDING",
                creative_run_id=SUCCESSOR,
                concept_id=None,
                creative_revalidation_id=None,
            ),
        ]
    )
    fake = FakeApi(
        {
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): lambda: next(resolutions),
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/research/snapshots"): [_current_research_snapshot()],
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): [historical, pending],
            ("GET", f"/v1/products/{PRODUCT}/creative/concept-sets"): [_historical_concept_set()],
            ("GET", f"/v1/creative/concepts/{CONCEPT}/revalidations"): [
                {
                    "id": REVALIDATION,
                    "product_id": PRODUCT,
                    "concept_id": CONCEPT,
                    "current_research_snapshot_id": SNAPSHOT,
                    "result": "REQUIRES_RESTRATEGY",
                }
            ],
            ("GET", f"/v1/products/{PRODUCT}/production/runs"): [producer],
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.restrategy_creative_concept(settings(), store) == 0

    state = store.load()
    assert state is not None
    assert state.creative_run_id == SUCCESSOR
    assert state.creative_history_run_ids == [CREATIVE]
    assert state.producer_run_id is None
    assert all(
        not (method == "POST" and path.endswith("/creative/runs")) for method, path, _ in fake.calls
    )
    assert f"Existing Creative restrategy adopted: {SUCCESSOR}" in capsys.readouterr().out


def test_live_creative_restrategy_rejects_ambiguous_active_candidates(
    monkeypatch, tmp_path
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        creative_revalidation_id=REVALIDATION,
        producer_run_id=PRODUCER,
    )
    historical = recoverable_run(
        CREATIVE,
        acceptance.initial_creative_strategist_route(),
        status="SUCCEEDED",
        creative_concept_count=3,
        creative_channel_intent="TIKTOK",
    )
    first = _pending_restrategy_run()
    second = {**first, "id": UNRELATED}
    fake = FakeApi(
        {
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): _restrategy_resolution(),
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/research/snapshots"): [_current_research_snapshot()],
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): [historical, first, second],
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    with pytest.raises(RuntimeError, match="ACTIVE_RUN_AMBIGUOUS"):
        acceptance.restrategy_creative_concept(settings(), store)

    assert all(
        not (method == "POST" and path.endswith("/creative/runs")) for method, path, _ in fake.calls
    )


def test_restrategy_success_requires_approval_then_starts_new_normal_producer_lineage(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=SUCCESSOR,
        creative_history_run_ids=[CREATIVE],
    )
    approval = _restrategy_resolution(
        current_stage="CREATIVE_APPROVAL",
        next_action="APPROVE_CREATIVE",
        blocking_reason="CREATIVE_CONCEPT_SELECTION_REQUIRED",
        creative_state="SUCCEEDED",
        creative_run_id=SUCCESSOR,
        concept_id=None,
        creative_revalidation_id=None,
    )
    fake = FakeApi({("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): approval})
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.e2e(settings(), store) == 0
    assert "Next action: APPROVE_CREATIVE" in capsys.readouterr().out

    state = store.load()
    assert state is not None
    state.creative_concept_id = NEW_CONCEPT
    store.save(state)
    producer_resolution = _restrategy_resolution(
        current_stage="PRODUCER",
        next_action="RUN_PRODUCER",
        blocking_reason=None,
        creative_state="APPROVED_FOR_PRODUCTION",
        creative_run_id=SUCCESSOR,
        concept_id=NEW_CONCEPT,
        creative_revalidation_id=None,
    )
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}"): workspace(),
            ("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): producer_resolution,
            ("POST", f"/v1/creative/concepts/{NEW_CONCEPT}/production/runs"): {
                "id": NEW_PRODUCER,
                "recovery_of_run_id": None,
            },
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.openai_smoke(settings(), store) == 3
    assert store.load().producer_run_id == NEW_PRODUCER
    producer_posts = [
        call for call in fake.calls if call[0] == "POST" and "/production/runs" in call[1]
    ]
    assert producer_posts == [
        (
            "POST",
            f"/v1/creative/concepts/{NEW_CONCEPT}/production/runs",
            {"idempotency_key": f"live-production-{state.session_id}"},
        )
    ]
    assert all("replacement" not in path for _, path, _ in fake.calls)


def test_live_e2e_only_reports_authoritative_next_action(monkeypatch, tmp_path, capsys) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    before = store.path.read_text()
    response = {
        "current_stage": "CREATIVE_REVALIDATION",
        "next_action": "REVALIDATE_CREATIVE",
        "blocking_reason": "APPROVED_CREATIVE_USES_HISTORICAL_RESEARCH",
        "provider_cost": False,
        "human_approval_required": True,
        "provider_execution_permitted": False,
        "research_state": "SUCCEEDED_CURRENT",
        "creative_state": "STALE_RESEARCH",
        "producer_state": "NOT_STARTED",
    }
    fake = FakeApi({("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): response})
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.e2e(settings(), store) == 0

    assert store.path.read_text() == before
    assert fake.calls == [
        (
            "POST",
            f"/v1/products/{PRODUCT}/pipeline/next-action",
            {
                "research_run_id": RESEARCH,
                "creative_run_id": CREATIVE,
                "concept_id": CONCEPT,
                "creative_revalidation_id": None,
                "producer_run_id": PRODUCER,
                "production_plan_id": None,
                "use_latest_when_unbound": False,
            },
        )
    ]
    output = capsys.readouterr().out
    assert "Next action: REVALIDATE_CREATIVE" in output
    assert "Provider cost: no" in output
    assert "No provider, worker, generation, or database mutation" in output


def test_live_e2e_without_checkpoint_inspects_latest_product_state(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    response = {
        "current_stage": "RESEARCH",
        "next_action": "WAIT_FOR_RESEARCH",
        "blocking_reason": "RESEARCH_RUN_PENDING",
        "provider_cost": False,
        "human_approval_required": False,
        "provider_execution_permitted": False,
        "research_state": "PENDING",
        "creative_state": "NOT_STARTED",
        "producer_state": "NOT_STARTED",
    }
    fake = FakeApi({("POST", f"/v1/products/{PRODUCT}/pipeline/next-action"): response})
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)

    assert acceptance.e2e(settings(), store) == 0

    assert not store.path.exists()
    assert fake.calls[0][2]["use_latest_when_unbound"] is True
    assert "Next action: WAIT_FOR_RESEARCH" in capsys.readouterr().out


def test_live_producer_replacement_requires_revalidation_after_research_refresh(
    monkeypatch, tmp_path
) -> None:
    store = acceptance.StateStore(tmp_path / "state.json")
    saved_state(
        store,
        researcher_run_id=SUCCESSOR,
        researcher_history_run_ids=[RESEARCH],
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    fake = FakeApi({})
    monkeypatch.setattr(acceptance, "api_for", lambda *_args, **_kwargs: fake)
    with pytest.raises(RuntimeError, match="REVALIDATION_REQUIRED"):
        acceptance.replace_invalid_producer(settings(), store)
    assert fake.calls == []


def test_live_producer_replacement_is_explicit_and_leaves_successor_pending(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    failed_run = replace(
        run(),
        id=UUID(PRODUCER),
        tenant_id=UUID(TENANT),
        product_id=UUID(PRODUCT),
        agent_type="producer",
        input_context_kind="production_planning.v2",
        selected_evidence=(),
        output_contract_key="production.production_plan",
        output_contract_version=2,
        status=AgentRunStatus.FAILED,
        failure_code="PRODUCTION_PLAN_INVALID",
        estimated_cost=Decimal("0.129156"),
        unknown_cost=Decimal("0"),
    )
    replacement_run = replace(
        failed_run,
        id=UUID(SUCCESSOR),
        recovery_of_run_id=UUID(PRODUCER),
        output_contract_version=3,
        status=AgentRunStatus.PENDING,
        failure_code=None,
        agent_version_id=UUID(int=24),
        resolved_provider=None,
        resolved_model=None,
        resolved_model_route_version=None,
        pricing_version=None,
    )
    failed = _agent_run(failed_run).model_dump(mode="json")
    replacement = _agent_run(replacement_run).model_dump(mode="json")
    assert failed["output_contract_key"] == "production.production_plan"
    assert failed["output_contract_version"] == 2
    assert replacement["output_contract_key"] == "production.production_plan"
    assert replacement["output_contract_version"] == 3
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/production/runs"): [failed],
            (
                "POST",
                f"/v1/products/{PRODUCT}/production/runs/{PRODUCER}/replacement",
            ): replacement,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    assert acceptance.replace_invalid_producer(settings(), store) == 0
    assert store.load().producer_run_id == SUCCESSOR
    assert [call for call in fake.calls if call[0] == "POST"] == [
        (
            "POST",
            f"/v1/products/{PRODUCT}/production/runs/{PRODUCER}/replacement",
            {"transition_id": state.session_id},
        )
    ]
    output = capsys.readouterr().out
    assert "left PENDING" in output and "No provider execution" in output


def test_live_producer_replacement_preserves_chained_parent_lineage(monkeypatch, tmp_path) -> None:
    v1 = HISTORICAL
    v2_no_response = UNRELATED
    v2_failed = PRODUCER
    v3_pending = SUCCESSOR
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=v2_failed,
    )
    runs = [
        producer_run(
            v1,
            contract_version=1,
            status=AgentRunStatus.FAILED,
            failure_code="MODEL_INVALID_OUTPUT",
            agent_version=21,
        ),
        producer_run(
            v2_no_response,
            contract_version=2,
            status=AgentRunStatus.FAILED,
            failure_code="MODEL_PROVIDER_BAD_REQUEST",
            recovery_of_run_id=v1,
            agent_version=22,
        ),
        producer_run(
            v2_failed,
            contract_version=2,
            status=AgentRunStatus.FAILED,
            failure_code="PRODUCTION_PLAN_INVALID",
            recovery_of_run_id=v2_no_response,
            agent_version=22,
        ),
    ]
    successor = producer_run(
        v3_pending,
        contract_version=3,
        status=AgentRunStatus.PENDING,
        failure_code=None,
        recovery_of_run_id=v2_failed,
        agent_version=23,
    )
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/production/runs"): runs,
            (
                "POST",
                f"/v1/products/{PRODUCT}/production/runs/{v2_failed}/replacement",
            ): successor,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    assert acceptance.replace_invalid_producer(settings(), store) == 0

    assert store.load().producer_run_id == v3_pending
    assert runs[2]["recovery_of_run_id"] == v2_no_response
    assert successor["recovery_of_run_id"] == v2_failed
    assert [call for call in fake.calls if call[0] == "POST"] == [
        (
            "POST",
            f"/v1/products/{PRODUCT}/production/runs/{v2_failed}/replacement",
            {"transition_id": state.session_id},
        )
    ]
    assert not any("responses" in path for _method, path, _body in fake.calls)


def test_live_producer_replacement_adopts_existing_child_idempotently(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    failed = producer_run(
        PRODUCER,
        contract_version=2,
        status=AgentRunStatus.FAILED,
        failure_code="PRODUCTION_PLAN_INVALID",
        recovery_of_run_id=UNRELATED,
        agent_version=22,
    )
    successor = producer_run(
        SUCCESSOR,
        contract_version=3,
        status=AgentRunStatus.PENDING,
        failure_code=None,
        recovery_of_run_id=PRODUCER,
        agent_version=23,
    )
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/production/runs"): [failed, successor]})
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    assert acceptance.replace_invalid_producer(settings(), store) == 0
    assert store.load().producer_run_id == SUCCESSOR
    assert acceptance.replace_invalid_producer(settings(), store) == 0
    assert all(method == "GET" for method, _path, _body in fake.calls)
    output = capsys.readouterr().out
    assert "was adopted" in output
    assert "already bound" in output
    assert output.count("No provider execution") == 1


def test_live_producer_replacement_rejects_multiple_child_successors(monkeypatch, tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    failed = producer_run(
        PRODUCER,
        contract_version=2,
        status=AgentRunStatus.FAILED,
        failure_code="PRODUCTION_PLAN_INVALID",
        agent_version=22,
    )
    first = producer_run(
        SUCCESSOR,
        contract_version=3,
        status=AgentRunStatus.PENDING,
        failure_code=None,
        recovery_of_run_id=PRODUCER,
        agent_version=23,
    )
    second = producer_run(
        str(UUID(int=12)),
        contract_version=3,
        status=AgentRunStatus.PENDING,
        failure_code=None,
        recovery_of_run_id=PRODUCER,
        agent_version=24,
    )
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/production/runs"): [failed, first, second]})
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    with pytest.raises(RuntimeError, match="LIVE_PRODUCER_REPLACEMENT_SUCCESSOR_AMBIGUOUS"):
        acceptance.replace_invalid_producer(settings(), store)

    assert store.load().producer_run_id == PRODUCER
    assert all(method == "GET" for method, _path, _body in fake.calls)


def test_live_producer_replacement_rejects_mismatched_existing_child(monkeypatch, tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=CREATIVE,
        creative_concept_id=CONCEPT,
        producer_run_id=PRODUCER,
    )
    failed = producer_run(
        PRODUCER,
        contract_version=2,
        status=AgentRunStatus.FAILED,
        failure_code="PRODUCTION_PLAN_INVALID",
        agent_version=22,
    )
    mismatched = {
        **producer_run(
            SUCCESSOR,
            contract_version=3,
            status=AgentRunStatus.PENDING,
            failure_code=None,
            recovery_of_run_id=PRODUCER,
            agent_version=23,
        ),
        "resolved_agent_definition_id": str(UUID(int=999)),
    }
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/production/runs"): [failed, mismatched]})
    monkeypatch.setattr(acceptance, "api_for", lambda _settings: fake)

    with pytest.raises(RuntimeError, match="LIVE_PRODUCER_REPLACEMENT_PROVENANCE_MISMATCH"):
        acceptance.replace_invalid_producer(settings(), store)

    assert store.load().producer_run_id == PRODUCER
    assert all(method == "GET" for method, _path, _body in fake.calls)


def test_recovery_successor_binding_remains_compatible_after_contract_upgrade(
    tmp_path,
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, producer_run_id=PRODUCER)
    route = acceptance.initial_producer_route()
    upgraded = recoverable_run(
        PRODUCER,
        route,
        status="FAILED",
        failure_code="MODEL_PROVIDER_BAD_REQUEST",
        recovery_of_run_id=UNRELATED,
        agent_type="producer",
        prompt_revision="producer_v6_contract_v3",
        input_context_kind="production_planning.v2",
        model_profile_key=route.profile_key,
        output_contract_key="production.production_plan",
        output_contract_version=3,
    )
    rerun = {
        **upgraded,
        "id": SUCCESSOR,
        "status": "PENDING",
        "failure_code": None,
        "recovery_of_run_id": PRODUCER,
        "resolved_provider": None,
        "resolved_model": None,
        "model_route_version": None,
        "pricing_version": None,
    }
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/production/runs"): [upgraded, rerun]})

    selected = acceptance.bind_recovery_successor(
        fake,
        PRODUCT,
        state,
        store,
        "producer_run_id",
        "production",
        route,
        "Producer",
    )

    assert selected["id"] == SUCCESSOR
    assert store.load().producer_run_id == SUCCESSOR
    assert selected["recovery_of_run_id"] == PRODUCER
    assert upgraded["recovery_of_run_id"] == UNRELATED


def test_live_status_preserves_historical_unknown_and_actual_costs_after_replacement(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(
        store,
        researcher_run_id=RESEARCH,
        creative_run_id=SUCCESSOR,
        creative_history_run_ids=[CREATIVE],
    )
    current_route = acceptance.initial_creative_strategist_route()
    historical_route = next(
        route
        for route in acceptance.initial_agent_model_routes()
        if route.profile_key == "creative_balanced"
    )
    current = recoverable_run(
        SUCCESSOR,
        current_route,
        status="PENDING",
        resolved_provider=None,
        resolved_model=None,
        model_route_version=None,
        pricing_version=None,
        estimated_cost="0",
        reserved_cost="0.416000",
        agent_version_id=str(UUID(int=24)),
    )
    terminal = recoverable_run(
        CREATIVE,
        historical_route,
        status="FAILED",
        failure_code="MODEL_PROVIDER_INCOMPLETE_RESPONSE",
        recovery_of_run_id=UNRELATED,
        estimated_cost="0.178964",
        unknown_cost="0",
    )
    unknown_two = recoverable_run(
        UNRELATED,
        historical_route,
        status="FAILED",
        recovery_of_run_id=HISTORICAL,
        estimated_cost="0",
        unknown_cost="0.256000",
        remaining_unknown_cost="0.256000",
    )
    unknown_one = recoverable_run(
        HISTORICAL,
        historical_route,
        status="FAILED",
        estimated_cost="0",
        unknown_cost="0.256000",
        remaining_unknown_cost="0.256000",
    )
    fake = FakeApi(
        {
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                {
                    **succeeded(RESEARCH, acceptance.initial_researcher_route()),
                    "estimated_cost": "0",
                }
            ],
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): [current],
            ("GET", f"/v1/agent-runs/{CREATIVE}"): terminal,
            ("GET", f"/v1/agent-runs/{UNRELATED}"): unknown_two,
            ("GET", f"/v1/agent-runs/{HISTORICAL}"): unknown_one,
        }
    )
    monkeypatch.setattr(acceptance, "api_for", lambda _settings, **_kwargs: fake)

    assert acceptance.session_status(settings(), store) == 0
    output = capsys.readouterr().out
    assert "historical Creative lineage:" in output
    assert "immutable original unknown cost: 0.512000 USD" in output
    assert "Session-bound actual cost: 0.178964 USD" in output
    assert "Session-bound reserved cost: 0.416000 USD" in output
    assert "Session-bound unknown potential cost: 0.512000 USD" in output


def test_live_session_does_not_adopt_unrelated_recovery_successor(tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, creative_run_id=CREATIVE)
    route = acceptance.initial_creative_strategist_route()
    predecessor = recoverable_run(CREATIVE, route, status="RUNNING")
    unrelated = recoverable_run(SUCCESSOR, route, recovery_of_run_id=UNRELATED)
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/creative/runs"): [predecessor, unrelated]})

    selected = acceptance.bind_recovery_successor(
        fake, PRODUCT, state, store, "creative_run_id", "creative", route, "Creative Strategist"
    )

    assert selected["id"] == CREATIVE
    assert store.load().creative_run_id == CREATIVE


def test_live_session_rejects_multiple_recovery_successors(tmp_path) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    state = saved_state(store, creative_run_id=CREATIVE)
    route = acceptance.initial_creative_strategist_route()
    values = [
        recoverable_run(CREATIVE, route, status="FAILED"),
        recoverable_run(SUCCESSOR, route, recovery_of_run_id=CREATIVE),
        recoverable_run(str(UUID(int=12)), route, recovery_of_run_id=CREATIVE),
    ]
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/creative/runs"): values})

    with pytest.raises(RuntimeError, match="SUCCESSOR_AMBIGUOUS"):
        acceptance.bind_recovery_successor(
            fake,
            PRODUCT,
            state,
            store,
            "creative_run_id",
            "creative",
            route,
            "Creative Strategist",
        )
    assert store.load().creative_run_id == CREATIVE


def test_live_status_shows_recovery_and_separates_costs(monkeypatch, tmp_path, capsys) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(store, creative_run_id=CREATIVE)
    route = acceptance.initial_creative_strategist_route()
    stranded = recoverable_run(
        CREATIVE,
        route,
        status="RUNNING",
        operational_status="recovery_required",
        recovery_classification="PROVIDER_OUTCOME_UNKNOWN",
        estimated_cost="0",
        reserved_cost="0.256000",
        unknown_cost="0.256000",
        reconciled_actual_cost="0",
        remaining_unknown_cost="0.256000",
    )
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/creative/runs"): [stranded]})
    monkeypatch.setattr(acceptance, "api_for", lambda _settings, **_kwargs: fake)

    assert acceptance.session_status(settings(), store) == 0

    output = capsys.readouterr().out
    assert "Creative Strategist: RUNNING / RECOVERY_REQUIRED" in output
    assert "immutable original unknown cost: 0.256000 USD" in output
    assert "remaining unknown potential cost: 0.256000 USD" in output
    assert "Session-bound actual cost: 0 USD" in output
    assert "Session-bound reserved cost: 0.256000 USD" in output
    assert "Session-bound unknown potential cost: 0.256000 USD" in output


def test_live_status_reports_reconciled_cost_without_mutating_original_unknown(
    monkeypatch, tmp_path, capsys
) -> None:
    store = acceptance.StateStore(tmp_path / "live-validation.json")
    saved_state(store, creative_run_id=CREATIVE)
    route = acceptance.initial_creative_strategist_route()
    reconciled = recoverable_run(
        CREATIVE,
        route,
        status="FAILED",
        failure_code="STRANDED_PROVIDER_OUTCOME_UNKNOWN",
        estimated_cost="0",
        reserved_cost="0.256000",
        unknown_cost="0.256000",
        reconciled_actual_cost="0",
        remaining_unknown_cost="0",
    )
    fake = FakeApi({("GET", f"/v1/products/{PRODUCT}/creative/runs"): [reconciled]})
    monkeypatch.setattr(acceptance, "api_for", lambda _settings, **_kwargs: fake)

    assert acceptance.session_status(settings(), store) == 0

    output = capsys.readouterr().out
    assert "immutable original unknown cost: 0.256000 USD" in output
    assert "reconciled actual cost: 0 USD" in output
    assert "remaining unknown potential cost:" not in output
    assert "Session-bound actual cost: 0 USD" in output
    assert "Session-bound unknown potential cost: 0 USD" in output


def test_reset_removes_only_local_session_state(tmp_path) -> None:
    directory = tmp_path / ".creative-marketer"
    store = acceptance.StateStore(directory / "live-validation.json")
    saved_state(store)
    sibling = directory / "operator-note.txt"
    sibling.write_text("retain")

    assert acceptance.reset_session(store) == 0
    assert not store.path.exists()
    assert sibling.read_text() == "retain"
