# ruff: noqa: E501
"""L5 — tải file đa phương tiện an toàn: media_ref không phải URL, giới hạn kích thước/MIME (khai báo VÀ byte thật), sha256,
tên tệp, và `media.fetch` qua bus (bridge giả ghi kết quả vào danh sách Redis)."""

import base64
import hashlib
import uuid
from typing import Any

import orjson
import pytest
from redis.asyncio import Redis

from gh.biz.media import fetch as mf
from gh.biz.media.providers import fake_image_bytes, fake_voice_bytes
from gh.chassis.bus import BRIDGE_CONTROL, EventBus

REF = "zalo:s1:5001"
WHO = {"chat": "g1", "sender": "u1"}


def test_media_ref_chi_nhan_khoa_tra_cuu_khong_phai_url() -> None:
    assert mf.valid_ref("whatsapp:w1:3EB0ABCD") and mf.valid_ref(REF)
    for bad in ("https://evil.example/x", "zalo:s1:../../etc/passwd", "zalo:s1:a/b", "zalo:s1:a?b=1", "zalo:s1:a%2Fb",
                "telegram:s1:1", "zalo:s1:", "", None, 5, "zalo:s1:" + "a" * 121, "zalo:s 1:a"):
        assert not mf.valid_ref(bad), bad


