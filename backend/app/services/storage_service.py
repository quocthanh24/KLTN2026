from __future__ import annotations

import asyncio
from functools import lru_cache
from typing import BinaryIO
from urllib.parse import quote

import boto3
from botocore.client import BaseClient
from botocore.config import Config

from app.core.config import get_settings


class StorageService:
    """Minimal S3-compatible boundary used by chat business logic."""

    def __init__(self, client: BaseClient, bucket: str) -> None:
        self.client = client
        self.bucket = bucket

    async def check_bucket(self) -> None:
        await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)

    async def upload(self, key: str, body: BinaryIO, content_type: str) -> None:
        body.seek(0)
        await asyncio.to_thread(
            self.client.upload_fileobj, body, self.bucket, key,
            ExtraArgs={"ContentType": content_type},
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self.client.delete_object, Bucket=self.bucket, Key=key)

    async def read_document(self, key: str, max_bytes: int) -> bytes:
        def read():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            try:
                if response["ContentLength"] > max_bytes:
                    raise ValueError("Object exceeds processing limit")
                data = response["Body"].read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("Object exceeds processing limit")
                return data
            finally:
                response["Body"].close()
        return await asyncio.to_thread(read)

    async def temporary_download_url(self, key: str, filename: str) -> str:
        # HTTP header values must not contain raw UTF-8 filename bytes.
        fallback = "".join(c for c in filename if 32 <= ord(c) < 127 and c not in '"\\') or "download"
        disposition = f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": disposition,
            },
            ExpiresIn=300,
        )


@lru_cache
def get_storage_service() -> StorageService:
    settings = get_settings()
    if not all((
        settings.object_storage_endpoint.strip(),
        settings.object_storage_access_key.get_secret_value(),
        settings.object_storage_secret_key.get_secret_value(),
        settings.object_storage_bucket.strip(),
    )):
        raise RuntimeError("Object storage environment variables are required")
    endpoint = settings.object_storage_endpoint.rstrip("/")
    if settings.object_storage_secure and endpoint.startswith("http://"):
        endpoint = "https://" + endpoint.removeprefix("http://")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=settings.object_storage_access_key.get_secret_value(),
        aws_secret_access_key=settings.object_storage_secret_key.get_secret_value(),
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )
    return StorageService(client, settings.object_storage_bucket)
