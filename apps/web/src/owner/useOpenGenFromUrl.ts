/**
 * Mở Bản tin Gen / thẻ "Hôm nay của Sếp" từ link của chuông (`?gen=`) — dùng chung cho khung Console (AppShell) và Mặt tiền
 * Owner (OwnerShell). Tách khỏi AppShell.tsx ở v0.1.55 (G5) để cả hai khung dùng lại, không chép logic (và để AppShell.tsx chỉ
 * xuất component — Fast Refresh). Hành vi của Console giữ nguyên: không truyền `targets` ⇒ ở lại trang hiện tại.
 */
import { useEffect, useRef } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { genBusy, loadConversation } from '../gen/genClient';
import { useGenStore } from '../gen/genStore';
import { errorDetail, errorText } from '../lib/errorText';
import { toast } from '../lib/toast';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** v0.1.54 (Gen hướng dẫn): `?gen=coach` — chuông "Gen hướng dẫn" dẫn tới thẻ "Hôm nay của Sếp". */
const COACH_PARAM = 'coach';
/** Gen tắt mà gặp `?gen=coach` ⇒ đưa Sếp tới "Việc Sếp cần làm" (cùng nội dung, không cần khung Gen). */
const COACH_FALLBACK_PATH = '/guide/viec-sep';

/**
 * v0.1.41 (F-8): mở Bản tin Gen từ chuông — link `/overview?gen=<conversation_id>`. Mở khung Gen, tải hội thoại rồi
 * bỏ tham số `gen` khỏi địa chỉ (replace — nút Lùi không mở lại). Mã sai dạng ⇒ bỏ qua (chỉ gỡ tham số). Gen đang
 * trả lời câu khác ⇒ GIỮ tham số và chờ lượt đó xong mới mở (không đè câu trả lời đang viết).
 *
 * v0.1.54: `?gen=coach` — bỏ tham số, mở khung Gen và đặt cờ `coachFocus` để thẻ "Hôm nay của Sếp" cuộn tới + mở rộng.
 * Gen TẮT (`genOn` false) ⇒ `navigate('/guide/viec-sep', { replace: true })`. Hành vi với mã hội thoại giữ nguyên: Gen tắt
 * thì tham số UUID không làm gì.
 *
 * v0.1.55 (G5): xuất ra cho OwnerShell (Mặt tiền) dùng lại, không chép logic. `targets` cho phép Mặt tiền chọn trang đáp
 * sau khi bỏ tham số: `?gen=coach` → `/owner` (thẻ Hôm nay của Sếp), `?gen=<mã hội thoại>` → `/owner/gen`. Không truyền ⇒
 * giữ nguyên trang hiện tại như Console.
 */
export interface OpenGenTargets {
  coachPath?: string;
  conversationPath?: string;
}

export function useOpenGenFromUrl(userId: string | null, genOn: boolean, targets: OpenGenTargets = {}): void {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const busy = useGenStore((s) => s.busy);
  const warned = useRef<string | null>(null);
  useEffect(() => {
    if (!userId) return;
    const params = new URLSearchParams(search);
    const cid = params.get('gen');
    if (cid === null) return;
    if (cid === COACH_PARAM) {
      if (!genOn) {
        navigate(COACH_FALLBACK_PATH, { replace: true });
        return;
      }
      params.delete('gen');
      const rest = params.toString();
      navigate((targets.coachPath ?? pathname) + (rest ? `?${rest}` : ''), { replace: true });
      useGenStore.getState().setOpen(userId, true);
      useGenStore.getState().setCoachFocus(true);
      return;
    }
    if (!genOn) return;
    if (UUID_RE.test(cid) && (busy || genBusy())) {
      useGenStore.getState().setOpen(userId, true);
      if (warned.current !== cid) {
        warned.current = cid;
        toast('Gen đang trả lời — bản tin sẽ mở khi xong', 'warn');
      }
      return;
    }
    warned.current = null;
    params.delete('gen');
    const rest = params.toString();
    navigate((UUID_RE.test(cid) ? (targets.conversationPath ?? pathname) : pathname) + (rest ? `?${rest}` : ''), { replace: true });
    if (!UUID_RE.test(cid)) return;
    useGenStore.getState().setOpen(userId, true);
    loadConversation(cid, userId).then(
      (r) => {
        if (r === 'missing') toast('Không mở được bản tin — có thể đã quá hạn lưu', 'bad');
        else if (r === 'busy') toast('Gen đang trả lời — mở bản tin sau từ "Hội thoại cũ"', 'warn');
      },
      (e: unknown) => {
        const detail = errorDetail(e);
        toast(`Không mở được bản tin — ${errorText(e)}${detail ? ` (Chi tiết kỹ thuật: ${detail})` : ''}`, 'bad');
      },
    );
  }, [userId, genOn, pathname, search, navigate, busy, targets.coachPath, targets.conversationPath]);
}
