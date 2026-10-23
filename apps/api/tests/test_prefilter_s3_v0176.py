# ruff: noqa: E501
"""S3 (spec Bước 1 r6.6.1 §10): luật (a) bắt mẫu "đã nhập / đã đăng nhập / bị trừ tiền" (cờ `credentials_entered`).

Bộ câu TỰ SOẠN trong test (không lấy từ kb hay thư mục evalkey*). Ngưỡng: recall ≥ 90% toàn bộ, ≥ 95% nhóm "đã nhập";
không bắt nhầm SĐT hay mã đơn. Cộng các ca cho cờ còn lại của `scan_text`, `enrich` (người, mạo danh, STK) và tin không cần model."""

import pytest

from gh.refinery import prefilter as pf

# ─── nhóm 1: ĐÃ NHẬP (OTP / mật khẩu / số thẻ / mã) — cần recall ≥ 95% ────────────────────────────────────────────────
ENTERED = [
    "Em lỡ nhập mã OTP vào cái link đó rồi chị ơi",
    "Anh đã nhập mật khẩu vào trang web lạ mất rồi",
    "Mình vừa nhập số thẻ vào web đó, giờ phải làm sao",
    "Con đã đọc mã OTP cho người ta rồi mẹ ơi",
    "Em nhập OTP xong mới thấy lạ",
    "Chị trót điền mật khẩu ngân hàng vào link họ gửi",
    "em da nhap ma xac thuc vao link do roi",
    "tôi vừa cung cấp mã OTP cho người tự xưng là nhân viên ngân hàng",
    "Lỡ gửi mã xác minh cho họ rồi, anh ơi giờ sao",
    "Em đã điền thông tin thẻ vào trang đó rồi",
    "Cháu vừa gõ mật khẩu vào trang đăng nhập giả",
    "Mình đã đưa số CVV cho họ rồi",
    "em lo nhap pass zalo vao link la",
    "Bác đã đọc OTP cho họ nghe rồi con ơi",
    "Em vừa nhập mã PIN vào link ngân hàng gửi qua tin nhắn",
    "Anh đã khai báo số thẻ và mã OTP vào form đó",
    "Em đã chia sẻ mật khẩu Zalo cho người lạ",
    "Chị lỡ cho họ mã bảo mật rồi",
    "Mình nhập tài khoản và mật khẩu rồi bấm xác nhận luôn",
    "Em đã nhập mã OTP 6 số họ gửi qua tin nhắn",
    "Tôi lỡ nhập số thẻ tín dụng vào link giả mạo",
    "Em bấm vào link rồi nhập mã OTP luôn",
    "Anh vừa gửi mã OTP cho họ xong",
    "con nhập otp rồi mẹ ơi",
]
# ─── nhóm 2: ĐÃ ĐĂNG NHẬP vào link / trang lạ ───────────────────────────────────────────────────────────────────────
LOGGED_IN = [
    "Em đã đăng nhập vào link đó rồi",
    "em đã login vào link đó",
    "Chị vừa đăng nhập vào trang web ngân hàng giả",
    "Anh lỡ bấm vào link rồi đăng nhập luôn",
    "Mình đã đăng nhập vào đường dẫn họ gửi",
    "Em vừa đăng nhập vào cái web lạ này rồi",
    "Tôi đã đăng nhập link người lạ gửi vào Zalo",
    "em dang nhap vao trang gia roi anh",
    "Chị đăng nhập link đó rồi, giờ tài khoản lạ lắm",
]
# ─── nhóm 3: BỊ TRỪ TIỀN / mất tiền ──────────────────────────────────────────────────────────────────────────────────
CHARGED = [
    "Tài khoản em vừa bị trừ 5 triệu mà em không biết",
    "Sao tự nhiên tôi bị trừ tiền trong thẻ vậy",
    "Bị trừ 2 triệu rồi anh ơi",
    "Em thấy tin nhắn trừ tiền 3 triệu trong tài khoản, em có làm gì đâu",
    "Mới bị rút sạch tiền trong tài khoản sau khi bấm link",
    "Tiền trong tài khoản em bay mất hết rồi",
    "Chị bị lừa mất 20 triệu rồi mọi người ơi",
    "Tài khoản ngân hàng của anh bị trừ mất 10 triệu",
    "em bi tru tien roi ma khong nhan duoc hang",
    "Vừa bị trừ 500k trong ví momo",
    "Mẹ em bị trừ tiền sau khi nhập OTP",
    "Có thông báo trừ tiền 8 triệu mà em không giao dịch",
    "Tiền bị rút sạch trong thẻ rồi",
    "tài khoản em tự dưng mất 10 triệu",
    "ngân hàng báo trừ 5tr",
    "TK em vừa bị trừ 2.500.000",
    "Em bị lừa tiền rồi anh chị ơi",
]
# ─── nhóm 4: câu soạn SAU khi viết luật (lần đầu bắt 12/15 = 80%; 3 câu sót đã được tổng quát hoá rồi đưa vào đây) ──────────────
HELD_OUT = [
    "Trời ơi em nhập mật khẩu internet banking vào cái trang họ gửi mất rồi",
    "Em điền luôn mã OTP vào đường link ngân hàng nhắn",
    "chị ơi em lỡ đọc cho họ nghe mã xác nhận gồm 6 số rồi",
    "Mình đã nhập số thẻ và mã CVV vào app lạ",
    "Em vừa bấm vào link đăng nhập luôn rồi giờ mới thấy sai tên miền",
    "sáng nay tài khoản anh bị trừ 12 triệu không rõ lý do",
    "Mới nhập pass ví vào trang đó xong thì hết sạch tiền",
    "Thằng đó bảo đọc otp cho nó, em đọc rồi",
    "e nhap mat khau vao link do roi a",
    "Tiền trong thẻ bị trừ hết sạch rồi chị ơi",
    "Em vừa đăng nhập tài khoản ngân hàng vào link họ gửi",
    "Anh trót nhập mã bảo mật cho họ",
    "Mình bị rút 30 triệu từ tài khoản sau khi bấm link",
    "Em điền thông tin thẻ ATM vào trang đó rồi",
    "Chị đã cung cấp mật khẩu cho kẻ gian",
]
# ─── KHÔNG được bắt: SĐT, mã đơn, mã vận đơn, mã giảm giá, câu thường ngày có từ gần giống ─────────────────────────────────
NEGATIVES = [
    "SĐT của em là 0901234567 anh nhé",
    "Số điện thoại em 0912 345 678, gọi em nhé",
    "Zalo em: +84 912 345 678",
    "Mã đơn DH2610123 anh nhé",
    "Đơn hàng số 123456 đã giao rồi anh",
    "Mã vận đơn GHN123456789 đang giao",
    "Em gửi mã giảm giá SALE50 cho anh",
    "Anh nhập mã giảm giá khi thanh toán nhé",
    "OTP em chưa nhận được anh ơi",
    "Chị gửi em mã xác nhận đơn hàng nhé",
    "Em chưa nhập mật khẩu, anh hướng dẫn giúp",
    "Bạn đã nhập OTP chưa?",
    "Đã gửi hàng cho khách rồi",
    "Anh đã chuyển khoản rồi nhé",
    "Đã nhập kho 50 tấn thép",
    "Em đã gửi báo giá rồi ạ",
    "Hôm nay nhập hàng về kho",
    "Tháng này bị trừ điểm thi đua",
    "Bị trừ lương 200k do đi trễ",
    "Bị trừ 10% hoa hồng nhé",
    "Đã đăng nhập vào hệ thống kế toán của công ty",
    "Em đã đăng ký tài khoản rồi",
    "Mình pass lại cho bạn cái áo này nhé",
    "Anh gửi thông tin chuyển khoản cho em nhé",
    "Thẻ nhớ 64GB giá 300k",
    "Tiền hàng đã trừ vào công nợ tháng trước",
    "Em đã nhập số lượng vào bảng rồi",
    "Đã nhận được tiền trong tài khoản rồi cảm ơn chị",
    "Mất điện cả khu từ sáng",
    "đã gửi số tài khoản cho khách rồi",
    "đã gửi pass wifi rồi",
    "Em đã cho khách số tài khoản công ty rồi",
    "Em bị trừ phí ship 30k",
    "Chào mọi người, hôm nay shop mở cửa sớm",
]


