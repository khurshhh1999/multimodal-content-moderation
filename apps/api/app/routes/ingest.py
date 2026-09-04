from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile
from opentelemetry import trace

from ..config import get_settings
from ..ingest_service import accept_image, apply_rate_limit_headers
from ..rate_limit import (
    check_tenant_rate_limit,
    normalize_tenant_id,
    rate_limit_headers,
)
from ..schemas import IngestResponse, IngestUrlRequest
from ..url_fetch import ImageFetchError, UnsafeImageUrl, fetch_image_bytes

router = APIRouter(prefix="/v1", tags=["ingest"])


async def _rate_limit(response: Response, tenant_id: str):
    settings = get_settings()
    decision = await check_tenant_rate_limit(
        tenant_id,
        limit=settings.rate_limit_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    apply_rate_limit_headers(response, rate_limit_headers(decision, tenant_id))
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail=(
                f"Rate limit exceeded for tenant '{tenant_id}': "
                f"{settings.rate_limit_requests} requests per "
                f"{settings.rate_limit_window_seconds}s"
            ),
            headers=rate_limit_headers(decision, tenant_id),
        )


@router.post("/content", response_model=IngestResponse)
async def ingest_content(
    response: Response,
    image: Annotated[UploadFile, File(...)],
    caption: Annotated[str, Form()] = "",
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    settings = get_settings()
    tenant_id = normalize_tenant_id(x_tenant_id, default=settings.default_tenant_id)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("moderation.tenant_id", tenant_id)
        span.set_attribute("moderation.ingest_source", "multipart")

    await _rate_limit(response, tenant_id)

    content_type = image.content_type or "application/octet-stream"
    body = await image.read()
    result = await accept_image(
        settings=settings,
        body=body,
        caption=caption,
        content_type=content_type,
        tenant_id=tenant_id,
        source="api",
    )
    if span.is_recording():
        span.set_attribute("moderation.job_id", str(result.job_id))
        span.set_attribute("moderation.content_id", str(result.content_id))
        span.set_attribute("moderation.content_hash", result.content_hash)
        span.set_attribute("moderation.deduplicated", result.deduplicated)
    return result


@router.post("/content/url", response_model=IngestResponse)
async def ingest_content_from_url(
    payload: IngestUrlRequest,
    response: Response,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> IngestResponse:
    settings = get_settings()
    tenant_id = normalize_tenant_id(x_tenant_id, default=settings.default_tenant_id)
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute("moderation.tenant_id", tenant_id)
        span.set_attribute("moderation.ingest_source", "url")

    await _rate_limit(response, tenant_id)

    try:
        body, content_type = fetch_image_bytes(
            str(payload.image_url),
            max_bytes=settings.max_upload_bytes,
            timeout=settings.ingest_url_timeout_seconds,
        )
    except UnsafeImageUrl as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ImageFetchError as exc:
        detail = str(exc)
        status = 413 if "exceeds max" in detail else 502
        raise HTTPException(status_code=status, detail=detail) from exc

    result = await accept_image(
        settings=settings,
        body=body,
        caption=payload.caption,
        content_type=content_type,
        tenant_id=tenant_id,
        source="url",
    )
    if span.is_recording():
        span.set_attribute("moderation.job_id", str(result.job_id))
        span.set_attribute("moderation.content_id", str(result.content_id))
        span.set_attribute("moderation.content_hash", result.content_hash)
        span.set_attribute("moderation.deduplicated", result.deduplicated)
    return result
