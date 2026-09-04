from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.goog",
}


class UnsafeImageUrl(ValueError):
    """Remote URL is not safe to fetch (scheme, host, or private address)."""


class ImageFetchError(RuntimeError):
    """Network or size failure while downloading a remote image."""


def sniff_content_type(data: bytes, header: str | None = None) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if header:
        declared = header.split(";", 1)[0].strip().lower()
        if declared in ALLOWED_IMAGE_TYPES:
            return declared
    raise ImageFetchError("remote bytes are not a supported image type")


def _host_ips(hostname: str, port: int) -> list[ipaddress._BaseAddress]:
    infos = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
    addresses: list[ipaddress._BaseAddress] = []
    for info in infos:
        sockaddr = info[4]
        addresses.append(ipaddress.ip_address(sockaddr[0]))
    return addresses


def is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or (isinstance(ip, ipaddress.IPv4Address) and ip.is_reserved)
    )


def validate_image_url(url: str) -> str:
    """Return a cleaned http(s) URL or raise UnsafeImageUrl."""
    raw = (url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise UnsafeImageUrl("only http and https image URLs are allowed")
    if parsed.username or parsed.password:
        raise UnsafeImageUrl("URLs with credentials are not allowed")
    if not parsed.hostname:
        raise UnsafeImageUrl("image URL is missing a host")
    host = parsed.hostname.lower().rstrip(".")
    if host in _BLOCKED_HOSTS or host.endswith(".localhost"):
        raise UnsafeImageUrl("image URL host is not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and is_blocked_ip(literal):
        raise UnsafeImageUrl("image URL must not target a private address")
    return raw


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise UnsafeImageUrl("image URL redirects are not followed")


def fetch_image_bytes(
    url: str,
    *,
    max_bytes: int,
    timeout: float,
) -> tuple[bytes, str]:
    """Download an image after rejecting private/metadata targets.

    DNS is resolved and every address is checked before the HTTP request so
    literal private IPs and names that resolve to RFC1918 space are blocked.
    """
    cleaned = validate_image_url(url)
    parsed = urlparse(cleaned)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        addresses = _host_ips(host, port)
    except socket.gaierror as exc:
        raise ImageFetchError(f"could not resolve image host: {exc}") from exc
    if not addresses:
        raise ImageFetchError("could not resolve image host")
    if any(is_blocked_ip(ip) for ip in addresses):
        raise UnsafeImageUrl("image URL resolved to a private or reserved address")

    opener = urllib.request.build_opener(_NoRedirect)
    request = urllib.request.Request(
        cleaned,
        method="GET",
        headers={"User-Agent": "moderation-ingest/1.0"},
    )
    try:
        with opener.open(request, timeout=timeout) as resp:
            header_type = resp.headers.get("Content-Type")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise ImageFetchError("remote image exceeds max upload size")
                chunks.append(chunk)
    except UnsafeImageUrl:
        raise
    except ImageFetchError:
        raise
    except urllib.error.HTTPError as exc:
        raise ImageFetchError(f"image URL returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ImageFetchError(f"image URL fetch failed: {exc.reason}") from exc
    except TimeoutError as exc:
        raise ImageFetchError("image URL fetch timed out") from exc
    except OSError as exc:
        raise ImageFetchError(f"image URL fetch failed: {exc}") from exc

    data = b"".join(chunks)
    if not data:
        raise ImageFetchError("remote image was empty")
    content_type = sniff_content_type(data, header_type)
    return data, content_type
