"""File storage: local disk (signed URLs served by the app) or S3 (presigned URLs)."""
import mimetypes
import shutil
import uuid
from pathlib import Path

import httpx

from . import config
from .security import sign_path


def new_key(user_id: str, kind: str, ext: str) -> str:
    return f"u/{user_id}/{kind}/{uuid.uuid4().hex}.{ext.lstrip('.')}"


class LocalStorage:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("bad key")
        return p

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key: str, src: Path) -> None:
        p = self.path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, p)

    def get_bytes(self, key: str) -> bytes:
        return self.path(key).read_bytes()

    def local_path(self, key: str) -> Path:
        return self.path(key)

    def url(self, key: str, download_name: str | None = None) -> str:
        u = sign_path(key)
        return u + (f"&dl={download_name}" if download_name else "")

    def size(self, key: str) -> int:
        return self.path(key).stat().st_size


class S3Storage:
    def __init__(self):
        import boto3

        self.s3 = boto3.client("s3", region_name=config.S3_REGION)
        self.bucket = config.S3_BUCKET

    def _k(self, key: str) -> str:
        return config.S3_PREFIX + key

    def put_bytes(self, key: str, data: bytes) -> None:
        ctype = mimetypes.guess_type(key)[0] or "application/octet-stream"
        if key.endswith(".glb"):
            ctype = "model/gltf-binary"
        self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType=ctype)

    def put_file(self, key: str, src: Path) -> None:
        self.put_bytes(key, Path(src).read_bytes())

    def get_bytes(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def local_path(self, key: str) -> Path:
        tmp = Path("/tmp/chat3d-cache") / key
        if not tmp.exists():
            tmp.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_bytes(self.get_bytes(key))
        return tmp

    def url(self, key: str, download_name: str | None = None) -> str:
        params = {"Bucket": self.bucket, "Key": self._k(key)}
        if download_name:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_name}"'
        return self.s3.generate_presigned_url("get_object", Params=params, ExpiresIn=config.SIGNED_URL_TTL)

    def size(self, key: str) -> int:
        return self.s3.head_object(Bucket=self.bucket, Key=self._k(key))["ContentLength"]


storage = S3Storage() if config.STORAGE_BACKEND == "s3" else LocalStorage(config.LOCAL_STORAGE_DIR)


def fetch_url(url: str, max_mb: int = 200) -> bytes:
    """Download a provider output (their URLs expire, so we keep our own copy)."""
    with httpx.stream("GET", url, timeout=120, follow_redirects=True) as r:
        r.raise_for_status()
        chunks, total = [], 0
        for chunk in r.iter_bytes():
            total += len(chunk)
            if total > max_mb * 1024 * 1024:
                raise ValueError("Provider output too large")
            chunks.append(chunk)
    return b"".join(chunks)
