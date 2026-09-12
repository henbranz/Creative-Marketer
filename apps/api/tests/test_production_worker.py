# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import creative_marketer.infrastructure.database.production_authority as authority_module
import creative_marketer_api.production_worker as worker_module
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.production.application import initial_media_router
from creative_marketer.production.domain import (
    ProductionCapabilityChanged,
    ProductionNotFound,
    ProductionPermissionDenied,
)
from creative_marketer_api.config import Settings
from creative_marketer_api.production_worker import _bridge_loop, _workload, main, run

DATABASE = "postgresql+psycopg://test:test@localhost:5432/test"


def test_local_media_workload_is_distinct_deterministic_and_overridable() -> None:
    first = _workload(Settings(database_url=DATABASE))
    second = _workload(Settings(database_url=DATABASE))
    assert first == second
    assert first.workload_id == "local-media-worker"
    actor = uuid4()
    explicit = _workload(
        Settings(
            database_url=DATABASE,
            media_workload_actor_id=actor,
            media_workload_id="local-explicit-media",
        )
    )
    assert explicit.actor_id == actor
    with pytest.raises(ValueError, match="invalid"):
        MediaWorkloadIdentity(actor, " ", "test")
    deployed = Settings(
        app_env="production",
        database_url=DATABASE,
        object_storage_backend="disabled",
    )
    with pytest.raises(RuntimeError, match="deployment-issued"):
        _workload(deployed)


def test_generation_authority_capability_revalidation_fails_closed() -> None:
    image = initial_media_router().resolve("production_image")
    video = initial_media_router().resolve("production_video")
    SqlAlchemyGenerationAuthority._validate_capability(image, {}, None)
    SqlAlchemyGenerationAuthority._validate_capability(
        video, {"resolution": "720p", "aspect_ratio": "9:16"}, 5
    )
    for route, spec, duration in (
        (image, {"size": "1x1"}, None),
        (video, {"resolution": "4k", "aspect_ratio": "9:16"}, 5),
        (video, {"resolution": "720p", "aspect_ratio": "portrait"}, 5),
        (video, {"resolution": "720p", "aspect_ratio": "9:16"}, 31),
    ):
        with pytest.raises(ProductionCapabilityChanged):
            SqlAlchemyGenerationAuthority._validate_capability(route, spec, duration)


@pytest.mark.asyncio
async def test_generation_authority_job_lookup_and_prepared_state_fail_closed(monkeypatch) -> None:
    tenant_id = uuid4()
    job_id = uuid4()
    job = SimpleNamespace(tenant_id=tenant_id)

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

        def begin(self):
            return self

    class Repository:
        def __init__(self, _session) -> None:
            pass

        async def get_job(self, requested_id):
            return job if requested_id == job_id else None

    monkeypatch.setattr(authority_module, "SqlAlchemyProductionRepository", Repository)
    authority = SqlAlchemyGenerationAuthority(
        lambda: Session(),
        object(),
        MediaWorkloadIdentity(uuid4(), "test-media-worker", "test"),
    )
    authority._tenant = AsyncMock()  # type: ignore[method-assign]
    assert (await authority.current_job(tenant_id, job_id)).tenant_id == tenant_id
    await authority.authorize_resource(tenant_id, job_id)
    with pytest.raises(ProductionNotFound):
        await authority.current_job(tenant_id, uuid4())
    with pytest.raises(ProductionPermissionDenied, match="prepared"):
        authority._state(job_id)
    authority._transition = AsyncMock()  # type: ignore[method-assign]
    await authority.outcome_unknown(job_id)
    authority._transition.assert_awaited_once()


@pytest.mark.asyncio
async def test_worker_fails_before_connecting_without_required_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_module,
        "Settings",
        lambda: Settings(database_url=DATABASE, object_storage_backend="disabled"),
    )
    with pytest.raises(RuntimeError, match="private S3"):
        await run()
    monkeypatch.setattr(
        worker_module,
        "Settings",
        lambda: Settings(
            database_url=DATABASE,
            object_storage_backend="s3",
            cors_origins=["http://localhost:3000"],
        ),
    )
    with pytest.raises(RuntimeError, match="provider selection"):
        await run()


