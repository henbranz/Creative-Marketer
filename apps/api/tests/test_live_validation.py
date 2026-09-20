# mypy: disable-error-code="arg-type,attr-defined,no-untyped-call,no-untyped-def,union-attr"

import json
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError
from uuid import UUID

import pytest

import scripts.live_acceptance as acceptance
import scripts.live_validation as live
from creative_marketer_api.config import Settings

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
        "status": "SUCCEEDED",
        "resolved_provider": "openai",
        "resolved_model": route.model,
        "model_route_version": route.route_version,
        "input_tokens": 1,
        "output_tokens": 1,
        "total_tokens": 2,
        "estimated_cost": "0.01",
        "currency": "USD",
    }


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
            ("GET", f"/v1/products/{PRODUCT}/research/runs"): [
                succeeded(RESEARCH, acceptance.initial_researcher_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/creative/runs"): [
                succeeded(CREATIVE, acceptance.initial_creative_strategist_route())
            ],
            ("GET", f"/v1/products/{PRODUCT}/creative/concept-sets"): [
                {
                    "agent_run_id": HISTORICAL,
                    "concepts": [{"id": CONCEPT, "decision_state": "APPROVED_FOR_PRODUCTION"}],
                },
                {"agent_run_id": CREATIVE, "concepts": []},
            ],
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


def test_reset_removes_only_local_session_state(tmp_path) -> None:
    directory = tmp_path / ".creative-marketer"
    store = acceptance.StateStore(directory / "live-validation.json")
    saved_state(store)
    sibling = directory / "operator-note.txt"
    sibling.write_text("retain")

    assert acceptance.reset_session(store) == 0
    assert not store.path.exists()
    assert sibling.read_text() == "retain"
