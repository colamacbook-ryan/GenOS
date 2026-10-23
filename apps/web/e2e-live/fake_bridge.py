"""Bridge giả: heartbeat, trả QR rồi 'đã quét' rồi active khi nhận session.login; gửi danh bạ nhóm; nhận lệnh
'send' từ file; giai đoạn 5.3 (luồng 5): nghe `gh.bridge.outbound` — bản nháp Owner đã duyệt gửi permit sang
đây, bridge giả "gửi" (ghi log, không gọi mạng thật ra ngoài) rồi báo `send.result` để Bàn làm việc chuyển
trạng thái `sent` — đúng vòng permit dùng một lần của `gh.biz.core.drafts`.

v0.1.76 (gói L6): thêm KỊCH BẢN PHÁT TIN — bridge giả phát tin nhóm, tin 1-1, voice, ảnh, rớt phiên, bị kick. Kịch bản là
danh sách sự kiện JSON (xem `compile_script_event`): đưa vào bằng sự kiện điều khiển `fake.script` (payload `{"events": [...]}`)
trên `gh.bridge.control`, hoặc tệp `FAKE_SCRIPT` (JSON một danh sách) đọc lúc khởi động. Phần biên dịch sự kiện → phong bì bus
là hàm THUẦN (không Redis, không đồng hồ thật) để test và `scripts/intel-eval/replay.py` dùng chung đúng một khuôn phong bì."""
import asyncio, base64, hashlib, json, os, sys, time, uuid
sys.path.insert(0, os.environ.get("GH_API_DIR", os.path.join(os.path.dirname(__file__), "../../api")))

ORG = os.environ.get("ORG", "")
OUT = os.environ.get("LIVE_OUT", ".")
QR = "data:image/svg+xml;base64," + base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200"><rect width="200" height="200" fill="#fff"/><rect x="20" y="20" width="60" height="60"/><rect x="120" y="20" width="60" height="60"/><rect x="20" y="120" width="60" height="60"/></svg>').decode()


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


# ─── Kịch bản phát tin (hàm thuần) ────────────────────────────────────────────────────────────────────────────────────────
# Tên stream khớp `gh.chassis.bus` (BRIDGE_INBOUND/STATUS/DIRECTORY); khai ở đây để biên dịch không phải nạp `gh`.
S_INBOUND, S_STATUS, S_DIRECTORY = "gh.bridge.inbound", "gh.bridge.status", "gh.bridge.directory"
SCRIPT_KINDS = ("group_msg", "dm", "voice", "image", "session_drop", "session_reconnecting", "kicked", "sleep")
#: Mặc định kênh thử; mọi tin giả mang `payload.fake = true` để không ai nhầm với tin thật khi đọc lại kho.
DEFAULT_CHANNEL = "zalo"


def stable_msg_id(*parts: object) -> str:
    """Mã tin ổn định theo nội dung (phát lại cùng kịch bản ⇒ cùng mã ⇒ kho thô khử trùng, không nhân đôi)."""
    return hashlib.sha1("\x1f".join(str(p) for p in parts).encode()).hexdigest()[:24]


