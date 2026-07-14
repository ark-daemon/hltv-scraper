"""Upload snapshot files to Cloudflare R2 (S3-compatible API)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from loguru import logger

SNAPSHOT_FILES = ("manifest.json", "data.json", "data.csv", "data.parquet")


def _env(name: str, *aliases: str) -> str | None:
    for key in (name, *aliases):
        val = os.environ.get(key)
        if val:
            return val.strip()
    return None


def load_r2_config() -> dict[str, str]:
    """
    Required env:
      R2_ACCOUNT_ID
      R2_ACCESS_KEY_ID
      R2_SECRET_ACCESS_KEY
      R2_BUCKET
      R2_PUBLIC_BASE_URL  (e.g. https://pub-xxx.r2.dev or custom domain, no trailing slash)
    Optional:
      R2_ENDPOINT  (default https://<account>.r2.cloudflarestorage.com)
    """
    account = _env("R2_ACCOUNT_ID", "CF_ACCOUNT_ID")
    key = _env("R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID")
    secret = _env("R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY")
    bucket = _env("R2_BUCKET", "AWS_S3_BUCKET")
    public = _env("R2_PUBLIC_BASE_URL", "R2_PUBLIC_URL")
    missing = [
        n
        for n, v in [
            ("R2_ACCOUNT_ID", account),
            ("R2_ACCESS_KEY_ID", key),
            ("R2_SECRET_ACCESS_KEY", secret),
            ("R2_BUCKET", bucket),
            ("R2_PUBLIC_BASE_URL", public),
        ]
        if not v
    ]
    if missing:
        raise RuntimeError(
            "Missing R2 env vars: "
            + ", ".join(missing)
            + ". Set them then re-run publish."
        )
    endpoint = _env("R2_ENDPOINT") or f"https://{account}.r2.cloudflarestorage.com"
    return {
        "account_id": account,  # type: ignore[dict-item]
        "access_key": key,  # type: ignore[dict-item]
        "secret_key": secret,  # type: ignore[dict-item]
        "bucket": bucket,  # type: ignore[dict-item]
        "public_base": public.rstrip("/"),  # type: ignore[arg-type]
        "endpoint": endpoint.rstrip("/"),
    }


def upload_snapshot(
    *,
    export_dir: Path,
    repo_slug: str,
    content_types: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Upload export/* to R2 keys {repo_slug}/{filename}, overwrite in place.
    Verifies by GETting public manifest and comparing record_count.
    """
    try:
        import boto3
        from botocore.client import Config
    except ImportError as exc:
        raise RuntimeError("boto3 required for R2 publish: pip install boto3") from exc

    cfg = load_r2_config()
    export_dir = Path(export_dir)
    for name in SNAPSHOT_FILES:
        if not (export_dir / name).exists():
            raise FileNotFoundError(f"Missing {export_dir / name}; run snapshot first")

    local_manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_count = int(local_manifest.get("record_count", -1))

    ctypes = content_types or {
        "manifest.json": "application/json; charset=utf-8",
        "data.json": "application/json; charset=utf-8",
        "data.csv": "text/csv; charset=utf-8",
        "data.parquet": "application/vnd.apache.parquet",
    }

    client = boto3.client(
        "s3",
        endpoint_url=cfg["endpoint"],
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )

    uploaded: list[str] = []
    for name in SNAPSHOT_FILES:
        key = f"{repo_slug}/{name}"
        path = export_dir / name
        extra = {
            "ContentType": ctypes.get(name, "application/octet-stream"),
            "CacheControl": "public, max-age=60",
        }
        # Public bucket CORS/policy is bucket-level; ACL may be disabled on R2.
        client.upload_file(
            str(path),
            cfg["bucket"],
            key,
            ExtraArgs=extra,
        )
        uploaded.append(key)
        logger.info("uploaded s3://{}/{}", cfg["bucket"], key)

    public_manifest = f"{cfg['public_base']}/{repo_slug}/manifest.json"
    # Verify over HTTPS
    req = Request(
        public_manifest,
        headers={"User-Agent": "ark-daemon-r2-verify/0.1", "Accept": "application/json"},
    )
    with urlopen(req, timeout=30) as resp:
        body = resp.read().decode("utf-8")
        if resp.status != 200:
            raise RuntimeError(f"manifest GET status {resp.status} for {public_manifest}")
    remote = json.loads(body)
    remote_count = int(remote.get("record_count", -2))
    if remote_count != expected_count:
        raise RuntimeError(
            f"manifest record_count mismatch: local={expected_count} remote={remote_count} url={public_manifest}"
        )

    urls = {name: f"{cfg['public_base']}/{repo_slug}/{name}" for name in SNAPSHOT_FILES}
    logger.info("publish ok {} record_count={}", repo_slug, expected_count)
    return {
        "repo_slug": repo_slug,
        "record_count": expected_count,
        "manifest_url": public_manifest,
        "urls": urls,
        "uploaded_keys": uploaded,
    }