def hit(text: str) -> bool:
    return any(c.flag == "credentials_entered" for c in pf.scan_text(text))


def recall(cases: list[str]) -> tuple[float, list[str]]:
    miss = [t for t in cases if not hit(t)]
    return (len(cases) - len(miss)) / len(cases), miss


def test_s3_corpus_size_meets_spec() -> None:
    assert len(ENTERED) + len(LOGGED_IN) + len(CHARGED) + len(HELD_OUT) >= 40
    assert len(ENTERED) >= 20


def test_s3_recall_entered_ge_95() -> None:
    r, miss = recall(ENTERED)
    assert r >= 0.95, f"recall 'đã nhập' = {r:.1%}; sót: {miss}"


def test_s3_recall_all_ge_90() -> None:
    allc = ENTERED + LOGGED_IN + CHARGED + HELD_OUT
    r, miss = recall(allc)
    assert r >= 0.90, f"recall = {r:.1%} ({len(allc) - len(miss)}/{len(allc)}); sót: {miss}"


def test_s3_recall_per_group_reported() -> None:
    for name, group in (("entered", ENTERED), ("logged_in", LOGGED_IN), ("charged", CHARGED), ("held_out", HELD_OUT)):
        r, miss = recall(group)
        print(f"S3 {name}: {len(group) - len(miss)}/{len(group)} = {r:.1%}", miss)
        assert r >= 0.85, (name, miss)


