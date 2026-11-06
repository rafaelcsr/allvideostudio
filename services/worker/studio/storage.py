"""Object storage behind a tiny interface: Cloudflare R2 (S3 API) in production,
local filesystem for tests/CLI. Swapping providers = new class, same methods."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Protocol

from studio.config import Settings


class Storage(Protocol):
    def upload_file(self, path: str | Path, key: str, content_type: str | None = None) -> None: ...
    def download_file(self, key: str, path: str | Path) -> None: ...
    def put_bytes(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get_bytes(self, key: str) -> bytes | None: ...
    def head(self, key: str) -> dict | None: ...
    def presign_put(self, key: str, content_type: str, expires: int = 3600) -> str: ...
    def presign_get(self, key: str, expires: int = 3600, filename: str | None = None) -> str: ...
    def delete_prefix(self, prefix: str, older_than_s: float = 0) -> int: ...
    def total_bytes(self, prefix: str = "") -> int: ...


class R2Storage:
    def __init__(self, s: Settings):
        import boto3
        from botocore.config import Config

        self.bucket = s.r2_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=s.r2_endpoint,
            aws_access_key_id=s.r2_access_key,
            aws_secret_access_key=s.r2_secret_key,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"}),
        )

    def upload_file(self, path, key, content_type=None):
        extra = {"ContentType": content_type} if content_type else None
        self.client.upload_file(str(path), self.bucket, key, ExtraArgs=extra)

    def download_file(self, key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, key, str(path))

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get_bytes(self, key):
        try:
            return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def head(self, key):
        from botocore.exceptions import ClientError

        try:
            r = self.client.head_object(Bucket=self.bucket, Key=key)
            return {"size": r["ContentLength"], "etag": r.get("ETag", "").strip('"'), "content_type": r.get("ContentType")}
        except ClientError:
            return None

    def presign_put(self, key, content_type, expires=3600):
        return self.client.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=expires
        )

    def presign_get(self, key, expires=3600, filename=None):
        params = {"Bucket": self.bucket, "Key": key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        return self.client.generate_presigned_url("get_object", Params=params, ExpiresIn=expires)

    def _iter(self, prefix):
        pag = self.client.get_paginator("list_objects_v2")
        for page in pag.paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents", [])

    def delete_prefix(self, prefix, older_than_s=0):
        now = time.time()
        keys = [o["Key"] for o in self._iter(prefix) if now - o["LastModified"].timestamp() >= older_than_s]
        for i in range(0, len(keys), 1000):
            self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]]})
        return len(keys)

    def total_bytes(self, prefix=""):
        return sum(o["Size"] for o in self._iter(prefix))


class LocalStorage:
    """Filesystem implementation (tests and offline CLI)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("chave inválida")
        return p

    def upload_file(self, path, key, content_type=None):
        dst = self._p(key)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, dst)

    def download_file(self, key, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._p(key), path)

    def put_bytes(self, key, data, content_type="application/octet-stream"):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def get_bytes(self, key):
        p = self._p(key)
        return p.read_bytes() if p.exists() else None

    def head(self, key):
        p = self._p(key)
        return {"size": p.stat().st_size, "etag": str(int(p.stat().st_mtime)), "content_type": None} if p.exists() else None

    def presign_put(self, key, content_type, expires=3600):
        return f"file://{self._p(key)}"

    def presign_get(self, key, expires=3600, filename=None):
        return f"file://{self._p(key)}"

    def delete_prefix(self, prefix, older_than_s=0):
        base = self._p(prefix) if prefix else self.root
        n = 0
        now = time.time()
        for p in list(base.rglob("*")) if base.exists() else []:
            if p.is_file() and now - p.stat().st_mtime >= older_than_s:
                p.unlink()
                n += 1
        return n

    def total_bytes(self, prefix=""):
        base = self._p(prefix) if prefix else self.root
        return sum(p.stat().st_size for p in base.rglob("*") if p.is_file()) if base.exists() else 0