def message_payload(ev: dict, seq: int = 0) -> dict:
    """Sự kiện kịch bản tin (group_msg | dm | voice | image) → payload `message` của `gh.bridge.inbound` (cùng khoá với e2e-live/send.py).

    Trường sự kiện: `sender`, `name` (bắt buộc); `text`; `group` (không có ⇒ tin 1-1 trừ khi `kind == dm`), `group_name`;
    `id` (mã tin; không có ⇒ băm ổn định), `ts` (ISO, thành `occurred_at`), `reply_to` (mã tin được trả lời), `mention`,
    `channel`, `forwarded`; voice: `transcript` (chữ mà engine media giả sẽ trả), `duration_s`; ảnh: `caption`, `ocr_text`,
    `media_text`. Voice/ảnh KHÔNG mang chữ thật trong `body_text` — chữ nằm ở `payload.fake_media_text` cho engine giả (§4E)."""
    kind_ev = ev["type"]
    channel = ev.get("channel", DEFAULT_CHANNEL)
    group = ev.get("group") if kind_ev != "dm" else None
    text = ev.get("text")
    payload: dict = {"fake": True, "seq": seq}
    kind = "text"
    if kind_ev == "voice":
        kind = "voice"
        payload |= {"media_ref": f"fake://voice/{ev.get('id') or stable_msg_id(channel, group, ev['sender'], ev.get('ts'), seq)}",
                    "duration_s": ev.get("duration_s", 10), "fake_media_text": ev.get("transcript", text or "")}
        text = None
    elif kind_ev == "image":
        kind = "image"
        payload |= {"media_ref": f"fake://image/{ev.get('id') or stable_msg_id(channel, group, ev['sender'], ev.get('ts'), seq)}",
                    "fake_media_text": ev.get("media_text") or ev.get("ocr_text") or ""}
        text = ev.get("caption", text)
    if ev.get("reply_to"):
        payload["reply_to"] = ev["reply_to"]
    if ev.get("forwarded") is not None:
        payload["forwarded"] = ev["forwarded"]
    if ev.get("source"):
        payload["source"] = ev["source"]
    if ev.get("dataset_kind"):
        payload["dataset_kind"] = ev["dataset_kind"]       # forward | link | system của bộ dữ liệu tổng hợp
    p = {"channel": channel,
         "external_msg_id": ev.get("id") or stable_msg_id(channel, group, ev["sender"], ev.get("ts"), text, seq),
         "external_group_id": group, "group_name": ev.get("group_name"),
         "sender_external_id": ev["sender"], "sender_name": ev["name"], "sender_phone": ev.get("phone"),
         "body_text": text, "kind": kind, "mentions_self": bool(ev.get("mention", False)), "payload": payload}
    if ev.get("ts"):
        p["occurred_at"] = ev["ts"]
    return p


def compile_script_event(ev: dict, seq: int = 0) -> list[tuple[str, str, dict]]:
    """Một sự kiện kịch bản → danh sách (stream, type, payload) để phát lên bus. Sự kiện lạ ⇒ ValueError (kịch bản sai phải lộ ngay).

    * `group_msg` / `dm` / `voice` / `image` → `gh.bridge.inbound` `message`.
    * `session_drop` {session_id, reason: expired|logged_out|error, error?} → `gh.bridge.status` `session.ended` (rớt phiên).
    * `session_reconnecting` {session_id, error?} → `session.reconnecting` (mất mạng, đang tự nối lại).
    * `kicked` {group, channel?, by?} → `gh.bridge.directory` `group.self_removed` (bot bị mời ra / chặn khỏi nhóm).
    * `sleep` {seconds} → không phát gì (chỉ để người chạy trực tiếp chờ; `replay.py` bỏ qua vì dùng đồng hồ giả)."""
    t = ev.get("type")
    if t not in SCRIPT_KINDS:
        raise ValueError(f"sự kiện kịch bản lạ: {t!r} (hợp lệ: {', '.join(SCRIPT_KINDS)})")
    if t in ("group_msg", "dm", "voice", "image"):
        for k in ("sender", "name"):
            if not ev.get(k):
                raise ValueError(f"sự kiện {t} thiếu `{k}`")
        if t == "group_msg" and not ev.get("group"):
            raise ValueError("group_msg thiếu `group`")
        if t == "voice" and "transcript" not in ev and "text" not in ev:
            raise ValueError("voice thiếu `transcript`")
        return [(S_INBOUND, "message", message_payload(ev, seq))]
    if t in ("session_drop", "session_reconnecting"):
        if not ev.get("session_id"):
            raise ValueError(f"{t} thiếu `session_id`")
        if t == "session_drop":
            reason = ev.get("reason", "error")
            if reason not in ("expired", "logged_out", "error"):
                raise ValueError(f"reason không hợp lệ: {reason!r}")
            return [(S_STATUS, "session.ended", {"session_id": ev["session_id"], "reason": reason, "error": ev.get("error")})]
        return [(S_STATUS, "session.reconnecting", {"session_id": ev["session_id"], "error": ev.get("error", "NETWORK_UNAVAILABLE")})]
    if t == "kicked":
        if not ev.get("group"):
            raise ValueError("kicked thiếu `group`")
        return [(S_DIRECTORY, "group.self_removed",
                 {"channel": ev.get("channel", DEFAULT_CHANNEL), "group_id": ev["group"], "by": ev.get("by", "unknown")})]
    return []   # sleep


def compile_script(events: list[dict]) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    for i, ev in enumerate(events):
        out.extend(compile_script_event(ev, i))
    return out