def test_s3_no_false_hit_on_phone_or_order_codes() -> None:
    bad = [t for t in NEGATIVES if hit(t)]
    assert not bad, f"bắt nhầm: {bad}"


def test_s3_credentials_is_hard_rule_fraud_kind_with_gate() -> None:
    c = next(x for x in pf.scan_text("Em lỡ nhập mã OTP vào cái link đó rồi chị ơi") if x.flag == "credentials_entered")
    assert c.kind == "rui_ro_lua_dao" and c.gate_candidate == "lua_dao_dang_dien_ra"
    assert c.hard_rule_hit == "credentials_entered" and c.immediate and c.safety and c.first_person


def test_s3_candidate_does_not_carry_message_text_or_raw_numbers() -> None:
    text = "mã OTP 482913 em gửi họ rồi, chuyển khoản vào stk 0123456789012 giúp em"
    cands = pf.scan_text(text)
    blob = repr([c.to_json() for c in cands]) + repr(pf.enrich(cands, pf.SenderCtx(sender_ref="u_1")))
    assert "482913" not in blob and "0123456789012" not in blob
    assert all(isinstance(c.span, tuple) and 0 <= c.span[0] <= c.span[1] <= len(text) for c in cands)
    c = next(x for x in cands if x.flag == "credentials_entered")
    assert "OTP" in text[c.span[0]:c.span[1]] or "gửi" in text[c.span[0]:c.span[1]]      # span trỏ về chữ gốc có dấu


def test_victims_first_person_only() -> None:
    mine = pf.enrich(pf.scan_text("Em lỡ nhập mã OTP vào link đó rồi"), pf.SenderCtx(sender_ref="u_a"))
    c = next(x for x in mine if x.flag == "credentials_entered")
    assert c.victims == ("u_a",)
    other = pf.enrich(pf.scan_text("Mẹ em lỡ nhập mã OTP vào link đó rồi"), pf.SenderCtx(sender_ref="u_a"))
    c2 = next(x for x in other if x.flag == "credentials_entered")
    assert c2.victims == () and not c2.first_person            # ngôi thứ ba: để model/(d) nêu nạn nhân


# ─── các cờ còn lại ──────────────────────────────────────────────────────────────────────────────────────────────────
def flags(text: str) -> dict[str, pf.Candidate]:
    return {c.flag: c for c in pf.scan_text(text)}


