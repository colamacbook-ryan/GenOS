"""Facebook cá nhân — CHỈ ĐỌC: nhận biết trạng thái trang, đọc thông báo và danh sách hội thoại (xem trước tin mới
nhất). Trích VĂN BẢN CÓ CẤU TRÚC bằng quy tắc cố định (không đưa HTML thô ra ngoài, model không bao giờ chọn selector).

THÀNH THẬT: bộ chọn dưới đây dựa trên dấu hiệu tương đối bền của facebook.com (cookie `c_user` khi đã đăng nhập, đường
dẫn `/checkpoint/`, link thông báo có `notif_id=`/`notif_t=`, link hội thoại `/messages/t/<id>`, vai trò ARIA) và được
kiểm trên TRANG MẪU lưu sẵn (tests/fixtures) — CHƯA kiểm trên Facebook thật. Giao diện đổi → việc báo lỗi `SELECTOR`
rõ ràng thay vì đoán; Owner nghiệm thu bằng một lần đăng nhập + đọc thật.

GHI (trả lời bình luận, nhắn tin): chỉ khi có permit hợp lệ (ghb.permit). THÀNH THẬT: bộ chọn ghi kiểm trên TRANG
MẪU, CHƯA kiểm trên Facebook thật — Owner nghiệm thu bằng một lần trả lời thật. Nội dung được chèn MỘT lần
(`insert_text`), không mô phỏng gõ phím người.

KHÔNG có: đăng bài, thích, kết bạn; không lách chống bot; gặp checkpoint / CAPTCHA thì trả trạng thái để worker DỪNG,
không vượt.
"""

import re
from typing import Any

from ghb.adapters.base import Adapter, PageState, TargetNotFound

HOME = "https://www.facebook.com/"
NOTIFICATIONS = "https://www.facebook.com/notifications"
INBOX = "https://www.facebook.com/messages/t/"

TIME_RE = re.compile(r"^(vừa xong|just now|\d+\s*(giây|phút|giờ|ngày|tuần|năm|s|m|h|d|w|y|min|mins|hr|hrs)\b.*|"
                     r"hôm qua|yesterday|thứ .+|(mon|tue|wed|thu|fri|sat|sun)\w*)$", re.IGNORECASE)

# Trích trong trang — chỉ đọc DOM, không bấm, không gõ gì.
NOTIF_JS = """(max) => {
  const root = document.querySelector('[role="main"]') || document.body;
  const links = Array.from(root.querySelectorAll('a[href*="notif_id="], a[href*="notif_t="]'));
  const seen = new Set(); const out = [];
  for (const a of links) {
    const row = a.closest('[role="row"], [role="listitem"], [role="article"]') || a;
    const text = (row.innerText || '').trim();
    if (!text || seen.has(text)) continue;
    seen.add(text);
    const label = (row.getAttribute('aria-label') || '') + ' ' +
      Array.from(row.querySelectorAll('[aria-label]')).map((e) => e.getAttribute('aria-label')).join(' ');
    out.push({ text, href: a.href, unread: /unread|chưa đọc/i.test(label) });
    if (out.length >= max) break;
  }
  return out;
}"""

INBOX_JS = """(max) => {
  const lists = Array.from(document.querySelectorAll(
    '[role="navigation"] [role="grid"], [aria-label="Chats"], [aria-label="Đoạn chat"], [role="navigation"]'));
  const root = lists[0] || document.body;
  const links = Array.from(root.querySelectorAll('a[href*="/messages/t/"]'));
  const seen = new Set(); const out = [];
  for (const a of links) {
    const href = a.href;
    if (seen.has(href)) continue;
    seen.add(href);
    const row = a.closest('[role="row"], [role="listitem"], [role="gridcell"]') || a;
    const text = (row.innerText || '').trim();
    if (!text) continue;
    const label = (row.getAttribute('aria-label') || '') + ' ' +
      Array.from(row.querySelectorAll('[aria-label]')).map((e) => e.getAttribute('aria-label')).join(' ');
    out.push({ text, href, unread: /unread|chưa đọc/i.test(label) });
    if (out.length >= max) break;
  }
  return out;
}"""

CAPTCHA_SELECTORS = ('iframe[src*="recaptcha"]', 'iframe[src*="captcha"]', 'iframe[title*="captcha" i]',
                     '#captcha', '[id*="captcha" i]')


