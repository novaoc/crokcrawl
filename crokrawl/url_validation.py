"""URL safety checks — prevents SSRF attacks.

Blocks requests to private/internal network addresses and cloud
metadata endpoints. Mirrors the approach in Hermes agent's url_safety.py
but is self-contained (no hermes_cli dependency).
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cloud metadata hostnames — always blocked
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.com",
})

# Cloud metadata IPs and link-local range — always blocked
_ALWAYS_BLOCKED_IPS = frozenset({
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("169.254.170.2"),
    ipaddress.ip_address("169.254.169.253"),
    ipaddress.ip_address("fd00:ec2::254"),
    ipaddress.ip_address("100.100.100.200"),
})

_ALWAYS_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),
)

# CGNAT range
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def is_safe_url(url: str) -> bool:
    """Return True if the URL target is not a private/internal address.

    Resolves hostname to IP and checks against private ranges.
    Fails closed: DNS errors and exceptions block the request.
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()

        if not hostname:
            return False

        # Block known internal hostnames — ALWAYS
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        # Try to resolve
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            # Always block cloud metadata
            if ip in _ALWAYS_BLOCKED_IPS or any(ip in net for net in _ALWAYS_BLOCKED_NETWORKS):
                logger.warning("Blocked request to cloud metadata address: %s -> %s", hostname, ip_str)
                return False

            # Block private/loopback/link-local/multicast/CGNAT
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                logger.warning("Blocked request to private/internal address: %s -> %s", hostname, ip_str)
                return False
            if ip.is_multicast or ip.is_unspecified:
                logger.warning("Blocked request to reserved address: %s -> %s", hostname, ip_str)
                return False
            if ip in _CGNAT_NETWORK:
                logger.warning("Blocked request to CGNAT address: %s -> %s", hostname, ip_str)
                return False

        return True

    except Exception as exc:
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False


def is_safe_redirect_url(url: str) -> bool:
    """Validate a redirect target URL.

    Same checks as is_safe_url, plus scheme validation (rejects non-http schemes).
    Used by the redirect event hook to prevent redirect-based SSRF bypass.
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            logger.warning("Blocked redirect to non-HTTP scheme: %s", url)
            return False
        return is_safe_url(url)
    except Exception:
        return False