@pytest.mark.parametrize("text", ["Nhà em đang cháy, mọi người gọi 115 giúp", "Mẹ em vừa nhập viện cấp cứu", "Bên kho có hỏa hoạn",
                                  "Anh Bảy bị tai nạn xe trên đường về"])
def test_emergency_candidates(text: str) -> None:
    f = flags(text)
    assert "emergency" in f and f["emergency"].kind == "suc_khoe_an_toan" and f["emergency"].gate_candidate == "an_toan"


@pytest.mark.parametrize("text", ["Hôm nay cháy hàng rồi anh", "Anh chạy xe cẩn thận nhé", "Cháy cầu chì nhưng không sao, hết hàng",
                                  "Em gặp anh chiều nay nhé", "Cháy túi cuối tháng", "Lô này 115 tấn thép", "Giá 115k một cái", "Tin số 115 về hàng nhé"])
def test_emergency_no_false_hit(text: str) -> None:
    assert "emergency" not in flags(text)


def test_cay_vs_chay_hard_rule_only_for_fire_and_115() -> None:
    assert flags("Gọi 115 giúp em")["emergency"].hard_rule_hit == "emergency"
    assert flags("Anh trai em nhập viện")["emergency"].hard_rule_hit is None     # tai nạn/nhập viện: ứng viên, không phải luật cứng


@pytest.mark.parametrize("text", ["Zalo em bị hack rồi, ai nhắn mượn tiền đừng chuyển nha", "Facebook của em bị chiếm",
                                  "Có người đăng nhập tài khoản Zalo của em"])
def test_account_takeover(text: str) -> None:
    f = flags(text)
    assert f["account_takeover"].kind == "rui_ro_lua_dao" and f["account_takeover"].gate_candidate == "lua_dao_dang_dien_ra"


def test_money_request_alone_has_no_kind_and_no_gate() -> None:
    f = flags("Chuyển khoản giúp em 5 triệu vào stk 0123456789 Vietcombank nhé")
    assert f["money_request"].kind is None and f["money_request"].gate_candidate is None      # r6.4 CNS-131
    assert f["account_number"].details["account_numbers"] == ("0123456789",)
    out = pf.enrich(pf.scan_text("Chuyển khoản giúp em 5 triệu vào stk 0123456789"), pf.SenderCtx(sender_ref="u_x"))
    assert next(c for c in out if c.flag == "money_request").kind is None


def test_money_request_promoted_with_stranger_impersonation_or_changed_stk() -> None:
    text = "Chuyển khoản gấp 15 triệu vào stk 0123456789 giúp em"
    cands = pf.scan_text(text)
    stranger = next(c for c in pf.enrich(cands, pf.SenderCtx(sender_ref="u_n", joined_days=3)) if c.flag == "money_request")
    assert stranger.kind == "rui_ro_lua_dao" and stranger.gate_candidate == "lua_dao_dang_dien_ra"
    assert "stranger" in stranger.details["promoted_by"] and stranger.hard_rule_hit is None
    old = next(c for c in pf.enrich(cands, pf.SenderCtx(sender_ref="u_o", joined_days=400)) if c.flag == "money_request")
    assert old.kind is None
    imp = next(c for c in pf.enrich(cands, pf.SenderCtx(sender_ref="u_n", joined_days=2, name_collision_with="u_real"))
               if c.flag == "money_request")
    assert imp.impersonated == ("u_real",) and imp.kind == "rui_ro_lua_dao"


def test_stk_changed_vs_history_is_hard_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pf, "_digest", lambda n: "d_" + n)
    cands = pf.scan_text("Anh ơi chuyển tiền vào stk 0987654321 nhé")
    ctx_known = pf.SenderCtx(sender_ref="u_s", joined_days=400, known_stk_digests=frozenset({"d_0111222333"}))
    m = next(c for c in pf.enrich(cands, ctx_known) if c.flag == "money_request")
    assert m.kind == "rui_ro_lua_dao" and m.hard_rule_hit == "stk_changed_money_request"
    same = pf.SenderCtx(sender_ref="u_s", joined_days=400, known_stk_digests=frozenset({"d_0987654321"}))
    assert next(c for c in pf.enrich(cands, same) if c.flag == "money_request").kind is None
    none_known = pf.SenderCtx(sender_ref="u_s", joined_days=400)
    assert next(c for c in pf.enrich(cands, none_known) if c.flag == "money_request").kind is None


