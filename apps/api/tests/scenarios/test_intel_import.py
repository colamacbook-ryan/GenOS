# ruff: noqa: E501
"""Bộ nhập file xuất Zalo PC / WhatsApp (L6): các khuôn Android/iOS/12h, đoán thứ tự ngày/tháng, tin nhiều dòng, media, hệ thống, tin thu hồi; Zalo (khuôn CHƯA verify) +
`custom_pattern`; id tin ổn định (nhập lại / tệp chồng nhau không nhân đôi); không bao giờ mentions_self; giờ gốc + múi giờ; CLI."""

import json
from datetime import datetime
from pathlib import Path

import import_history as ih
import parsers as ps
import pytest

WA_ANDROID = """12/10/2026, 14:05 - Messages and calls are end-to-end encrypted. No one outside of this chat can read them.
12/10/2026, 14:05 - Ryan created group "Khách sỉ"
12/10/2026, 14:06 - Nguyễn Thị Lan: Chị lấy 20kg Blend Honey như cũ nha
13/10/2026, 09:00 - Ryan: Dạ em lên đơn
chiều giao nha chị
13/10/2026, 09:02 - Nguyễn Thị Lan: <Media omitted>
13/10/2026, 09:03 - Nguyễn Thị Lan: PTT-20261013-WA0001.opus (file attached)
13/10/2026, 09:04 - Nguyễn Thị Lan: IMG-20261013-WA0002.jpg (file attached)
13/10/2026, 09:05 - Nguyễn Thị Lan: This message was deleted
13/10/2026, 09:06 - Trần Văn Tùng: sticker omitted
25/10/2026, 18:30 - Ryan added Trần Văn Tùng
"""

WA_IOS = """‎[12/10/2026, 14:06:11] Nguyễn Thị Lan: Chị lấy 20kg nha
‎[12/10/2026, 14:07:00] Ryan: Dạ ok chị
[13/10/2026, 9:00:01 PM] Ryan: Tối nay giao nha
‎[13/10/2026, 9:01:00 PM] Nguyễn Thị Lan: ‎image omitted
"""


def test_whatsapp_android_basic() -> None:
    r = ps.parse_whatsapp(WA_ANDROID)
    assert r.date_order == "dmy" and not r.date_order_ambiguous           # có ngày 13/25 > 12 ở vế đầu
    assert r.system_skipped == 3 and r.deleted_skipped == 1
    assert [(m.sender, m.kind) for m in r.messages] == [("Nguyễn Thị Lan", "text"), ("Ryan", "text"), ("Nguyễn Thị Lan", "file"), ("Nguyễn Thị Lan", "voice"),
                                                        ("Nguyễn Thị Lan", "image"), ("Trần Văn Tùng", "sticker")]
    assert r.messages[1].text == "Dạ em lên đơn\nchiều giao nha chị"          # tin nhiều dòng nối vào tin trước
    assert r.messages[0].ts == datetime(2026, 10, 12, 14, 6)
    assert all(m.text == "" for m in r.messages if m.kind != "text")


def test_whatsapp_ios_with_marks_and_12h() -> None:
    r = ps.parse_whatsapp(WA_IOS)
    assert [m.ts.hour for m in r.messages] == [14, 14, 21, 21] and r.messages[2].ts.second == 1
    assert r.messages[3].kind == "image" and r.messages[0].text == "Chị lấy 20kg nha"


def test_whatsapp_12h_am_pm_midnight_noon() -> None:
    t = "[1/12/26, 12:05:00 AM] A: nửa đêm\n[1/12/26, 12:05:00 PM] A: trưa\n[13/12/26, 1:00 PM] A: chiều\n"
    r = ps.parse_whatsapp(t)
    assert [m.ts.hour for m in r.messages] == [0, 12, 13] and r.messages[0].ts.year == 2026


def test_date_order_detection_and_ambiguity() -> None:
    assert ps.detect_date_order(["13/10/2026", "01/11/2026"]) == ("dmy", False)
    assert ps.detect_date_order(["10/13/2026", "11/01/2026"]) == ("mdy", False)
    assert ps.detect_date_order(["01/02/2026", "03/04/2026"]) == ("dmy", True)
    r = ps.parse_whatsapp("01/02/2026, 10:00 - A: một\n03/04/2026, 11:00 - A: hai\n")
    assert r.date_order_ambiguous and r.notes and r.messages[0].ts.month == 2
    r2 = ps.parse_whatsapp("01/02/2026, 10:00 - A: một\n03/04/2026, 11:00 - A: hai\n", date_order="mdy")
    assert not r2.date_order_ambiguous and r2.messages[0].ts.month == 1


def test_whatsapp_vietnamese_media_and_deleted_variants() -> None:
    t = ("12/10/2026, 14:06 - A: Tin nhắn này đã bị xóa\n12/10/2026, 14:07 - A: <Phương tiện bị bỏ qua>\n"
         "12/10/2026, 14:08 - A: Bạn đã thu hồi một tin nhắn\n12/10/2026, 14:09 - A: audio omitted\n12/10/2026, 14:10 - A: Giá 150k: ok không?\n")
    r = ps.parse_whatsapp(t)
    assert r.deleted_skipped == 2 and [m.kind for m in r.messages] == ["file", "voice", "text"]
    assert r.messages[2].text == "Giá 150k: ok không?"           # dấu ': ' trong nội dung không cắt nhầm người gửi


