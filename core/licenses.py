from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LicenseInfo:
    identifier: str
    url: str
    commercial_use_allowed: bool | None
    attribution_required: bool | None
    share_alike_required: bool | None
    status: str


_KNOWN_LICENSES: dict[str, LicenseInfo] = {
    "CC0-1.0": LicenseInfo(
        "CC0-1.0",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        True,
        False,
        False,
        "known",
    ),
    "PUBLIC-DOMAIN": LicenseInfo(
        "Public-Domain",
        "https://creativecommons.org/publicdomain/mark/1.0/",
        True,
        False,
        False,
        "known",
    ),
    "CC-BY-4.0": LicenseInfo(
        "CC-BY-4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        True,
        True,
        False,
        "known",
    ),
    "CC-BY-3.0": LicenseInfo(
        "CC-BY-3.0",
        "https://creativecommons.org/licenses/by/3.0/",
        True,
        True,
        False,
        "known",
    ),
    "CC-BY-SA-4.0": LicenseInfo(
        "CC-BY-SA-4.0",
        "https://creativecommons.org/licenses/by-sa/4.0/",
        True,
        True,
        True,
        "known",
    ),
}


def _normalize(value: str) -> str:
    normalized = value.strip().upper().replace("_", "-").replace(" ", "-")
    while "--" in normalized:
        normalized = normalized.replace("--", "-")
    aliases = {
        "CC0": "CC0-1.0",
        "CC-0": "CC0-1.0",
        "CC-BY-4": "CC-BY-4.0",
        "CC-BY-3": "CC-BY-3.0",
        "CC-BY-SA-4": "CC-BY-SA-4.0",
        "PUBLIC-DOMAIN": "PUBLIC-DOMAIN",
        "PUBLICDOMAIN": "PUBLIC-DOMAIN",
        "PD": "PUBLIC-DOMAIN",
    }
    return aliases.get(normalized, normalized)


def resolve_license(value: str | None) -> LicenseInfo:
    if not value or not value.strip():
        return LicenseInfo("", "", None, None, None, "unknown")
    normalized = _normalize(value)
    known = _KNOWN_LICENSES.get(normalized)
    if known:
        return known
    if "-NC" in normalized or "NONCOMMERCIAL" in normalized or "ALL-RIGHTS-RESERVED" in normalized:
        return LicenseInfo(value.strip(), "", False, None, None, "prohibited")
    return LicenseInfo(value.strip(), "", None, None, None, "unknown")


def download_allowed(info: LicenseInfo, allow_unknown: bool = False) -> bool:
    if info.status == "prohibited":
        return False
    return info.status == "known" or allow_unknown
