import asyncio

from creative_marketer.infrastructure.object_storage.s3 import S3ObjectStore
from creative_marketer_api.config import get_settings


async def main() -> None:
    settings = get_settings()
    store = S3ObjectStore(
        endpoint_url=str(settings.object_storage_endpoint_url),
        public_endpoint_url=str(settings.object_storage_public_endpoint_url),
        region=settings.object_storage_region,
        bucket=settings.object_storage_bucket,
        access_key_id=settings.object_storage_access_key_id,
        secret_access_key=settings.object_storage_secret_access_key.get_secret_value(),
    )
    for attempt in range(30):
        try:
            await store.ensure_private_bucket(
                [str(origin).rstrip("/") for origin in settings.cors_origins]
            )
            return
        except Exception:
            if attempt == 29:
                raise
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
