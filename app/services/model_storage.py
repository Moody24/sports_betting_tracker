"""Model artifact storage helpers.

Supports local filesystem paths and optional S3-backed storage.
"""

import hashlib
import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MODEL_STORAGE_LOCAL = "local"
MODEL_STORAGE_S3 = "s3"
_CACHE_DIR = "/tmp/model_cache"


def storage_mode() -> str:
    return (os.getenv("MODEL_STORAGE", MODEL_STORAGE_LOCAL) or MODEL_STORAGE_LOCAL).lower()


def _parse_s3_uri(uri: str) -> Tuple[str, str]:
    if not uri.startswith("s3://"):
        raise ValueError("Not an S3 URI")
    bucket_and_key = uri[5:]
    if "/" not in bucket_and_key:
        raise ValueError("Invalid S3 URI")
    bucket, key = bucket_and_key.split("/", 1)
    return bucket, key


def _s3_uri(bucket: str, key: str) -> str:
    return f"s3://{bucket}/{key}"


def _get_s3_client():
    import boto3

    region = os.getenv("AWS_REGION") or None
    return boto3.client("s3", region_name=region)


def _build_s3_key(filename: str) -> str:
    prefix = (os.getenv("S3_MODEL_PREFIX", "models/") or "").strip()
    if prefix and not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return f"{prefix}{filename}" if prefix else filename


def persist_model_artifact(local_path: str, filename: str) -> str:
    """Persist a trained model artifact and return its stored path/URI.

    In local mode returns ``local_path``.
    In S3 mode uploads the file and returns ``s3://bucket/key``.
    Falls back to ``local_path`` on upload errors.
    """
    if storage_mode() != MODEL_STORAGE_S3:
        return local_path

    bucket = (os.getenv("S3_MODEL_BUCKET") or "").strip()
    if not bucket:
        logger.warning("MODEL_STORAGE=s3 but S3_MODEL_BUCKET is not set; using local model path")
        return local_path

    key = _build_s3_key(filename)
    try:
        client = _get_s3_client()
        client.upload_file(local_path, bucket, key)
        return _s3_uri(bucket, key)
    except Exception as exc:
        logger.error("Failed to upload model artifact to S3 (%s/%s): %s", bucket, key, exc)
        return local_path


def materialize_model_artifact(path_ref: str) -> Optional[str]:
    """Resolve a stored model reference to a local readable file path."""
    if not path_ref:
        return None

    # Backward-compatible local path mode.
    if not str(path_ref).startswith("s3://"):
        return path_ref if os.path.exists(path_ref) else None

    try:
        bucket, key = _parse_s3_uri(path_ref)
    except ValueError:
        return None

    os.makedirs(_CACHE_DIR, exist_ok=True)
    suffix = os.path.splitext(key)[1] or ".bin"
    cache_name = hashlib.sha256(path_ref.encode("utf-8")).hexdigest() + suffix
    cached_path = os.path.join(_CACHE_DIR, cache_name)
    if os.path.exists(cached_path):
        return cached_path

    try:
        client = _get_s3_client()
        client.download_file(bucket, key, cached_path)
        return cached_path if os.path.exists(cached_path) else None
    except Exception as exc:
        logger.error("Failed to download model artifact from S3 (%s/%s): %s", bucket, key, exc)
        return None