def test_whatsapp_garbage_raises() -> None:
    with pytest.raises(ps.ParseError):
        ps.parse_whatsapp("xin chào\nđây không phải tệp xuất\n")


ZALO_BRACKET = """[14:06:11 12/10/2026] Nguyễn Thị Lan: Chị lấy 20kg nha
[14:07:00 12/10/2026] Ryan: Dạ ok
chị nhé
[09:00:00 13/10/2026] Ryan: [Hình ảnh]
[09:01:00 13/10/2026] Nguyễn Thị Lan: Tin nhắn đã được thu hồi
"""

ZALO_HEADER = """Thứ Hai, 12/10/2026
14:06 Nguyễn Thị Lan: Chị lấy 20kg nha
14:07 Ryan: Dạ ok
Thứ Ba, 13/10/2026
09:00 Ryan: Tối giao nha
"""


def test_zalo_bracket_and_day_header_patterns() -> None:
    r = ps.parse_zalo(ZALO_BRACKET)
    assert [m.sender for m in r.messages] == ["Nguyễn Thị Lan", "Ryan", "Ryan"] and r.messages[1].text == "Dạ ok\nchị nhé"
    assert r.messages[2].kind == "image" and r.deleted_skipped == 1 and any("CHƯA verify" in n for n in r.notes)
    r2 = ps.parse_zalo(ZALO_HEADER)
    assert [m.ts.day for m in r2.messages] == [12, 12, 13] and r2.messages[0].ts.hour == 14


def test_zalo_custom_pattern_and_unknown_format() -> None:
    t = "12-10-2026|14:06|Lan>>chị lấy 20kg\n12-10-2026|14:07|Ryan>>dạ\n"
    pat = r"^(?P<date>\d+-\d+-\d+)\|(?P<time>\d+:\d+)\|(?P<sender>[^>]+)>>(?P<text>.*)$"
    r = ps.parse_zalo(t, custom_pattern=pat)
    assert [m.text for m in r.messages] == ["chị lấy 20kg", "dạ"]
    with pytest.raises(ps.ParseError, match="thiếu nhóm"):
        ps.parse_zalo(t, custom_pattern=r"(?P<date>x)")
    with pytest.raises(ps.ParseError):
        ps.parse_zalo("chỉ là một đoạn văn\nkhông có khuôn nào\n")


def test_sniff_format() -> None:
    assert ps.sniff_format(WA_IOS) == "whatsapp" and ps.sniff_format(WA_ANDROID) == "whatsapp"
    assert ps.sniff_format(ZALO_BRACKET) == "zalo" and ps.sniff_format(ZALO_HEADER) == "zalo"
    with pytest.raises(ps.ParseError):
        ps.sniff_format("không phải chat\n")


def env(res: ps.ParseResult, **kw):  # type: ignore[no-untyped-def]
    base = {"channel": "whatsapp", "group_id": "G-1", "group_name": "Khách sỉ", "owner_name": "Ryan", "batch": "b1"}
    return ih.build_envelopes(res, **(base | kw))


def test_envelopes_are_history_not_commands() -> None:
    envs = env(ps.parse_whatsapp(WA_ANDROID))
    assert len(envs) == 6 and [e["occurred_at"] for e in envs] == sorted(e["occurred_at"] for e in envs)
    for e in envs:
        assert e["payload"]["source"] == "import" and e["mentions_self"] is False and "replies_to_self" not in e and e["external_group_id"] == "G-1"
        assert e["external_msg_id"].startswith("imp:") and e["occurred_at"].endswith("+07:00")
    assert envs[0]["occurred_at"] == "2026-10-12T14:06:00+07:00"                      # giờ GỐC của tin, múi giờ VN
    owner = [e for e in envs if e["sender_name"] == "Ryan"][0]
    assert owner["direction"] == "outbound" and "direction" not in envs[0]
    media = [e for e in envs if e["kind"] != "text"]
    assert media and all(e["body_text"] is None and e["payload"]["media_missing"] for e in media)
    assert {e["kind"] for e in media} == {"file", "voice", "image", "sticker"}


def test_ids_are_stable_across_files_overlap_and_reimport() -> None:
    a = env(ps.parse_whatsapp(WA_ANDROID), batch="A")
    b = env(ps.parse_whatsapp(WA_ANDROID), batch="B")                                 # cùng nội dung, lô khác
    assert [e["external_msg_id"] for e in a] == [e["external_msg_id"] for e in b]
    # tệp thứ hai chồng một phần (bắt đầu giữa chừng, chèn thêm dòng đầu): tin chung cùng id
    tail = "\n".join(WA_ANDROID.splitlines()[5:])
    c = env(ps.parse_whatsapp("11/10/2026, 08:00 - Ryan: tin cũ hơn\n" + tail + "\n"))
    ids_a = {e["external_msg_id"] for e in a}
    shared = [e for e in c if e["external_msg_id"] in ids_a]
    assert len(shared) >= 4
    # hai tin GIỐNG HỆT cùng phút (ok, ok) vẫn là hai id khác nhau
    twin = env(ps.parse_whatsapp("12/10/2026, 10:00 - A: ok\n12/10/2026, 10:00 - A: ok\n"))
    assert len({e["external_msg_id"] for e in twin}) == 2
    # nhóm khác ⇒ id khác
    assert env(ps.parse_whatsapp(WA_ANDROID), group_id="G-2")[0]["external_msg_id"] != a[0]["external_msg_id"]


