"""Danh sách tên miền được phép + chặn địa chỉ nội bộ — dùng chung cho trình duyệt (chặn yêu cầu trong Playwright) và
proxy ra ngoài (`ghb.egress`). Hai lớp độc lập: kể cả khi một lớp lỗi, lớp kia vẫn chặn.
"""

import ipaddress
from urllib.parse import urlsplit

# Luôn cho phép (trang trắng/dữ liệu nội tuyến của chính trình duyệt, không ra mạng).
LOCAL_SCHEMES = ("about", "data", "blob")


def host_allowed(host: str | None, domains: tuple[str, ...] | list[str]) -> bool:
    if not host:
        return False
    h = host.lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in domains)


def url_allowed(url: str, domains: tuple[str, ...] | list[str]) -> bool:
    """Chỉ https tới đúng tên miền nền tảng (và về trang trắng nội bộ). http thường, IP trần, cổng lạ → chặn."""
    try:
        u = urlsplit(url)
    except ValueError:
        return False
    if u.scheme in LOCAL_SCHEMES:
        return True
    if u.scheme not in ("https", "wss"):
        return False
    if u.port not in (None, 443):
        return False
    return host_allowed(u.hostname, domains)


def ip_forbidden(ip: str) -> bool:
    """IP nội bộ/riêng/loopback/link-local/multicast/dự trữ → cấm (chống dùng trình duyệt để dò mạng nội bộ)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved
            or addr.is_unspecified or not addr.is_global)
