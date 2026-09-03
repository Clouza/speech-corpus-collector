from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


API_KEY_PREFIX = "cps_"
API_KEY_RANDOM_LENGTH = 22
MINIMUM_SECRET_LENGTH = 32
MAXIMUM_TTL_DAYS = 365
MAXIMUM_KEY_STORE_BYTES = 1_000_000
MAXIMUM_KEY_COUNT = 10_000
_KEY_PATTERN = re.compile(
    rf"^cps_[A-Za-z0-9_-]{{{API_KEY_RANDOM_LENGTH}}}$"
)
_KEY_DIGEST_PATTERN = re.compile(r"^[a-f0-9]{64}$")
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ApiKeyError(ValueError):
    """Raised when an API key or signing secret is invalid."""


class ApiKeyStoreError(RuntimeError):
    """Raised when the API key registry cannot be read or written safely."""


@dataclass(frozen=True)
class ApiKeyClaims:
    name: str
    issued_at: int
    expires_at: int


def validate_secret(secret: str | None) -> str:
    normalized = secret.strip() if secret else ""
    if len(normalized.encode("utf-8")) < MINIMUM_SECRET_LENGTH:
        raise ApiKeyError(
            f"CORPUS_API_SECRET Must Contain at Least {MINIMUM_SECRET_LENGTH} Bytes"
        )
    return normalized


def validate_key_name(name: str) -> str:
    normalized = name.strip()
    if not _NAME_PATTERN.fullmatch(normalized):
        raise ApiKeyError(
            "Key Name Must Be 1-64 Characters and Use Only Letters, Numbers, "
            "Dot, Underscore, or Hyphen"
        )
    return normalized


def generate_api_key(
    secret: str,
    name: str,
    key_store_path: Path,
    expires_in_days: int = 30,
    *,
    now: int | None = None,
) -> str:
    signing_secret = validate_secret(secret)
    key_name = validate_key_name(name)
    if not 1 <= expires_in_days <= MAXIMUM_TTL_DAYS:
        raise ApiKeyError(
            f"Expiration Must Be Between 1 and {MAXIMUM_TTL_DAYS} Days"
        )

    issued_at = int(time.time()) if now is None else now
    expires_at = issued_at + (expires_in_days * 24 * 60 * 60)
    records = [
        record
        for record in _load_key_store(key_store_path)
        if record["expires_at"] > issued_at
    ]
    if len(records) >= MAXIMUM_KEY_COUNT:
        raise ApiKeyStoreError("API Key Registry Has Reached Its Limit")

    existing_digests = {record["digest"] for record in records}
    for _ in range(5):
        api_key = f"{API_KEY_PREFIX}{secrets.token_urlsafe(16)}"
        digest = _key_digest(api_key, signing_secret)
        if digest not in existing_digests:
            break
    else:
        raise ApiKeyStoreError("Could Not Generate a Unique API Key")

    records.append(
        {
            "digest": digest,
            "name": key_name,
            "issued_at": issued_at,
            "expires_at": expires_at,
        }
    )
    _write_key_store(key_store_path, records)

    # fail closed if persistence and request-time validation ever diverge
    validate_api_key(api_key, signing_secret, key_store_path, now=issued_at)
    return api_key


def validate_api_key(
    api_key: str,
    secret: str,
    key_store_path: Path,
    *,
    now: int | None = None,
) -> ApiKeyClaims:
    signing_secret = validate_secret(secret)
    if not _KEY_PATTERN.fullmatch(api_key or ""):
        raise ApiKeyError("Invalid API Key")

    expected_digest = _key_digest(api_key, signing_secret)
    current_time = int(time.time()) if now is None else now
    for record in _load_key_store(key_store_path):
        if not hmac.compare_digest(record["digest"], expected_digest):
            continue
        if current_time >= record["expires_at"]:
            raise ApiKeyError("Expired API Key")
        return ApiKeyClaims(
            name=record["name"],
            issued_at=record["issued_at"],
            expires_at=record["expires_at"],
        )
    raise ApiKeyError("Invalid API Key")


def _key_digest(api_key: str, secret: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        api_key.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()


def _load_key_store(key_store_path: Path) -> list[dict[str, Any]]:
    if not key_store_path.exists():
        return []
    try:
        if key_store_path.stat().st_size > MAXIMUM_KEY_STORE_BYTES:
            raise ApiKeyStoreError("API Key Registry Is Too Large")
        payload = json.loads(key_store_path.read_text(encoding="utf-8"))
    except ApiKeyStoreError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise ApiKeyStoreError("API Key Registry Cannot Be Read") from exc

    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ApiKeyStoreError("API Key Registry Has an Unsupported Format")
    records = payload.get("keys")
    if not isinstance(records, list) or len(records) > MAXIMUM_KEY_COUNT:
        raise ApiKeyStoreError("API Key Registry Has an Invalid Key List")
    return [_validate_record(record) for record in records]


def _validate_record(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise ApiKeyStoreError("API Key Registry Contains an Invalid Record")
    digest = record.get("digest")
    name = record.get("name")
    issued_at = record.get("issued_at")
    expires_at = record.get("expires_at")
    if (
        not isinstance(digest, str)
        or not _KEY_DIGEST_PATTERN.fullmatch(digest)
        or not isinstance(name, str)
        or not _NAME_PATTERN.fullmatch(name)
        or not isinstance(issued_at, int)
        or isinstance(issued_at, bool)
        or not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or expires_at <= issued_at
    ):
        raise ApiKeyStoreError("API Key Registry Contains an Invalid Record")
    return {
        "digest": digest,
        "name": name,
        "issued_at": issued_at,
        "expires_at": expires_at,
    }


def _write_key_store(
    key_store_path: Path,
    records: list[dict[str, Any]],
) -> None:
    payload = json.dumps(
        {"version": 1, "keys": records},
        indent=2,
        sort_keys=True,
    )
    temporary_path = key_store_path.with_name(
        f".{key_store_path.name}.{secrets.token_hex(6)}.tmp"
    )
    try:
        key_store_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_text(payload + "\n", encoding="utf-8")
        temporary_path.replace(key_store_path)
    except OSError as exc:
        raise ApiKeyStoreError("API Key Registry Cannot Be Written") from exc
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