def test_sniff_theo_byte_that() -> None:
    assert mf.sniff_mime(b"OggS\x00\x02") == "audio/ogg"
    assert mf.sniff_mime(b"\x00\x00\x00\x18ftypM4A ") == "audio/mp4"
    assert mf.sniff_mime(b"ID3\x04\x00") == "audio/mpeg"
    assert mf.sniff_mime(b"\xff\xfb\x90\x00") == "audio/mpeg"
    assert mf.sniff_mime(b"\xff\xf1\x50\x80") == "audio/aac"
    assert mf.sniff_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert mf.sniff_mime(b"#!AMR\n") == "audio/amr"
    assert mf.sniff_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert mf.sniff_mime(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert mf.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert mf.sniff_mime(b"GIF89a..") == "image/gif"
    assert mf.sniff_mime(b"MZ\x90\x00") is None and mf.sniff_mime(b"<html>") is None and mf.sniff_mime(b"") is None


def test_mime_that_phai_thuoc_allowlist_cua_loai() -> None:
    assert mf.verified_mime("voice", b"OggS....") == "audio/ogg"
    with pytest.raises(mf.MediaFetchError) as e:
        mf.verified_mime("voice", b"\x89PNG\r\n\x1a\n..")      # ảnh giả làm voice
    assert e.value.code == "mime_not_allowed"
    with pytest.raises(mf.MediaFetchError):
        mf.verified_mime("image", b"OggS....")
    with pytest.raises(mf.MediaFetchError):
        mf.verified_mime("voice", b"MZ\x90\x00 exe")


def test_khai_bao_qua_lon_hoac_mime_la_bi_chan_truoc_khi_tai() -> None:
    assert mf.declared_problem("voice", {"mime": "audio/ogg", "size": 1000, "seconds": 30}) is None
    assert mf.declared_problem("voice", {"size": mf.MAX_VOICE_BYTES + 1}) == "too_large"
    assert mf.declared_problem("voice", {"seconds": mf.MAX_VOICE_SECONDS + 1}) == "too_large"
    assert mf.declared_problem("voice", {"seconds": mf.MAX_VOICE_SECONDS}) is None
    assert mf.declared_problem("image", {"size": mf.MAX_IMAGE_BYTES + 1}) == "too_large"
    assert mf.declared_problem("image", {"size": mf.MAX_IMAGE_BYTES, "mime": "image/png"}) is None
    assert mf.declared_problem("image", {"mime": "image/svg+xml"}) == "mime_not_allowed"   # SVG chạy được script
    assert mf.declared_problem("voice", {"mime": "application/x-msdownload"}) == "mime_not_allowed"
    assert mf.declared_problem("voice", {"size": "abc"}) == "bad_request"
    assert mf.declared_problem("video", {}) == "bad_request"
    assert mf.declared_problem("voice", None) is None


def test_ten_tep_an_toan_chong_path_traversal() -> None:
    for evil in ("../../etc/passwd", "..\\..\\windows\\system32", "/abs/path/x.ogg", "a/../../b.ogg", "x\x00y.ogg", "..", ".",
                 "", None, "con?.ogg", "<script>.png", "a" * 500 + ".ogg", "héllo wörld.ogg"):
        n = mf.safe_filename(evil, "audio/ogg")
        assert "/" not in n and "\\" not in n and ".." not in n and "\x00" not in n, n
        assert n.endswith(".ogg") and len(n) <= 70 and n[0] not in "._-"
    assert mf.safe_filename("a.exe", "audio/ogg").endswith(".ogg")      # đuôi theo MIME đã kiểm, không theo tên người gửi
    assert mf.safe_filename("x", "audio/zzz").endswith(".bin")


# ─── media.fetch qua bus ─────────────────────────────────────────────────────────────────────────────────────────────

def _ok(rid: str, data: bytes, **over: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"request_id": rid, "ok": True, "mime": "audio/ogg", "size": len(data),
                         "sha256": hashlib.sha256(data).hexdigest(), "data_b64": base64.b64encode(data).decode()}
    return r | over


class FakeBridgeBus(EventBus):
    """Bus thật; mỗi `media.fetch` được 'bridge' trả lời bằng `responder(request_id)` (None = im lặng)."""

    def __init__(self, redis: Redis, responder: Any):
        super().__init__(redis)
        self.responder = responder
        self.sent: list[dict[str, Any]] = []

    async def publish(self, stream: str, type: str, payload: dict[str, Any], **kw: Any) -> str:
        out = await super().publish(stream, type, payload, **kw)
        if stream == BRIDGE_CONTROL and type == "media.fetch":
            self.sent.append(payload)
            res = self.responder(payload["request_id"])
            if res is not None:
                await self.redis.rpush(mf.result_key(payload["request_id"]), orjson.dumps(res))
        return out


ORG = uuid.uuid4()


async def test_fetch_thanh_cong_va_gui_dung_lenh(redis: Redis) -> None:
    data = fake_voice_bytes("alo")
    bus = FakeBridgeBus(redis, lambda rid: _ok(rid, data))
    got = await mf.fetch(bus, ORG, kind="voice", media_ref=REF, **WHO, timeout=3)
    assert (got.data, got.mime, got.size) == (data, "audio/ogg", len(data))
    assert got.sha256 == hashlib.sha256(data).hexdigest()
    assert (bus.sent[0]["external_group_id"], bus.sent[0]["sender_external_id"]) == ("g1", "u1")
    assert bus.sent[0]["media_ref"] == REF and bus.sent[0]["channel"] == "zalo" and bus.sent[0]["session_id"] == "s1"
    assert await redis.llen(mf.result_key(bus.sent[0]["request_id"])) == 0     # đã xoá khoá kết quả


async def test_fetch_tu_choi_ref_la_khong_phat_lenh(redis: Redis) -> None:
    bus = FakeBridgeBus(redis, lambda rid: _ok(rid, b"OggS"))
    for ref in ("https://evil.example/a.ogg", "zalo:s1:../x", "x"):
        with pytest.raises(mf.MediaFetchError) as e:
            await mf.fetch(bus, ORG, kind="voice", media_ref=ref, **WHO, timeout=1)
        assert e.value.code == "bad_request"
    with pytest.raises(mf.MediaFetchError):
        await mf.fetch(bus, ORG, kind="video", media_ref=REF, **WHO, timeout=1)
    for bad in ({"chat": "g1", "sender": ""}, {"chat": None, "sender": "u1"}, {"chat": "g1", "sender": None}):   # thiếu ràng buộc
        with pytest.raises(mf.MediaFetchError) as e2:
            await mf.fetch(bus, ORG, kind="voice", media_ref=REF, **bad, timeout=1)  # type: ignore[arg-type]
        assert e2.value.code == "bad_request"
    assert bus.sent == []


@pytest.mark.parametrize(("make", "code"), [
    (lambda rid, d: {"request_id": rid, "ok": False, "error": "too_large"}, "too_large"),
    (lambda rid, d: {"request_id": rid, "ok": False, "error": "not_found"}, "not_found"),
    (lambda rid, d: {"request_id": rid, "ok": False, "error": "<script>lạ"}, "download_failed"),      # mã lạ không lọt ra ngoài
    (lambda rid, d: _ok(rid, d, sha256="0" * 64), "download_failed"),                      # sha256 không khớp
    (lambda rid, d: _ok(rid, d, size=len(d) + 1), "download_failed"),                      # size khai sai
    (lambda rid, d: _ok(rid, d, data_b64="!!không phải base64!!"), "download_failed"),
    (lambda rid, d: _ok(rid, d, request_id="id-khac"), "download_failed"),                 # kết quả của yêu cầu khác
    (lambda rid, d: _ok(rid, b"\x89PNG\r\n\x1a\n....."), "mime_not_allowed"),               # byte là ảnh nhưng kind voice
    (lambda rid, d: _ok(rid, b"MZ\x90\x00 exe", mime="audio/ogg"), "mime_not_allowed"),     # bridge khai audio/ogg, byte là exe
])
async def test_fetch_khong_tin_bridge(redis: Redis, make: Any, code: str) -> None:
    data = fake_voice_bytes("x")
    bus = FakeBridgeBus(redis, lambda rid: make(rid, data))
    with pytest.raises(mf.MediaFetchError) as e:
        await mf.fetch(bus, ORG, kind="voice", media_ref=REF, **WHO, timeout=2)
    assert e.value.code == code


async def test_fetch_vuot_tran_byte_du_bridge_noi_nho(redis: Redis) -> None:
    big = b"OggS" + b"\x00" * mf.MAX_VOICE_BYTES      # > 5 MB
    bus = FakeBridgeBus(redis, lambda rid: _ok(rid, big))
    with pytest.raises(mf.MediaFetchError) as e:
        await mf.fetch(bus, ORG, kind="voice", media_ref=REF, **WHO, timeout=5)
    assert e.value.code == "too_large"
    img = b"\x89PNG\r\n\x1a\n" + b"\x00" * mf.MAX_VOICE_BYTES      # 5 MB: dưới trần ảnh 8 MB ⇒ qua
    ok = await mf.fetch(FakeBridgeBus(redis, lambda rid: _ok(rid, img, mime="image/png")), ORG, kind="image", media_ref=REF, **WHO, timeout=5)
    assert ok.mime == "image/png"


async def test_fetch_bridge_im_lang_la_timeout(redis: Redis) -> None:
    bus = FakeBridgeBus(redis, lambda rid: None)
    with pytest.raises(mf.MediaFetchError) as e:
        await mf.fetch(bus, ORG, kind="image", media_ref=REF, **WHO, timeout=1)
    assert e.value.code == "timeout"
    assert mf.friendly("timeout") and "http" not in mf.friendly("blocked_host")


def test_cau_than_thien_co_cho_moi_ma_loi() -> None:
    for code in ("too_large", "mime_not_allowed", "blocked_host", "not_found", "session_inactive", "timeout", "download_failed",
                 "bad_request", "engine_unavailable", "engine_error", "engine_timeout", "skipped_budget", "gave_up"):
        assert mf.FRIENDLY[code].endswith(".") and len(mf.FRIENDLY[code]) > 20
    assert mf.friendly("không-biết") == "Gen chưa đọc được file này."


def test_fake_bytes_qua_duoc_kiem_mime() -> None:
    assert mf.sniff_mime(fake_voice_bytes("a")) == "audio/ogg"
    assert mf.sniff_mime(fake_image_bytes("a", "b")) == "image/png"
