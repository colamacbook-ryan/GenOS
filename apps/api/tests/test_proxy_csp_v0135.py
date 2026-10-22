"""F-5 (v0.1.35): CSP của proxy Caddy (lớp quyết định) và nginx đủ chặt;
compose buộc tạo lại proxy khi Caddyfile đổi."""

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).parents[3]
CADDYFILE = ROOT / "deploy" / "proxy" / "Caddyfile"


def _directives(csp: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in csp.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = tokens[1:]
    return out


def _csp_of(path: Path) -> dict[str, list[str]]:
    m = re.search(r'Content-Security-Policy\s+"([^"]+)"', path.read_text())
    assert m, f"{path.name} thiếu Content-Security-Policy"
    return _directives(m.group(1))


def test_caddy_csp_locked_down() -> None:
    d = _csp_of(CADDYFILE)
    assert d["script-src"] == ["'self'"]
    assert d["object-src"] == ["'none'"]
    assert d["base-uri"] == ["'none'"]
    assert d["form-action"] == ["'self'"]
    assert d["frame-ancestors"] == ["'none'"]
    assert "'self'" in d["connect-src"]
    # Không có nguồn WebSocket mở toang; chỉ đúng host:port đang phục vụ (placeholder Caddy).
    assert not {"wss:", "ws:", "*", "https:"} & set(d["connect-src"])
    assert all(t == "'self'" or t == "wss://{http.request.hostport}" for t in d["connect-src"])


def test_caddy_keeps_upstream_csp_and_sends_single_csp() -> None:
    """CSP của Caddy nằm trong khối `header` (có `-Server` ⇒ deferred) nên nếu ĐẶT thẳng sẽ GHI ĐÈ CSP riêng của
    tệp tài liệu từ api (sandbox / PDF). `?` = chỉ đặt khi upstream chưa có. Riêng web (nginx có CSP riêng,
    `connect-src 'self'`) thì bỏ CSP upstream bằng `header_down` để trình duyệt chỉ nhận MỘT CSP — của Caddy."""
    text = CADDYFILE.read_text()
    assert re.search(r'^\s*\?Content-Security-Policy\s+"', text, re.M), "CSP Caddy phải là `?Content-Security-Policy`"
    assert not re.search(r'^\s*Content-Security-Policy\s+"', text, re.M)
    web = re.search(r"reverse_proxy web:8080 \{([^}]*)\}", text)
    assert web and "header_down -Content-Security-Policy" in web.group(1)
    api = re.search(r"reverse_proxy api:8000([^\n]*)", text)
    assert api and "header_down" not in api.group(1)


def test_compose_label_tracks_caddyfile_hash() -> None:
    sha = hashlib.sha256(CADDYFILE.read_bytes()).hexdigest()[:12]
    compose = (ROOT / "deploy" / "compose.yaml").read_text()
    m = re.search(r'gh\.caddyfile-sha:\s*"([0-9a-f]+)"', compose)
    assert m, "deploy/compose.yaml thiếu nhãn gh.caddyfile-sha cho service proxy"
    assert m.group(1) == sha, f"Sửa Caddyfile thì đổi nhãn gh.caddyfile-sha thành {sha} (và chép sang bản nhúng genh)"


def test_nginx_csp_no_open_websocket() -> None:
    nginx = ROOT / "apps" / "web" / "nginx.conf"
    d = _csp_of(nginx)
    assert "wss:" not in nginx.read_text()
    assert "ws:" not in d["connect-src"] and "'self'" in d["connect-src"]
    assert d["script-src"] == ["'self'"] and d["object-src"] == ["'none'"] and d["base-uri"] == ["'none'"]


def test_caddy_xfo_conditional_so_portable_frame_can_read_errors() -> None:
    """v0.1.40 (F-12): `?X-Frame-Options` — api đặt SAMEORIGIN cho riêng /system/offsite/portable (khung tải ẩn cùng gốc
    phải đọc được trang lỗi JSON); mọi phản hồi khác vẫn DENY. Đặt thẳng (không `?`) sẽ ghi đè và chặn trang lỗi."""
    text = CADDYFILE.read_text()
    assert re.search(r'^\s*\?X-Frame-Options\s+"DENY"', text, re.M)
    assert not re.search(r'^\s*X-Frame-Options\s+"', text, re.M)