ARTICLE = '[role="article"]'
TEXTBOX = '[contenteditable="true"][role="textbox"]'
MESSAGE_ROW = '[role="main"] [role="row"]'
TARGET_ATTR = "data-ghb-target"
# comment_id dạng base64 của Facebook (Y29tbWVudDo…%3D%3D) thường dài 50+ ký tự — cắt 40 thì không bao giờ khớp.
COMMENT_ID_MAX = 128

# Chọn bình luận đích: bài viết ARIA có aria-label bắt đầu "Bình luận"/"Comment" có link chứa ĐÚNG comment_id của
# target_url (đã ký trong permit). Không thấy → 0 (TargetNotFound): KHÔNG đoán sang bình luận được làm nổi hay bình luận
# đầu tiên — trả lời nhầm người là gửi thật dưới tên Owner. Đánh dấu bằng thuộc tính để lấy locator (chỉ đọc).
MARK_COMMENT_JS = """(commentId) => {
  const re = /^(bình luận|comment)/i;
  document.querySelectorAll('[data-ghb-target]').forEach((e) => e.removeAttribute('data-ghb-target'));
  if (!commentId) return 0;
  const arts = Array.from(document.querySelectorAll('[role="article"]'))
    .filter((a) => re.test(a.getAttribute('aria-label') || ''));
  // Chuẩn hoá GIỐNG comment_id() bên Python: giải mã %xx, bỏ ký tự ngoài [0-9A-Za-z_], cắt COMMENT_ID_MAX.
  const norm = (href) => {
    let v = null;
    try { v = new URL(href, location.href).searchParams.get('comment_id'); } catch (e) { return ''; }
    return (v || '').replace(/[^0-9A-Za-z_]/g, '').slice(0, COMMENT_ID_MAX);
  };
  const pick = arts.find((a) => Array.from(a.querySelectorAll('a[href*="comment_id="]'))
    .some((l) => norm(l.getAttribute('href') || '') === commentId));
  if (!pick) return 0;
  pick.setAttribute('data-ghb-target', '1');
  return arts.length;
}""".replace("COMMENT_ID_MAX", str(COMMENT_ID_MAX))

COUNT_JS = """([sel, text]) => Array.from(document.querySelectorAll(sel))
  .filter((e) => !e.querySelector('[contenteditable="true"]') && (e.innerText || '').includes(text)).length"""

WAIT_NEW_JS = """([sel, text, base]) => Array.from(document.querySelectorAll(sel))
  .filter((e) => !e.querySelector('[contenteditable="true"]') && (e.innerText || '').includes(text)).length > base"""


def comment_id(url: str) -> str:
    """comment_id trong URL đích ('' nếu không có) — trả lời bình luận BẮT BUỘC có, không đoán."""
    from urllib.parse import parse_qs, urlsplit

    vals = parse_qs(urlsplit(url).query).get("comment_id") or []
    return re.sub(r"[^0-9A-Za-z_]", "", vals[0])[:COMMENT_ID_MAX] if vals else ""


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and ln.strip() not in ("·", "•")]


def split_time(lines: list[str]) -> tuple[list[str], str | None]:
    if lines and TIME_RE.match(lines[-1]):
        return lines[:-1], lines[-1]
    return lines, None


def parse_notification(raw: dict[str, Any]) -> dict[str, Any] | None:
    lines, when = split_time(_lines(str(raw.get("text") or "")))
    lines = [ln for ln in lines if not re.fullmatch(r"(unread|chưa đọc|đánh dấu là đã đọc|mark as read)", ln, re.I)]
    if not lines:
        return None
    return {"kind": "notification", "who": None, "text": " ".join(lines), "time": when,
            "unread": bool(raw.get("unread")), "link": raw.get("href")}


def parse_conversation(raw: dict[str, Any]) -> dict[str, Any] | None:
    lines, when = split_time(_lines(str(raw.get("text") or "")))
    lines = [ln for ln in lines if not re.fullmatch(r"(unread|chưa đọc|đang hoạt động|active now)", ln, re.I)]
    if not lines:
        return None
    who, rest = lines[0], lines[1:]
    # Dòng xem trước dạng "Bạn: …" / "You: …" = tin mình gửi cuối cùng.
    return {"kind": "inbox", "who": who, "text": " ".join(rest) or "(chưa có tin)", "time": when,
            "unread": bool(raw.get("unread")), "link": raw.get("href")}


