import asyncio
from datetime import UTC
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from creative_marketer.catalog.asset_application import ObjectStoreUnavailable
from creative_marketer.infrastructure.object_storage import bootstrap
from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore


class Body:
    def __init__(self) -> None:
        self.chunks = [b"one", b"two", b""]
        self.closed = False

    def read(self, _size: int) -> bytes:
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True


class AlreadyExists(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "BucketAlreadyOwnedByYou"}}


class UnsupportedPublicAccessBlock(Exception):
    response: ClassVar[dict[str, dict[str, str]]] = {"Error": {"Code": "MalformedXML"}}


class FakeClient:
    def __init__(self) -> None:
        self.body = Body()
        self.calls: list[str] = []

    def generate_presigned_post(self, **kwargs: Any) -> dict[str, object]:
        self.calls.append("upload")
        return {"url": "https://public/upload", "fields": {"key": kwargs["Key"]}}

    def generate_presigned_url(self, *_args: Any, **_kwargs: Any) -> str:
        self.calls.append("download")
        return "https://public/download"

    def head_object(self, **kwargs: Any) -> dict[str, object]:
        if str(kwargs["Key"]).startswith("destination"):
            raise RuntimeError("not found")
        return {"ContentLength": 6, "ContentType": "image/png"}

    def get_object(self, **_kwargs: Any) -> dict[str, object]:
        return {"Body": self.body}

    def copy_object(self, **_kwargs: Any) -> None:
        self.calls.append("copy")

    def create_bucket(self, **_kwargs: Any) -> None:
        raise AlreadyExists

    def delete_bucket_policy(self, **_kwargs: Any) -> None:
        self.calls.append("policy-removed")

    def put_public_access_block(self, **kwargs: Any) -> None:
        assert all(kwargs["PublicAccessBlockConfiguration"].values())
        self.calls.append("private")

    def put_bucket_cors(self, **kwargs: Any) -> None:
        assert kwargs["CORSConfiguration"]["CORSRules"][0]["AllowedOrigins"] == [
            "http://localhost:3000"
        ]
        self.calls.append("cors")


def adapter(monkeypatch: pytest.MonkeyPatch, client: object) -> S3ObjectStore:
    import boto3  # type: ignore[import-untyped]

    monkeypatch.setattr(boto3, "client", lambda *_args, **_kwargs: client)
    return S3ObjectStore(
        endpoint_url="http://private",
        public_endpoint_url="http://public",
        region="test",
        bucket="bucket",
        access_key_id="key",
        secret_access_key="secret",
    )


@pytest.mark.asyncio
async def test_s3_adapter_grants_streams_promotes_and_configures_private_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = FakeClient()
    store = adapter(monkeypatch, client)
    upload = await store.create_upload_grant(key="source", content_type="image/png", max_bytes=100)
    assert upload.url == "https://public/upload" and upload.expires_at.tzinfo is UTC
    assert (await store.create_download_grant(key="source")).url == "https://public/download"
    assert (await store.head(key="source")).byte_size == 6
    assert [chunk async for chunk in store.stream(key="source")] == [b"one", b"two"]
    assert client.body.closed
    await store.promote(source_key="source", destination_key="destination-new")
    await store.ensure_private_bucket(["http://localhost:3000"])
    assert {"copy", "policy-removed", "private", "cors"} <= set(client.calls)


@pytest.mark.asyncio
async def test_s3_adapter_supports_policy_private_minio_without_aws_public_access_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MinioClient(FakeClient):
        def put_public_access_block(self, **_kwargs: Any) -> None:
            raise UnsupportedPublicAccessBlock

    client = MinioClient()
    store = adapter(monkeypatch, client)
    await store.ensure_private_bucket(["http://localhost:3000"])
    assert {"policy-removed", "cors"} <= set(client.calls)


class Broken:
    def __getattr__(self, _name: str) -> Any:
        def fail(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("offline")

        return fail


@pytest.mark.asyncio
async def test_s3_adapter_translates_provider_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    store = adapter(monkeypatch, Broken())
    with pytest.raises(ObjectStoreUnavailable):
        await store.create_upload_grant(key="k", content_type="image/png", max_bytes=1)
    with pytest.raises(ObjectStoreUnavailable):
        await store.create_download_grant(key="k")
    with pytest.raises(ObjectStoreUnavailable):
        await store.head(key="k")
    with pytest.raises(ObjectStoreUnavailable):
        [chunk async for chunk in store.stream(key="k")]
    with pytest.raises(ObjectStoreUnavailable):
        await store.promote(source_key="source", destination_key="destination")
    with pytest.raises(ObjectStoreUnavailable):
        await store.ensure_private_bucket([])


@pytest.mark.asyncio
async def test_bootstrap_retries_then_configures_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    class BootStore:
        attempts = 0

        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def ensure_private_bucket(self, origins: list[str]) -> None:
            assert origins == ["http://localhost:3000"]
            self.attempts += 1
            if self.attempts == 1:
                raise ObjectStoreUnavailable

    settings = SimpleNamespace(
        object_storage_endpoint_url="http://private",
        object_storage_public_endpoint_url="http://public",
        object_storage_region="test",
        object_storage_bucket="bucket",
        object_storage_access_key_id="key",
        object_storage_secret_access_key=SimpleNamespace(get_secret_value=lambda: "secret"),
        cors_origins=["http://localhost:3000"],
    )
    monkeypatch.setattr(bootstrap, "S3ObjectStore", BootStore)
    monkeypatch.setattr(bootstrap, "get_settings", lambda: settings)

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_sleep)
    await bootstrap.main()
