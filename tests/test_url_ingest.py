from __future__ import annotations

import ipaddress
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from app.url_fetch import (
    ImageFetchError,
    UnsafeImageUrl,
    fetch_image_bytes,
    is_blocked_ip,
    sniff_content_type,
    validate_image_url,
)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_sniff_png_jpeg_gif_webp():
    assert sniff_content_type(PNG_MAGIC) == "image/png"
    assert sniff_content_type(b"\xff\xd8\xff" + b"\x00" * 8) == "image/jpeg"
    assert sniff_content_type(b"GIF89a" + b"\x00" * 8) == "image/gif"
    assert sniff_content_type(b"RIFF" + b"\x00" * 4 + b"WEBP") == "image/webp"


def test_sniff_falls_back_to_declared_header():
    assert sniff_content_type(b"not-magic", "image/jpeg; charset=binary") == "image/jpeg"


def test_sniff_rejects_unknown_bytes():
    with pytest.raises(ImageFetchError):
        sniff_content_type(b"PK\x03\x04", "application/zip")


def test_validate_rejects_non_http_and_credentials():
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("file:///etc/passwd")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("ftp://example.com/a.png")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("https://user:pass@example.com/a.png")


def test_validate_rejects_localhost_and_metadata():
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("http://localhost/a.png")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("http://169.254.169.254/latest/meta-data")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("http://127.0.0.1/a.png")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("http://10.0.0.8/a.png")
    with pytest.raises(UnsafeImageUrl):
        validate_image_url("http://[::1]/a.png")


def test_validate_accepts_public_https():
    assert validate_image_url("https://cdn.example.com/ugc/photo.png").startswith("https://")


def test_is_blocked_ip_covers_rfc1918_and_loopback():
    assert is_blocked_ip(ipaddress.ip_address("10.1.2.3"))
    assert is_blocked_ip(ipaddress.ip_address("192.168.0.1"))
    assert is_blocked_ip(ipaddress.ip_address("127.0.0.1"))
    assert is_blocked_ip(ipaddress.ip_address("169.254.1.1"))
    assert not is_blocked_ip(ipaddress.ip_address("8.8.8.8"))


class _FakeResp:
    def __init__(self, payload: bytes, content_type: str = "image/png"):
        self.headers = {"Content-Type": content_type}
        self._buf = BytesIO(payload)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@patch("app.url_fetch._host_ips")
@patch("app.url_fetch.urllib.request.build_opener")
def test_fetch_downloads_when_dns_is_public(mock_opener_factory, mock_host_ips):
    mock_host_ips.return_value = [ipaddress.ip_address("93.184.216.34")]
    opener = MagicMock()
    opener.open.return_value = _FakeResp(PNG_MAGIC)
    mock_opener_factory.return_value = opener

    data, ctype = fetch_image_bytes(
        "https://example.com/a.png",
        max_bytes=1024,
        timeout=2,
    )
    assert ctype == "image/png"
    assert data.startswith(b"\x89PNG")


@patch("app.url_fetch._host_ips")
def test_fetch_rejects_dns_to_private_ip(mock_host_ips):
    mock_host_ips.return_value = [ipaddress.ip_address("10.0.0.9")]
    with pytest.raises(UnsafeImageUrl, match="private"):
        fetch_image_bytes("https://evil.example/a.png", max_bytes=1024, timeout=2)


@patch("app.url_fetch._host_ips")
@patch("app.url_fetch.urllib.request.build_opener")
def test_fetch_rejects_oversize(mock_opener_factory, mock_host_ips):
    mock_host_ips.return_value = [ipaddress.ip_address("1.1.1.1")]
    opener = MagicMock()
    opener.open.return_value = _FakeResp(PNG_MAGIC + b"x" * 50)
    mock_opener_factory.return_value = opener
    with pytest.raises(ImageFetchError, match="exceeds"):
        fetch_image_bytes("https://example.com/a.png", max_bytes=20, timeout=2)