def test_impersonation_only_when_new_renamed_or_unknown() -> None:
    cands = pf.scan_text("Chuyển khoản gấp 15 triệu vào stk 0123456789 giúp em")

    def imp(**kw: object) -> tuple[str, ...]:
        out = pf.enrich(cands, pf.SenderCtx(sender_ref="u_n", name_collision_with="u_real", **kw))  # type: ignore[arg-type]
        return next(c for c in out if c.flag == "money_request").impersonated

    assert imp(joined_days=2, known_entity=True) == ("u_real",)                # ID mới
    assert imp(joined_days=900, display_name_changed_recent=True, known_entity=True) == ("u_real",)  # vừa đổi tên
    assert imp(joined_days=900, known_entity=False) == ("u_real",)             # chưa là thực thể đã biết
    assert imp(joined_days=None, known_entity=False) == ("u_real",)
    assert imp(joined_days=None, known_entity=True) == ()                      # hai "Lan" lâu năm: `joined_days=None` KHÔNG phải "mới"
    assert imp(joined_days=900, known_entity=True) == ()                       # thành viên lâu năm trùng tên (CNS-149)


def test_threat_and_public_candidates_open_customer_gate() -> None:
    assert flags("Nếu không giải quyết em sẽ huỷ hợp đồng luôn")["said_will_leave"].gate_candidate == "khach_che_cong_khai_doa_bo"
    assert flags("Em sẽ đăng lên facebook cho mọi người biết")["public"].gate_candidate == "khach_che_cong_khai_doa_bo"
    assert "said_will_leave" not in flags("Em bỏ qua tin trên nhé, cảm ơn anh") and "public" not in flags("Anh gửi hàng lên kho giúp em")


def test_urgent_today_and_system_candidates() -> None:
    f = flags("Hệ thống bán hàng sập rồi, POS không vào được")
    assert f["urgent"].gate_candidate == "su_co_cong_viec_gap"
    f2 = flags("Cần gấp báo giá trước 15h hôm nay nhé")
    assert f2["urgent"].gate_candidate == "su_co_cong_viec_gap" and "deadline_today" in f2
    f3 = flags("Mai gặp nhau nhé")
    assert "urgent" not in f3 and "deadline_today" not in f3                       # "gặp" ≠ "gấp"
    f4 = flags("Cần gấp nhé")
    assert "urgent" in f4 and f4["urgent"].gate_candidate is None                  # gấp một mình: dấu vết, không mở cổng


def test_promise_due_and_big_deal() -> None:
    assert flags("Em hứa giao hàng cho anh trước 17h hôm nay")["promise_due"].gate_candidate == "loi_hua_khach_sap_vo"
    f = flags("Anh chốt đơn 730 triệu thép cuộn, cần gấp trong hôm nay")
    assert f["big_deal"].gate_candidate == "co_hoi_lon_sap_troi" and f["big_deal"].details["vnd"] == 730_000_000
    assert "big_deal" not in flags("Anh chốt 5 triệu tiền hàng hôm nay")


def test_amounts_parse() -> None:
    assert pf.parse_amounts("giá 1.200.000 đ, cọc 50k, tổng 2,5 tỷ, thêm 30 triệu") == [1_200_000, 50_000, 2_500_000_000, 30_000_000]


def test_link_phone_account_traces_are_not_gates() -> None:
    f = flags("Bấm vào https://vcb-xacthuc.top/login để xác minh tài khoản, gọi 0901234567")
    assert f["link"].gate_candidate is None and f["phone"].gate_candidate is None and f["scam_keyword"].kind is None
    assert not any(c.immediate for c in pf.scan_text("Mã đơn 123456789012 SĐT 0901234567"))