@pytest.mark.asyncio
async def test_worker_composes_governed_fake_runtime(monkeypatch) -> None:
    settings = Settings(
        database_url=DATABASE,
        event_publisher_database_url=DATABASE,
        object_storage_backend="s3",
        cors_origins=["http://localhost:3000"],
        media_image_provider="fake",
        media_video_provider="fake",
    )
    monkeypatch.setattr(worker_module, "Settings", lambda: settings)
    sessions = object()
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _: sessions)
    monkeypatch.setattr(worker_module, "S3ObjectStore", lambda **_: object())
    monkeypatch.setattr(worker_module, "SqlAlchemyGenerationAuthority", lambda *args: object())
    for name in (
        "SqlAlchemyCatalogUnitOfWorkFactory",
        "SqlAlchemyToolRegistryUnitOfWorkFactory",
        "SqlAlchemyAgentRegistryUnitOfWorkFactory",
        "SqlAlchemyPermissionUnitOfWorkFactory",
        "SqlAlchemyGatewayUnitOfWorkFactory",
        "SqlAlchemyProductionUnitOfWorkFactory",
    ):
        monkeypatch.setattr(worker_module, name, lambda _: object())

    class ToolResolver:
        async def __call__(self, _: str):
            return SimpleNamespace(definition_id=uuid4(), version_id=uuid4())

    monkeypatch.setattr(worker_module, "ResolveActiveTool", lambda _: ToolResolver())
    monkeypatch.setattr(worker_module, "ResolveActiveAgentVersion", lambda _: object())
    monkeypatch.setattr(worker_module, "compose_production_gateway", lambda *args: object())
    client = object()
    monkeypatch.setattr(worker_module, "connect_client", AsyncMock(return_value=client))
    bridge = AsyncMock()
    monkeypatch.setattr(worker_module, "_bridge_loop", bridge)

    class WorkerContext:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr(worker_module, "Worker", WorkerContext)
    await run()
    bridge.assert_awaited_once()


@pytest.mark.asyncio
async def test_bridge_marks_success_and_retry_then_requires_publisher_url(monkeypatch) -> None:
    settings = Settings(database_url=DATABASE, event_publisher_database_url=DATABASE)
    success = SimpleNamespace(event=object(), trace_context={}, marker="success")
    retry = SimpleNamespace(event=object(), trace_context={}, marker="retry")
    publisher = SimpleNamespace(
        claim_ready_types=AsyncMock(return_value=(success, retry)),
        mark_published=AsyncMock(),
        mark_retryable=AsyncMock(),
    )
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _: object())
    monkeypatch.setattr(worker_module, "PostgresPublisherStore", lambda _: publisher)

    class Consumer:
        async def __call__(self, _name, event, _trace):
            if event is retry.event:
                raise RuntimeError("provider unavailable")

    monkeypatch.setattr(worker_module, "ProcessEvent", lambda *args: Consumer())
    monkeypatch.setattr(
        asyncio,
        "sleep",
        AsyncMock(side_effect=RuntimeError("stop bridge")),
    )
    with pytest.raises(RuntimeError, match="stop bridge"):
        await _bridge_loop(settings, AsyncMock())
    publisher.mark_published.assert_awaited_once()
    publisher.mark_retryable.assert_awaited_once()
    retry_call = publisher.mark_retryable.await_args
    assert retry_call.kwargs["error_code"] == "PRODUCTION_BRIDGE_UNAVAILABLE"
    assert retry_call.kwargs["error_digest"].startswith("sha256:")

    without_publisher = Settings(database_url=DATABASE)
    with pytest.raises(RuntimeError, match="EVENT_PUBLISHER_DATABASE_URL"):
        await _bridge_loop(without_publisher, AsyncMock())


def test_worker_main_runs_async_entrypoint(monkeypatch) -> None:
    called = False

    def run_coroutine(coroutine) -> None:
        nonlocal called
        called = True
        coroutine.close()

    monkeypatch.setattr(asyncio, "run", run_coroutine)
    main()
    assert called