def test_sender_ids_and_map() -> None:
    envs = env(ps.parse_whatsapp(WA_ANDROID))
    lan = {e["sender_external_id"] for e in envs if e["sender_name"] == "Nguyễn Thị Lan"}
    assert len(lan) == 1 and next(iter(lan)).startswith("imp:")
    mapped = env(ps.parse_whatsapp(WA_ANDROID), sender_map={"Nguyễn Thị Lan": "84912345678@s.whatsapp.net"})
    assert {e["sender_external_id"] for e in mapped if e["sender_name"] == "Nguyễn Thị Lan"} == {"84912345678@s.whatsapp.net"}
    # tên khác dấu/hoa thường vẫn cùng một người
    assert ih.sender_id("zalo", "Nguyễn Thị Lan") == ih.sender_id("zalo", "nguyen thi lan")


def test_since_filter() -> None:
    envs = env(ps.parse_whatsapp(WA_ANDROID), since=datetime.fromisoformat("2026-10-13T00:00:00+07:00"))
    assert envs and all(e["occurred_at"] >= "2026-10-13" for e in envs)


def test_dm_import_refused_until_ingest_skips_import_source(tmp_path: Path) -> None:
    """Review L6 🟠2: ingest (L5) chưa bỏ qua payload.source='import' ⇒ tin 1-1 nhập vào đi đường nhanh + direct_new. Bộ nhập từ chối chat 1-1."""
    with pytest.raises(ValueError, match="từ chối nhập chat 1-1"):
        env(ps.parse_whatsapp(WA_ANDROID), group_id=None)
    f = tmp_path / "dm.txt"
    f.write_text(WA_ANDROID, encoding="utf-8")
    with pytest.raises(SystemExit, match="từ chối nhập chat 1-1"):
        ih.main([str(f)])


def test_parser_scales_linearly_and_enforces_caps(tmp_path: Path) -> None:
    import time

    big = "".join(f"12/10/2026, 14:{i % 60:02d} - A: tin {i}\n" for i in range(60_000))
    t0 = time.monotonic()
    r = ps.parse_whatsapp(big)
    assert len(r.messages) == 60_000 and time.monotonic() - t0 < 10        # trước đây O(n²): 60k dòng tính bằng phút
    long_line = "12/10/2026, 14:00 - A: " + "x" * (ps.MAX_LINE_CHARS * 3) + "\n"
    r2 = ps.parse_whatsapp(long_line)
    assert r2.truncated_lines == 1 and len(r2.messages[0].text) <= ps.MAX_LINE_CHARS
    f = tmp_path / "huge.txt"
    f.write_text("12/10/2026, 14:00 - A: x\n")
    old = ps.MAX_FILE_BYTES
    ih.MAX_FILE_BYTES = 5
    try:
        with pytest.raises(ps.ParseError, match="vượt trần"):
            ih.parse_file(f)
    finally:
        ih.MAX_FILE_BYTES = old


def test_stream_constant_matches_bus() -> None:
    from gh.chassis.bus import BRIDGE_INBOUND

    assert ih.BRIDGE_INBOUND == BRIDGE_INBOUND


def test_cli_dry_run_and_jsonl(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "chat.txt"
    f.write_text(WA_ANDROID, encoding="utf-8")
    assert ih.main([str(f), "--group-id", "G-1", "--group-name", "Khách sỉ", "--owner-name", "Ryan"]) == 0
    s = json.loads(capsys.readouterr().out)
    assert s["messages"] == 6 and s["kinds"]["text"] == 2 and s["media_missing"] == 4 and s["format"] == "whatsapp" and "emitted" not in s
    out = tmp_path / "e.jsonl"
    assert ih.main([str(f), "--group-id", "G-1", "--emit", f"jsonl:{out}", "--sender-map", str(_map(tmp_path))]) == 0
    rows = [json.loads(x) for x in out.read_text().splitlines()]
    assert len(rows) == 6 and rows[0]["stream"] == "gh.bridge.inbound" and rows[0]["type"] == "message"
    assert rows[0]["payload"]["sender_external_id"] == "u-lan"
    with pytest.raises(SystemExit):
        ih.main([str(f), "--group-id", "G-1", "--emit", "bus"])           # thiếu ORG/GH_REDIS_URL ⇒ từ chối, không đoán


def _map(tmp: Path) -> Path:
    p = tmp / "map.json"
    p.write_text(json.dumps({"Nguyễn Thị Lan": "u-lan"}))
    return p
