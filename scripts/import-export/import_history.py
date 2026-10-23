#!/usr/bin/env python3
# ruff: noqa: E501, ASYNC240
"""import_history.py — bộ nhập file xuất Zalo PC / WhatsApp vào `raw.events` (spec §4 "Khởi động lạnh" + §6 A7, gói L6).

    python3 scripts/import-export/import_history.py chat.txt --format auto --channel zalo --group-id GROUP_EXT_ID --group-name "Tên nhóm" \\
        --owner-name "Ryan" --emit jsonl:/tmp/imp.jsonl            # xem trước, không chạm hệ thống (mặc định là --dry-run)
    ORG=… GH_REDIS_URL=redis://localhost:6379/0 python3 … --emit bus   # đổ vào bus cục bộ (gh.bridge.inbound), rồi API ghi vào kho thô

Chạy TRÊN MÁY SẾP, không qua mạng ngoài: chỉ đọc tệp và (khi `--emit bus`) ghi vào Redis cục bộ.

Thiết kế (không phá luật nào của kho thô):
* mỗi tin mang `payload.source = 'import'` (+ `import_batch`, `import_line`); KHÔNG BAO GIỜ `mentions_self` / `replies_to_self` ⇒ không kích hoạt trả lời/đường nhanh/lệnh của Sếp;
* `external_msg_id` ổn định theo (kênh, nhóm, giờ, người gửi, nội dung, số thứ tự trùng) — KHÔNG phụ thuộc tên tệp hay vị trí dòng ⇒ nhập lại / nhập hai tệp chồng nhau
  không nhân đôi (kho thô khử trùng theo `(kênh, external_msg_id)`);
* `occurred_at` = giờ gửi GỐC (neo cho `valid_from` của sự thật ⇒ sự thật cũ chèn đúng chỗ, không đè sự thật mới — §4A.1, CNS-064). Múi giờ mặc định Asia/Ho_Chi_Minh;
* người gửi chỉ có TÊN trong tệp: `sender_external_id = imp:<băm tên>` trừ khi `--sender-map` {tên: id thật}; tin của `--owner-name` đi `direction='outbound'`;
* nhóm phải đã tồn tại và đang được nghe (`ingest_message` bỏ tin nhóm chưa bật); media xuất ra không kèm nội dung ⇒ `kind` image/voice/file/sticker, `body_text` rỗng,
  `payload.media_missing = true` (đếm vào dòng độ phủ — M12); tin đã thu hồi/xoá bị bỏ và đếm.
ĐIỂM CHỜ L2: kho thô đặt `received_at = now()` cho tin nhập ⇒ scheduler của L2 phải coi `payload.source='import'` là LỊCH SỬ (không chạy đường nhanh, xếp theo `occurred_at`)."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parsers import (  # noqa: E402
    MAX_FILE_BYTES,
    ParseError,
    ParseResult,
    fold_name,
    parse_whatsapp,
    parse_zalo,
    sniff_format,
)

BRIDGE_INBOUND = "gh.bridge.inbound"        # khớp gh.chassis.bus (test đối chiếu)
DEFAULT_TZ = "Asia/Ho_Chi_Minh"
#: ĐIỂM CHỜ (L5 sở hữu `gh/data/ingest.py`): `ingest_message` chưa bỏ qua `payload.source == 'import'` nên tin 1-1 nhập vào vẫn đi đường nhanh
#: (`direct_fast`) và sinh `direct_new`. Tới khi ingest xử lý, bộ nhập TỪ CHỐI chat 1-1.
DM_REFUSAL = ("từ chối nhập chat 1-1 (thiếu --group-id): ingest chưa bỏ qua payload.source='import' nên tin 1-1 sẽ đi đường nhanh và sinh sự kiện direct_new. "
              "Chỉ nhập nhóm cho tới khi ingest.py (L5) hỗ trợ — xem điểm chờ trong báo cáo L6")
KIND_FOR = {"text": "text", "image": "image", "voice": "voice", "file": "file", "sticker": "sticker"}


def sender_id(channel: str, name: str, sender_map: dict[str, str] | None = None) -> str:
    if sender_map and name in sender_map:
        return sender_map[name]
    return "imp:" + hashlib.sha1(f"{channel}\x1f{fold_name(name)}".encode()).hexdigest()[:12]


def message_id(channel: str, group_key: str, ts_iso: str, sender: str, text: str, occurrence: int) -> str:
    return "imp:" + hashlib.sha1("\x1f".join([channel, group_key, ts_iso, fold_name(sender), text, str(occurrence)]).encode()).hexdigest()[:24]


def batch_id(path: Path) -> str:
    """Nhãn lô (theo nội dung tệp, không theo đường dẫn) — chỉ để truy vết, KHÔNG vào external_msg_id."""
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12]


def build_envelopes(res: ParseResult, *, channel: str, group_id: str | None, group_name: str | None, owner_name: str | None = None,
                    sender_map: dict[str, str] | None = None, tz: str = DEFAULT_TZ, batch: str = "", since: datetime | None = None,
                    until: datetime | None = None) -> list[dict[str, Any]]:
    """Dòng đã đọc → payload `message` của `gh.bridge.inbound`, sắp theo giờ gửi (ổn định theo thứ tự trong tệp)."""
    if not group_id:
        raise ValueError(DM_REFUSAL)
    zone = ZoneInfo(tz)
    group_key = group_id
    seen: Counter[tuple[str, str, str]] = Counter()
    out: list[dict[str, Any]] = []
    for ln in res.messages:
        ts = ln.ts.replace(tzinfo=zone)
        if (since and ts < since.astimezone(zone)) or (until and ts > until.astimezone(zone)):
            continue
        iso = ts.isoformat()
        k = (iso, fold_name(ln.sender), ln.text)
        occ = seen[k]
        seen[k] += 1
        is_owner = bool(owner_name) and fold_name(ln.sender) == fold_name(owner_name or "")
        payload: dict[str, Any] = {"source": "import", "import_batch": batch, "import_line": ln.line_no, "import_format": res.format}
        if ln.kind != "text":
            payload["media_missing"] = True
        p: dict[str, Any] = {
            "channel": channel, "external_msg_id": message_id(channel, group_key, iso, ln.sender, ln.text, occ),
            "external_group_id": group_id, "group_name": group_name if group_id else None,
            "sender_external_id": sender_id(channel, ln.sender, sender_map), "sender_name": ln.sender, "sender_phone": None,
            "body_text": ln.text if ln.kind == "text" else None, "kind": KIND_FOR.get(ln.kind, "other"),
            "mentions_self": False, "occurred_at": iso, "payload": payload,
        }
        if is_owner:
            p["direction"] = "outbound"
        out.append(p)
    out.sort(key=lambda p: p["occurred_at"])        # sort ổn định: cùng giờ giữ thứ tự tệp
    return out


def summarize(res: ParseResult, envs: list[dict[str, Any]]) -> dict[str, Any]:
    kinds = Counter(e["kind"] for e in envs)
    return {"format": res.format, "messages": len(envs), "kinds": dict(kinds), "system_skipped": res.system_skipped, "deleted_skipped": res.deleted_skipped,
            "unparsed_lines": res.unparsed_lines, "truncated_lines": res.truncated_lines, "date_order": res.date_order, "date_order_ambiguous": res.date_order_ambiguous, "notes": res.notes,
            "first": envs[0]["occurred_at"] if envs else None, "last": envs[-1]["occurred_at"] if envs else None,
            "senders": sorted({e["sender_name"] for e in envs}), "media_missing": sum(1 for e in envs if e["payload"].get("media_missing"))}


def parse_file(path: Path, fmt: str = "auto", date_order: str | None = None, custom_pattern: str | None = None) -> ParseResult:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ParseError(f"tệp {path.stat().st_size} byte vượt trần {MAX_FILE_BYTES} byte")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    if fmt == "auto":
        fmt = sniff_format(text)
    if fmt == "whatsapp":
        return parse_whatsapp(text, date_order)
    if fmt == "zalo":
        return parse_zalo(text, date_order, custom_pattern)
    raise ParseError(f"format không hợp lệ: {fmt!r}")


async def emit_bus(envs: list[dict[str, Any]], redis_url: str, org: str) -> int:
    d = str(Path(os.environ.get("GH_API_DIR", Path(__file__).resolve().parents[2] / "apps" / "api")))
    if d not in sys.path:
        sys.path.insert(0, d)
    from redis.asyncio import Redis  # noqa: PLC0415

    from gh.chassis.bus import BRIDGE_INBOUND as STREAM  # noqa: PLC0415
    from gh.chassis.bus import EventBus  # noqa: PLC0415

    assert STREAM == BRIDGE_INBOUND
    r = Redis.from_url(redis_url)
    bus = EventBus(r, 10000)
    try:
        for p in envs:
            await bus.publish(STREAM, "message", p, actor="import", org_id=org)
    finally:
        await r.aclose()
    return len(envs)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Nhập file xuất Zalo PC / WhatsApp (xem docstring)")
    ap.add_argument("file")
    ap.add_argument("--format", choices=("auto", "whatsapp", "zalo"), default="auto")
    ap.add_argument("--channel", choices=("zalo", "whatsapp"), help="kênh đích (mặc định: theo --format)")
    ap.add_argument("--group-id", help="external_group_id của nhóm (BẮT BUỘC: chat 1-1 bị từ chối, xem DM_REFUSAL)")
    ap.add_argument("--group-name")
    ap.add_argument("--owner-name", help="tên của Sếp trong tệp: tin của người này đi direction=outbound")
    ap.add_argument("--sender-map", help="JSON {tên hiển thị: id thật của người gửi}")
    ap.add_argument("--tz", default=DEFAULT_TZ)
    ap.add_argument("--date-order", choices=("dmy", "mdy"))
    ap.add_argument("--custom-pattern", help="regex có nhóm đặt tên date,time,sender,text (khuôn Zalo lạ)")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--emit", default="dry", help="dry (mặc định, chỉ báo cáo) | jsonl:<đường dẫn> | bus")
    ap.add_argument("--org", default=os.environ.get("ORG", ""))
    ap.add_argument("--redis-url", default=os.environ.get("GH_REDIS_URL", ""))
    a = ap.parse_args(argv)
    path = Path(a.file)
    if not a.group_id:
        raise SystemExit(DM_REFUSAL)
    res = parse_file(path, a.format, a.date_order, a.custom_pattern)
    channel = a.channel or ("whatsapp" if res.format == "whatsapp" else "zalo")
    smap = json.loads(Path(a.sender_map).read_text(encoding="utf-8")) if a.sender_map else None
    envs = build_envelopes(res, channel=channel, group_id=a.group_id, group_name=a.group_name, owner_name=a.owner_name, sender_map=smap, tz=a.tz,
                           batch=batch_id(path), since=datetime.fromisoformat(a.since) if a.since else None,
                           until=datetime.fromisoformat(a.until) if a.until else None)
    summary = summarize(res, envs)
    if a.emit.startswith("jsonl:"):
        Path(a.emit[6:]).write_text("".join(json.dumps({"stream": BRIDGE_INBOUND, "type": "message", "payload": p}, ensure_ascii=False) + "\n" for p in envs),
                                    encoding="utf-8")
        summary["emitted"] = {"jsonl": a.emit[6:], "n": len(envs)}
    elif a.emit == "bus":
        if not a.org or not a.redis_url:
            raise SystemExit("--emit bus cần ORG và GH_REDIS_URL (hoặc --org/--redis-url)")
        summary["emitted"] = {"bus": asyncio.run(emit_bus(envs, a.redis_url, a.org))}
    elif a.emit != "dry":
        raise SystemExit(f"--emit không hợp lệ: {a.emit!r}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
