# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type"

from uuid import uuid4

import pytest

import creative_marketer_api.production_worker as worker_module
from creative_marketer.infrastructure.database.production_authority import (
    MediaWorkloadIdentity,
    SqlAlchemyGenerationAuthority,
)
from creative_marketer.production.application import initial_media_router
from creative_marketer.production.domain import ProductionCapabilityChanged
from creative_marketer_api.config import Settings
from creative_marketer_api.production_worker import _workload, run

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
async def test_worker_fails_before_connecting_without_required_adapters(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_module,
        "Settings",
        lambda: Settings(database_url=DATABASE, object_storage_backend="disabled"),
    )
    with pytest.raises(RuntimeError, match="private S3"):
        await run()
