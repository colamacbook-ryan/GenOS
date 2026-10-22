"""Nền dùng chung của cụm Bản đồ quan hệ: giới hạn ≤ 200 node/lượt (PLAN §3.5). Ngưỡng bucket của màn
`danh sách` (`heat`/`potential`/`churn_risk`, cùng thang 0–100) dùng lại đúng 80/50 mà
`gh.biz.relations.service.heat_bucket` đã dùng cho `heat` — ba chiều điểm cùng thang nên cùng ngưỡng."""

from collections.abc import Callable, Sequence
from typing import Any

MAX_NODES = 200
BUCKET_HIGH, BUCKET_MID = 80, 50


def build_graph(rows: Sequence[Any], node_a: Callable[[Any], dict[str, Any]],
                node_b: Callable[[Any], dict[str, Any]], edge_of: Callable[[Any], dict[str, Any]],
                limit: int = MAX_NODES) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """Dựng {nodes, edges} từ các dòng cạnh đã sắp `weight DESC`: tham lam nhận cạnh nặng nhất trước, bỏ qua
    cạnh nào sẽ đẩy số node vượt `limit` — nên tập trả về luôn là top trọng số cao nhất trong giới hạn, không
    cắt ngẫu nhiên (PLAN §3.5: "≤ 200 node… nếu vượt, trả tập con theo trọng số cao nhất")."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    truncated = False
    for r in rows:
        a, b = node_a(r), node_b(r)
        new_ids = {i for i in (a["id"], b["id"]) if i not in nodes}
        if len(nodes) + len(new_ids) > limit:
            truncated = True
            continue
        nodes[a["id"]] = a
        nodes[b["id"]] = b
        edges.append(edge_of(r))
    return list(nodes.values()), edges, truncated