async def play_script(bus, events: list[dict], *, org: str, pace: bool = True) -> int:
    """Phát một kịch bản lên bus thật (`bus` = EventBus). Trả số bản ghi đã phát. `sleep` chỉ chờ khi `pace`."""
    n = 0
    for i, ev in enumerate(events):
        if ev.get("type") == "sleep":
            if pace:
                await asyncio.sleep(float(ev.get("seconds", 1)))
            continue
        for stream, typ, payload in compile_script_event(ev, i):
            await bus.publish(stream, typ, payload, actor="bridge:fake", org_id=org)
            n += 1
    return n


async def main():
    from redis.asyncio import Redis
    from gh.chassis.bus import EventBus, BRIDGE_CONTROL, BRIDGE_STATUS, BRIDGE_DIRECTORY, BRIDGE_OUTBOUND
    from gh import crypto
    global ORG
    ORG = os.environ["ORG"]
    r = Redis.from_url(os.environ["GH_REDIS_URL"]); bus = EventBus(r, 10000)
    async def hb():
        while True:
            await r.set("gh:bridge:heartbeat", str(time.time()), ex=30); await asyncio.sleep(5)
    asyncio.create_task(hb())
    await bus.publish(BRIDGE_STATUS, "bridge.hello", {}, actor="bridge", org_id=ORG)
    async def on_control(ev):
        p = ev.payload
        if ev.type == "fake.script":      # L6: phát kịch bản tin (nhóm/DM/voice/ảnh/rớt phiên/kick)
            await play_script(bus, p.get("events", []), org=ORG, pace=bool(p.get("pace", True)))
            return
        if ev.type != "session.login": return
        sid, ch = p["session_id"], p["channel"]
        await bus.publish(BRIDGE_STATUS, "session.qr", {"session_id": sid, "image": QR,
                          "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 60))},
                          actor="bridge", org_id=ORG)
        await asyncio.sleep(float(os.environ.get("SCAN_AFTER", "6")))
        await bus.publish(BRIDGE_STATUS, "session.scanned", {"session_id": sid}, actor="bridge", org_id=ORG)
        await asyncio.sleep(2)
        cred = crypto.transport_encrypt(json.dumps({"imei": "x", "cookie": [], "userAgent": "UA"}).encode(), f"{ch}:{sid}")
        await bus.publish(BRIDGE_STATUS, "session.active", {"session_id": sid, "credential": cred,
                          "account": {"id": "acc-1", "name": "Zalo Sếp"}}, actor="bridge", org_id=ORG)
        await bus.publish(BRIDGE_DIRECTORY, "groups", {"channel": ch, "groups": [
            {"external_id": "g-si", "name": "Chợ thép sỉ miền Nam", "member_count": 124, "members": [
                {"external_id": "u-lan", "name": "Nguyễn Thị Lan", "phone": "0912345678"},
                {"external_id": "u-tung", "name": "Trần Văn Tùng"}]},
            {"external_id": "g-noibo", "name": "Nội bộ kinh doanh", "member_count": 9},
            {"external_id": "g-dt", "name": "Đối tác vận tải", "member_count": 31}]}, actor="bridge", org_id=ORG)
    async def on_outbound(ev):
        """`message.send`: permit = base64url(JSON claims) "." base64url(chữ ký) — bridge giả không cần kiểm
        chữ ký (đó là việc của server trước khi tin tới đây), chỉ đọc claims để trả kết quả đúng draft_id."""
        p = ev.payload
        if ev.type != "message.send": return
        claims = json.loads(_b64url_decode(p["permit"].split(".", 1)[0]))
        with open(os.path.join(OUT, "bridge_sent.log"), "a") as f:
            f.write(json.dumps({"channel": p["channel"], "thread_id": p["thread_id"], "text": p["text"],
                                "draft_id": claims["draft_id"]}, ensure_ascii=False) + "\n")
        await bus.publish(BRIDGE_STATUS, "send.result",
                          {"session_id": p["session_id"], "draft_id": claims["draft_id"], "ok": True,
                           "external_msg_id": f"fake-out-{uuid.uuid4().hex[:8]}"}, actor="bridge", org_id=ORG)
    script_file = os.environ.get("FAKE_SCRIPT")
    if script_file:
        with open(script_file) as f:
            asyncio.create_task(play_script(bus, json.load(f), org=ORG))
    stop = asyncio.Event()
    await asyncio.gather(bus.run(BRIDGE_CONTROL, "fakebridge", "fb-1", on_control, stop),
                         bus.run(BRIDGE_OUTBOUND, "fakebridge-out", "fb-out-1", on_outbound, stop))


if __name__ == "__main__":
    asyncio.run(main())