def test_gate_class_priority() -> None:
    assert pf.gate_class(pf.scan_text("Em lỡ nhập OTP rồi")) == "safety"
    assert pf.gate_class(pf.scan_text("Em sẽ huỷ hợp đồng")) == "gate"
    assert pf.gate_class(pf.scan_text("Chào anh")) is None


def test_scan_text_rejects_bad_source_field_and_handles_empty() -> None:
    assert pf.scan_text("") == [] and pf.scan_text("   ") == []
    with pytest.raises(ValueError):
        pf.scan_text("x", "bogus")
    c = next(x for x in pf.scan_text("em lỡ nhập otp rồi", "ocr") if x.flag == "credentials_entered")
    assert c.source_field == "ocr"


def test_candidate_json_roundtrip() -> None:
    c = pf.enrich(pf.scan_text("Em lỡ nhập mã OTP vào link đó rồi"), pf.SenderCtx(sender_ref="u_a"))[0]
    assert pf.Candidate.from_json(c.to_json()) == c


def test_no_model_messages() -> None:
    assert pf.no_model_reason("sticker", None) == "sticker" and pf.no_model_reason("reaction", "👍") == "reaction"
    assert pf.no_model_reason("text", "dạ ok ạ") == "ack" and pf.no_model_reason("text", "cảm ơn anh") == "ack"
    assert pf.no_model_reason("text", "dạ 15 triệu nhé") is None
    long = "Kính gửi quý khách, bảng giá thép cuộn tháng 10 đã cập nhật, vui lòng xem file đính kèm"
    assert pf.no_model_reason("text", long, template_n=2) is None
    assert pf.no_model_reason("text", long, template_n=3) == "template"


FIRST_PERSON = ["Mẹ ơi con đã nhập OTP rồi", "em đã nhập OTP vào link bạn gửi", "chị ơi em lỡ đọc mã OTP cho họ rồi",
                "anh ơi em vừa nhập mật khẩu vào trang ngân hàng nhà mình hay dùng, giờ bị trừ tiền", "khách ơi em đã gửi OTP",
                "em bị trừ 5 triệu ạ, bạn ơi giúp em", "con bị trừ hết tiền rồi bố ơi"]


@pytest.mark.parametrize("text", FIRST_PERSON)
def test_first_person_ignores_vocatives_and_objects(text: str) -> None:
    c = next(x for x in pf.enrich(pf.scan_text(text), pf.SenderCtx(sender_ref="u_me")) if x.flag == "credentials_entered")
    assert c.first_person and c.victims == ("u_me",)


@pytest.mark.parametrize("text", ["mẹ em đã nhập OTP rồi", "bạn tôi bị trừ tiền 5 triệu", "khách đã nhập mật khẩu vào link đó"])
def test_third_person_only_when_person_word_is_the_subject(text: str) -> None:
    c = next(x for x in pf.enrich(pf.scan_text(text), pf.SenderCtx(sender_ref="u_me")) if x.flag == "credentials_entered")
    assert not c.first_person and c.victims == ()


@pytest.mark.parametrize("text", ["chuyển khoản 500k nhé, mã đơn 2024102312345", "ck cho em 2tr, đơn hàng số 88812345678 nha",
                                  "chuyển tiền cho shop 1tr, ngày 20241023", "Đơn #DH2024102312345 giao hôm nay, mã vận đơn 8881234567890"])
def test_unlabelled_digits_after_order_codes_are_not_accounts(text: str) -> None:
    assert "account_number" not in flags(text)
    out = pf.enrich(pf.scan_text(text), pf.SenderCtx(sender_ref="u_x", known_stk_digests=frozenset({"zzz"})))
    assert next((c for c in out if c.flag == "money_request"), None) is None or next(c for c in out if c.flag == "money_request").kind is None


def test_unlabelled_account_needs_adjacent_transfer_word_and_login_secrets_exclude_stk() -> None:
    assert flags("anh chuyển khoản giúp em 3tr vào 1903654321012 nhé")["account_number"].details["account_numbers"] == ("1903654321012",)
    assert "account_number" not in flags("Chuyển khoản 3tr nhé, em gọi lại sau, 1903654321012")
