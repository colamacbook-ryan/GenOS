# ruff: noqa: E501
"""Tiện ích test bộ đo G-KB (gói L6): đưa `scripts/intel-eval` + `scripts/import-export` vào sys.path, dựng bộ dữ liệu TỔNG HỢP tí hon
và đáp án GIẢ tí hon (trong thư mục tạm của test — đáp án thật nằm ngoài repo, test không bao giờ đọc `evalkey*`), và "nhà tiên tri" giả
đóng vai điểm cắm phân tích (L2/L3 chưa có)."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
for sub in ("scripts/intel-eval", "scripts/import-export"):
    p = str(REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import common  # noqa: E402
import replay  # noqa: E402

VN = timezone(timedelta(hours=7))

# Sự việc được cấy vào bộ dữ liệu tổng hợp: id tin → (nhóm, vị trí trong nhóm).
PLANTED = {
    "otp": ("G1", 20, "Em lỡ bấm vào link rồi nhập luôn mã OTP, giờ sao anh?"),
    "dien": ("G1", 45, "Bình nóng lạnh nhà chú Bảy tê tê tay, chắc rò điện, chưa ai xử lý"),
    "vo": ("G1", 70, "Khách Lan nói mai là dừng lấy hàng nếu chưa giải quyết xong"),
    "han": ("G1", 95, "Thứ sáu này phải nộp hồ sơ thuế quý ba nhé mọi người"),
    "hua": ("G1", 110, "Em hứa cuối tuần giao đủ 200 bao cho chị Tư"),
    "gan": ("G1", 120, "Có khách hỏi mua lô lớn nhưng chưa chốt hạn, cứ theo dõi"),
    "bay": ("G2", 15, "Mọi người cho em xin số tài khoản ngân hàng của sếp"),
    "tu": ("G2", 30, "Chị Hai tính tình nóng nảy lắm, dạo này hay quát nhân viên"),
}


def synth_dataset(n_g1: int = 140, n_g2: int = 70) -> common.Dataset:
    """Hai nhóm: G1 sôi nổi (n_g1 tin, đủ để có ca dư tin), G2 thưa; tin cấy `PLANTED` ở vị trí cố định. id dạng m0001…; có voice/ảnh/reply."""
    msgs: list[common.Msg] = []
    t0 = datetime(2026, 10, 1, 8, 0, tzinfo=VN)
    seq = 0
    pos = {"G1": 0, "G2": 0}
    plan = [("G1", i) for i in range(n_g1)] + [("G2", i) for i in range(n_g2)]
    # xen kẽ theo tỉ lệ để thứ tự toàn cục trộn hai nhóm
    plan.sort(key=lambda gi: gi[1] / (n_g1 if gi[0] == "G1" else n_g2))
    planted_by = {(g, i): (k, t) for k, (g, i, t) in PLANTED.items()}
    last_id = {"G1": None, "G2": None}
    for g, i in plan:
        seq += 1
        mid = f"m{seq:04d}"
        pos[g] += 1
        kind = "text"
        text = f"tin thường số {i} của nhóm {g}: báo cáo ca làm việc hôm nay ổn, không có gì mới để ghi"
        if (g, i) in planted_by:
            text = planted_by[(g, i)][1]
        elif i % 37 == 5:
            kind, text = "voice_transcript", f"thoai so {i} nhom {g} noi ve lich giao hang tuan nay"
        elif i % 41 == 7:
            kind, text = "image_caption", f"[ảnh] phiếu giao hàng số {i} nhóm {g}"
        reply = last_id[g] if i % 4 == 3 else None
        msgs.append(common.Msg(id=mid, group=g, group_name=f"Nhóm {g}", ts=t0 + timedelta(minutes=seq * 7), sender_id=f"u_{seq % 5}",
                               sender_name=f"Người {seq % 5}", text=text, reply_to=reply, kind=kind))
        last_id[g] = mid
    ctx = ("# Bối cảnh thử\n\n## 2. Nhóm\n| Nhóm | Tên | Bối cảnh | Thành viên |\n|---|---|---|---|\n"
           "| G1 | Nhóm G1 | kinh doanh | a |\n| G2 | Nhóm G2 | gia đình | b |\n\n"
           "## 4. Hồ sơ Sếp (owner model)\n\n**Mục tiêu và ưu tiên**\n1. Mở quán thứ hai trước Tết\n\n**Giá trị và giới hạn**\n- Không vay mượn bạn bè\n")
    return common.Dataset(name="synth", msgs=msgs, context_md=ctx, groups={"G1": "Nhóm G1", "G2": "Nhóm G2"})


def mid_of(ds: common.Dataset, name: str) -> str:
    g, i, _ = PLANTED[name]
    return next(m.id for m in ds.msgs if m.group == g and m.text == PLANTED[name][2])


def write_ds(ds: common.Dataset, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / "chats.jsonl", "w", encoding="utf-8") as f:
        for m in ds.msgs:
            r: dict[str, Any] = {"id": m.id, "group": m.group, "group_name": m.group_name, "ts": m.ts.isoformat(), "sender_id": m.sender_id,
                                 "sender_name": m.sender_name, "text": m.text, "reply_to": m.reply_to}
            if m.kind != "text":
                r["kind"] = m.kind
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (directory / "context.md").write_text(ds.context_md, encoding="utf-8")
    return directory


def tiny_key(ds: common.Dataset) -> dict[str, Any]:
    """Đáp án GIẢ tí hon (tự tạo cho test): 4 sự việc R (3 loại cổng, trong đó ca OTP), 1 B, 2 N, 1 gần-🔴, bẫy cứng + riêng tư, 1 sự thật."""
    m = lambda n: [mid_of(ds, n)]  # noqa: E731
    return {"format": "intel-key/1", "dataset": ds.name, "events": [
        {"id": "E1", "kind": "rui_ro_lua_dao", "message_ids": m("otp"), "label": "R", "gate": "lua_dao_dang_dien_ra", "set": "edge", "credentials_entered": True},
        {"id": "E2", "kind": "suc_khoe_an_toan", "message_ids": m("dien"), "label": "R", "gate": "an_toan", "set": "kb"},
        {"id": "E3", "kind": "xung_dot_khieu_nai", "message_ids": m("vo"), "label": "R", "gate": "loi_hua_khach_sap_vo", "set": "edge"},
        {"id": "E4", "kind": "viec_han", "message_ids": m("han"), "label": "B", "old_level": "red"},
        {"id": "E5", "kind": "nghia_vu_loi_hua", "message_ids": m("hua"), "label": "N", "old_level": "red"},
        {"id": "E6", "kind": "co_hoi_ket_noi", "message_ids": m("gan"), "label": "N", "old_level": "amber", "near_gate": "co_hoi_lon_sap_troi"},
    ], "facts": [{"id": "F1", "message_ids": m("bay"), "predicate": "payment_account"}],
        "traps": {"hard": [{"id": "T1", "message_ids": m("bay")}], "privacy": [{"id": "P1", "message_ids": m("tu")}], "no_opportunity_groups": ["G2"]}}


def level_for(ev: dict[str, Any]) -> str:
    if ev["label"] == "R":
        return "red"
    if ev["label"] == "B":
        return "amber"
    old = ev.get("old_level") or "amber"
    return "amber" if old == "red" else old


class Oracle:
    """Điểm cắm phân tích GIẢ biết đáp án: mỗi sự việc của đáp án có mặt trong lượt nào thì phát đúng một đơn vị `brain-out/1`. `tweak(event, unit_dict)` để
    bẻ hỏng có chủ đích (hạ mức, đổi loại, bịa trích dẫn…); `extra(inp)` thêm đơn vị nhiễu; `invalid_turns` làm hỏng đầu ra lượt đó."""

    name = "oracle"

    def __init__(self, key: dict[str, Any], tweak: Any = None, extra: Any = None, invalid_turns: set[int] | None = None, tokens: int = 1000) -> None:
        self.key = key
        self.tweak = tweak
        self.extra = extra
        self.invalid = invalid_turns or set()
        self.tokens = tokens

    async def analyze(self, inp: replay.TurnInput) -> replay.AnalysisResult:
        new = {m["id"]: m for m in inp.new}
        units: list[dict[str, Any]] = []
        facts: list[dict[str, Any]] = []
        for e in self.key["events"]:
            hit = [i for i in e["message_ids"] if i in new]
            if not hit:
                continue
            q = new[hit[0]]
            u = {"unit_ref": f"u{len(units) + 1}", "event_ref": e["id"], "kind": e["kind"],
                 "quotes": [{"message_id": q["message_id"], "field": "body", "text": q["text"][:100]}], "summary": f"sự việc {e['id']}",
                 "level_hint": level_for(e), "gate_candidate": e.get("gate"),
                 "flags": {"credentials_entered": bool(e.get("credentials_entered"))}}
            if self.tweak:
                u = self.tweak(e, u) or u
            if u is not None:
                units.append(u)
        for f in self.key.get("facts", []):
            hit = [i for i in f["message_ids"] if i in new]
            if hit and not units:        # sự thật cần một đơn vị mang (kind null = không chiếu tín hiệu, §4F)
                units.append({"unit_ref": "carrier", "event_ref": "carrier", "kind": None,
                              "quotes": [{"message_id": new[hit[0]]["message_id"], "field": "body", "text": new[hit[0]]["text"][:100]}], "summary": "mang sự thật"})
            if hit:
                facts.append({"unit_ref": units[0]["unit_ref"], "subject": {"surface": "Sếp"}, "predicate": f["predicate"], "quote_refs": [new[hit[0]]["message_id"]]})
        if self.extra:
            units.extend(self.extra(inp, len(units)) or [])
        out: Any = {"contract_version": "brain-out/1", "thread_ref": inp.thread_ref, "units": units, "facts_proposed": facts}
        if inp.n in self.invalid:
            out = {"contract_version": "brain-out/1", "thread_ref": "không-hợp-lệ", "units": "sai"}
        return replay.AnalysisResult(output=out, usage={"input": self.tokens, "output": 100, "cache_read": 500, "cache_write": 0, "model": "fake", "calls": 1})
