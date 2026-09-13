from __future__ import annotations

import httpx

from ecochronos_vault.config import Settings

USER_AGENT = "ecochronos-vault/0.1.0"


class IngestError(Exception):
    """Fetch or persist failed."""


class IngestConfigError(IngestError):
    """Settings are insufficient to run ingest."""


def require_openaq_source(settings: Settings) -> None:
    if settings.ingest_source != "openaq":
        raise IngestConfigError(
            f"unsupported ingest source {settings.ingest_source!r}; only 'openaq' is implemented"
        )
    if not (settings.openaq_api_key and settings.openaq_api_key.strip()):
        raise IngestConfigError(
            "OPENAQ_API_KEY is required for OpenAQ v3 (register at https://explore.openaq.org)"
        )


def locations_url(settings: Settings) -> str:
    return f"{settings.openaq_base_url.rstrip('/')}/locations"


def location_query_params(settings: Settings) -> dict[str, str | int]:
    params: dict[str, str | int] = {"limit": settings.openaq_limit}
    if settings.openaq_iso and settings.openaq_iso.strip():
        params["iso"] = settings.openaq_iso.strip().upper()
    return params


def fetch_openaq_locations(settings: Settings, client: httpx.Client) -> httpx.Response:
    require_openaq_source(settings)
    headers = {
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
        "X-API-Key": settings.openaq_api_key.strip(),
    }
    try:
        response = client.get(
            locations_url(settings),
            headers=headers,
            params=location_query_params(settings),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise IngestError(f"OpenAQ request failed: {exc}") from exc
    return response
