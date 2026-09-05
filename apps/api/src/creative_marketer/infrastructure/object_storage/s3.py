import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from creative_marketer.catalog.asset_application import (
    DownloadGrant,
    ObjectMetadata,
    ObjectStoreUnavailable,
    UploadGrant,
)


class S3ObjectStore:
    """S3-compatible private object store; boto3 is confined to this adapter."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        public_endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        upload_ttl_seconds: int = 900,
        download_ttl_seconds: int = 600,
    ) -> None:
        try:
            import boto3  # type: ignore[import-untyped]
            from botocore.config import Config  # type: ignore[import-untyped]
        except ImportError as error:  # pragma: no cover - packaging guard
            raise ObjectStoreUnavailable("S3 adapter dependency is unavailable") from error
        options: dict[str, Any] = {
            "region_name": region,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": Config(signature_version="s3v4", s3={"addressing_style": "path"}),
        }
        self._client: Any = boto3.client("s3", endpoint_url=endpoint_url, **options)
        self._grant_client: Any = boto3.client("s3", endpoint_url=public_endpoint_url, **options)
        self._bucket = bucket
        self._region = region
        self._upload_ttl = upload_ttl_seconds
        self._download_ttl = download_ttl_seconds

    async def create_upload_grant(
        self, *, key: str, content_type: str, max_bytes: int
    ) -> UploadGrant:
        try:
            value = await asyncio.to_thread(
                self._grant_client.generate_presigned_post,
                Bucket=self._bucket,
                Key=key,
                Fields={"Content-Type": content_type},
                Conditions=[
                    {"Content-Type": content_type},
                    ["content-length-range", 1, max_bytes],
                ],
                ExpiresIn=self._upload_ttl,
            )
        except Exception as error:
            raise ObjectStoreUnavailable("object storage is unavailable") from error
        return UploadGrant(
            url=str(value["url"]),
            fields={str(k): str(v) for k, v in value["fields"].items()},
            expires_at=datetime.now(UTC) + timedelta(seconds=self._upload_ttl),
        )

    async def create_download_grant(self, *, key: str) -> DownloadGrant:
        try:
            url = await asyncio.to_thread(
                self._grant_client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=self._download_ttl,
            )
        except Exception as error:
            raise ObjectStoreUnavailable("object storage is unavailable") from error
        return DownloadGrant(
            url=str(url), expires_at=datetime.now(UTC) + timedelta(seconds=self._download_ttl)
        )

    async def head(self, *, key: str) -> ObjectMetadata:
        try:
            value = await asyncio.to_thread(self._client.head_object, Bucket=self._bucket, Key=key)
        except Exception as error:
            raise ObjectStoreUnavailable("uploaded object is unavailable") from error
        return ObjectMetadata(
            byte_size=int(value["ContentLength"]), content_type=value.get("ContentType")
        )

    async def stream(self, *, key: str) -> AsyncIterator[bytes]:
        try:
            value = await asyncio.to_thread(self._client.get_object, Bucket=self._bucket, Key=key)
            body = value["Body"]
            while True:
                chunk = await asyncio.to_thread(body.read, 1024 * 1024)
                if not chunk:
                    break
                yield bytes(chunk)
            await asyncio.to_thread(body.close)
        except Exception as error:
            raise ObjectStoreUnavailable("object storage stream failed") from error

    async def promote(self, *, source_key: str, destination_key: str) -> None:
        try:
            try:
                await asyncio.to_thread(
                    self._client.head_object, Bucket=self._bucket, Key=destination_key
                )
            except Exception:
                pass
            else:
                raise ObjectStoreUnavailable("immutable destination already exists")
            await asyncio.to_thread(
                self._client.copy_object,
                Bucket=self._bucket,
                Key=destination_key,
                CopySource={"Bucket": self._bucket, "Key": source_key},
                MetadataDirective="COPY",
            )
        except ObjectStoreUnavailable:
            raise
        except Exception as error:
            raise ObjectStoreUnavailable("object promotion failed") from error

    async def ensure_private_bucket(self, cors_origins: list[str]) -> None:
        try:
            create: dict[str, object] = {"Bucket": self._bucket}
            if self._region != "us-east-1":
                create["CreateBucketConfiguration"] = {"LocationConstraint": self._region}
            await asyncio.to_thread(self._client.create_bucket, **create)
        except Exception as error:
            code = getattr(error, "response", {}).get("Error", {}).get("Code")
            if code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise ObjectStoreUnavailable("could not initialize object storage") from error
        try:
            try:
                await asyncio.to_thread(self._client.delete_bucket_policy, Bucket=self._bucket)
            except Exception as error:
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code not in {"NoSuchBucketPolicy", "NoSuchPolicy"}:
                    raise
            try:
                await asyncio.to_thread(
                    self._client.put_public_access_block,
                    Bucket=self._bucket,
                    PublicAccessBlockConfiguration={
                        "BlockPublicAcls": True,
                        "IgnorePublicAcls": True,
                        "BlockPublicPolicy": True,
                        "RestrictPublicBuckets": True,
                    },
                )
            except Exception as error:
                # MinIO is policy-private by default and does not implement this
                # AWS-only defense-in-depth API. Its current response is MalformedXML.
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code not in {"MalformedXML", "NotImplemented", "XNotImplemented"}:
                    raise
            try:
                await asyncio.to_thread(
                    self._client.put_bucket_cors,
                    Bucket=self._bucket,
                    CORSConfiguration={
                        "CORSRules": [
                            {
                                "AllowedOrigins": cors_origins,
                                "AllowedMethods": ["GET", "POST"],
                                "AllowedHeaders": ["*"],
                                "ExposeHeaders": ["ETag"],
                                "MaxAgeSeconds": 600,
                            }
                        ]
                    },
                )
            except Exception as error:
                # Community MinIO configures CORS at server scope through
                # MINIO_API_CORS_ALLOW_ORIGIN and rejects the bucket API.
                code = getattr(error, "response", {}).get("Error", {}).get("Code")
                if code not in {"NotImplemented", "XNotImplemented"}:
                    raise
        except Exception as error:
            raise ObjectStoreUnavailable("could not secure object storage") from error