class FacebookAdapter(Adapter):
    key = "facebook_personal"
    home = HOME
    write_kinds = ("reply_comment", "send_message")

    async def page_state(self, page: Any, context: Any) -> PageState:
        url = (page.url or "").lower()
        if "/checkpoint" in url or "two_step_verification" in url:
            return "checkpoint"
        if await page.query_selector('form[action*="/checkpoint"]'):
            return "checkpoint"
        if "captcha" in url:
            return "captcha"
        for sel in CAPTCHA_SELECTORS:
            if await page.query_selector(sel):
                return "captcha"
        cookies = await context.cookies("https://www.facebook.com")
        if not any(c.get("name") == "c_user" and c.get("value") for c in cookies):
            return "need_login"
        if "/login" in url:
            return "need_login"
        return "ok"

    async def handle(self, page: Any, context: Any) -> str | None:
        return None      # tên hiển thị: không đoán từ DOM (dễ sai) — Owner đặt nhãn tài khoản

    async def read(self, page: Any, what: str, max_items: int) -> list[dict[str, Any]]:
        if what == "notifications":
            await page.goto(NOTIFICATIONS, wait_until="domcontentloaded")
            await page.wait_for_selector('a[href*="notif_id="], a[href*="notif_t="], [role="main"]', timeout=15_000)
            raws = await page.evaluate(NOTIF_JS, max_items)
            parse = parse_notification
        elif what == "inbox":
            await page.goto(INBOX, wait_until="domcontentloaded")
            await page.wait_for_selector('a[href*="/messages/t/"], [role="navigation"]', timeout=15_000)
            raws = await page.evaluate(INBOX_JS, max_items)
            parse = parse_conversation
        else:
            raise ValueError(f"không đọc được mục {what}")
        return [x for x in (parse(r) for r in raws if isinstance(r, dict)) if x is not None]

    # ─── ghi (selector cố định; chỉ chạy sau ghb.permit.check) ──────────────────────────────────────────────
    async def open_target(self, page: Any, action: str, target_url: str) -> None:
        await page.goto(target_url, wait_until="domcontentloaded")
        if action == "reply_comment":
            await page.wait_for_selector(ARTICLE, timeout=15_000)
        else:
            await page.wait_for_selector(f'[role="main"] {TEXTBOX}, [role="main"]', timeout=15_000)

    async def compose(self, page: Any, action: str, text: str, target_url: str = "") -> None:
        if action == "reply_comment":
            # comment_id lấy từ target_url đã ký trong permit — KHÔNG từ page.url (chuyển hướng có thể làm mất).
            cid = comment_id(target_url)
            if not cid:
                raise TargetNotFound("đường dẫn đích không có comment_id")
            if not await page.evaluate(MARK_COMMENT_JS, cid):
                raise TargetNotFound("không thấy bình luận đích")
            target = page.locator(f"[{TARGET_ATTR}]")
            btn = target.locator('[role="button"]').filter(has_text=re.compile(r"^\s*(Phản hồi|Reply)\s*$", re.I))
            if await btn.count() == 0:
                raise TargetNotFound("bình luận không có nút Phản hồi")
            await btn.first.click()
            box = target.locator(TEXTBOX)
            try:
                await box.first.wait_for(state="visible", timeout=5_000)
            except Exception as e:  # noqa: BLE001
                raise TargetNotFound("không thấy ô trả lời") from e
            sel = ARTICLE
        elif action == "send_message":
            box = page.locator(f'[role="main"] {TEXTBOX}')
            if await box.count() == 0:
                raise TargetNotFound("không thấy ô soạn tin")
            sel = MESSAGE_ROW
        else:
            raise TargetNotFound("hành động lạ")
        base = await page.evaluate(COUNT_JS, [sel, text])
        await page.evaluate("(n) => { window.__ghbBase = n; }", base)
        await box.first.focus()
        await page.keyboard.insert_text(text)       # MỘT lần chèn — không gõ từng phím

    async def submit(self, page: Any, action: str) -> None:
        await page.keyboard.press("Enter")

    async def confirm_sent(self, page: Any, action: str, text: str, timeout_ms: int) -> bool:
        sel = ARTICLE if action == "reply_comment" else MESSAGE_ROW
        try:
            base = await page.evaluate("() => window.__ghbBase || 0")
            await page.wait_for_function(WAIT_NEW_JS, arg=[sel, text, base], timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001 — hết giờ / trang đóng: không xác nhận được
            return False
