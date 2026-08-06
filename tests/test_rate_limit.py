from app.rate_limit import (
    DEFAULT_TENANT_ID,
    evaluate_fixed_window,
    normalize_tenant_id,
    rate_limit_headers,
)


def test_normalize_defaults_when_missing():
    assert normalize_tenant_id(None) == DEFAULT_TENANT_ID
    assert normalize_tenant_id("") == DEFAULT_TENANT_ID
    assert normalize_tenant_id("   ") == DEFAULT_TENANT_ID


def test_normalize_accepts_safe_ids():
    assert normalize_tenant_id("acme") == "acme"
    assert normalize_tenant_id("Acme_Corp-1") == "Acme_Corp-1"
    assert normalize_tenant_id("tenant.demo") == "tenant.demo"


def test_normalize_rejects_unsafe_ids():
    assert normalize_tenant_id("../etc") == DEFAULT_TENANT_ID
    assert normalize_tenant_id("a/b") == DEFAULT_TENANT_ID
    assert normalize_tenant_id("has space") == DEFAULT_TENANT_ID
    assert normalize_tenant_id("x" * 65) == DEFAULT_TENANT_ID


def test_evaluate_allows_under_limit():
    d = evaluate_fixed_window(3, limit=5, window_seconds=60, now=100)
    assert d.allowed is True
    assert d.remaining == 2
    assert d.retry_after == 0
    assert d.reset_at == 120


def test_evaluate_blocks_over_limit():
    d = evaluate_fixed_window(6, limit=5, window_seconds=60, now=100)
    assert d.allowed is False
    assert d.remaining == 0
    assert d.retry_after == 20
    assert d.reset_at == 120


def test_evaluate_limit_zero_disables():
    d = evaluate_fixed_window(999, limit=0, window_seconds=60, now=100)
    assert d.allowed is True
    assert d.limit == 0


def test_rate_limit_headers_include_retry_when_blocked():
    d = evaluate_fixed_window(10, limit=5, window_seconds=60, now=90)
    headers = rate_limit_headers(d, "acme")
    assert headers["X-Tenant-Id"] == "acme"
    assert headers["X-RateLimit-Limit"] == "5"
    assert headers["X-RateLimit-Remaining"] == "0"
    assert headers["Retry-After"] == "30"
    assert headers["X-RateLimit-Reset"] == "120"


def test_rate_limit_headers_omit_retry_when_allowed():
    d = evaluate_fixed_window(1, limit=5, window_seconds=60, now=90)
    headers = rate_limit_headers(d, "acme")
    assert "Retry-After" not in headers
    assert headers["X-RateLimit-Remaining"] == "4"
