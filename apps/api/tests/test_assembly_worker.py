# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

import creative_marketer_api.assembly_worker as worker_module
from creative_marketer.assembly.execution import AssemblyWorkloadIdentity
from creative_marketer_api.assembly_worker import _bridge_loop, _workload, main, run
from creative_marketer_api.config import Settings

DATABASE = "postgresql+psycopg://test:test@localhost:5432/test"


def test_assembly_workload_is_deterministic_overridable_and_deployment_safe() -> None:
    first = _workload(Settings(database_url=DATABASE))
    assert first == _workload(Settings(database_url=DATABASE))
    assert first.workload_id == "local-assembly-worker"
    actor = uuid4()
    explicit = _workload(
        Settings(
            database_url=DATABASE,
            assembly_workload_actor_id=actor,
            assembly_workload_id="assembly-worker-1",
        )
    )
    assert explicit.actor_id == actor
    with pytest.raises(ValueError, match="invalid"):
        AssemblyWorkloadIdentity(actor, " ", "test")
    deployed = Settings(
        app_env="production",
        database_url=DATABASE,
        object_storage_backend="disabled",
        dev_identity_enabled=False,
    )
    with pytest.raises(RuntimeError, match="deployment-issued"):
        _workload(deployed)


@pytest.mark.asyncio
async def test_assembly_worker_requires_private_storage(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_module,
        "Settings",
        lambda: Settings(database_url=DATABASE, object_storage_backend="disabled"),
    )
    with pytest.raises(RuntimeError, match="private S3"):
        await run()


@pytest.mark.asyncio
async def test_assembly_worker_composes_renderer_executor_temporal_and_bridge(monkeypatch) -> None:
    settings = Settings(
        database_url=DATABASE,
        event_publisher_database_url=DATABASE,
        object_storage_backend="s3",
        cors_origins=["http://localhost:3000"],
    )
    monkeypatch.setattr(worker_module, "Settings", lambda: settings)
    sessions = object()
    monkeypatch.setattr(worker_module, "create_session_factory", lambda _: sessions)
    monkeypatch.setattr(worker_module, "S3ObjectStore", lambda **_: object())
    monkeypatch.setattr(worker_module, "SqlAlchemyCatalogUnitOfWorkFactory", lambda _: object())
    monkeypatch.setattr(worker_module, "AssetService", lambda *_: object())
    monkeypatch.setattr(worker_module, "AssemblyJobExecutor", lambda *_: object())
    renderer = SimpleNamespace(version=AsyncMock(return_value="ffmpeg-7.1"))
    monkeypatch.setattr(worker_module, "FFmpegAssemblyRenderer", lambda: renderer)
    client = object()
    monkeypatch.setattr(worker_module, "connect_client", AsyncMock(return_value=client))
    bridge = AsyncMock()
    monkeypatch.setattr(worker_module, "_bridge_loop", bridge)

    class WorkerContext:
        def __init__(self, *args, **kwargs) -> None:
            assert args[0] is client
            assert kwargs["activities"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> None:
            return None

    monkeypatch.setattr(worker_module, "Worker", WorkerContext)
    await run()
    renderer.version.assert_awaited_once()
    bridge.assert_awaited_once()


@pytest.mark.asyncio
async def test_assembly_bridge_marks_success_and_retry_and_requires_publisher(monkeypatch) -> None:
    settings = Settings(database_url=DATABASE, event_publisher_database_url=DATABASE)
    success = SimpleNamespace(event=object(), trace_context={})
    retry = SimpleNamespace(event=object(), trace_context={})
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
                raise RuntimeError("Temporal unavailable")

    monkeypatch.setattr(worker_module, "ProcessEvent", lambda *args: Consumer())
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(side_effect=RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        await _bridge_loop(settings, AsyncMock())
    publisher.mark_published.assert_awaited_once()
    retry_call = publisher.mark_retryable.await_args
    assert retry_call.kwargs["error_code"] == "ASSEMBLY_BRIDGE_UNAVAILABLE"
    assert retry_call.kwargs["error_digest"].startswith("sha256:")

    with pytest.raises(RuntimeError, match="EVENT_PUBLISHER_DATABASE_URL"):
        await _bridge_loop(
            Settings(database_url=DATABASE, event_publisher_database_url=None), AsyncMock()
        )


def test_assembly_worker_main_runs_async_entrypoint(monkeypatch) -> None:
    called = False

    def run_coroutine(coroutine) -> None:
        nonlocal called
        called = True
        coroutine.close()

    monkeypatch.setattr(asyncio, "run", run_coroutine)
    main()
    assert called
