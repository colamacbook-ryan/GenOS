"""Máy chủ MCP giả (JSON-RPC 2.0 qua HTTP, transport `streamable_http`) cho giai đoạn 5.3 luồng 8.

Hai tool:
- `list_customer` — readOnlyHint=true → `gh.mcp_api.routes.discover_tools` gán access='read'.
- `update_crm` — không có readOnlyHint (coi như ghi) → access='write', mọi lời gọi phải qua Bàn làm việc trước
  (khoá cứng #4, `gh.mcp_api.routes.call_tool`), máy chủ này không bao giờ thấy `tools/call` của tool ghi thật
  (route tạo `action_drafts` rồi dừng, không gọi ra ngoài) — chỉ phục vụ `tools/call` của tool đọc.
"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

TOOLS = [
    {"name": "list_customer", "description": "Tra cứu khách hàng theo mã", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "update_crm", "description": "Ghi/ cập nhật CRM ngoài", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": False}},
]


class H(BaseHTTPRequestHandler):
    def _send(self, result, code=200):
        b = json.dumps({"jsonrpc": "2.0", "id": "gh-1", "result": result}).encode()
        self.send_response(code); self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        method = body.get("method")
        if method == "tools/list":
            self._send({"tools": TOOLS})
        elif method == "tools/call":
            name = (body.get("params") or {}).get("name")
            args = (body.get("params") or {}).get("arguments") or {}
            self._send({"content": [{"type": "text", "text": f"Kết quả giả cho {name}({args})"}], "isError": False})
        else:
            self._send({"error": f"method lạ: {method}"}, 400)

    def log_message(self, *a): pass


HTTPServer(("127.0.0.1", 9913), H).serve_forever()
