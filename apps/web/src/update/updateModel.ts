import type { SystemUpdate, SystemUpdateBlockReason } from '@gen-harness/contracts';

export const UPDATE_COMMAND = '~/.gen-harness/bin/genh update';
export const UPDATE_KEY = ['system', 'update'] as const;
/** v0.1.53 (F-99): lệnh cố định bật linger (tiến trình nền chạy cả khi không ai đăng nhập) — chạy một lần, không dấu chấm sau lệnh. */
export const LINGER_COMMAND = 'sudo loginctl enable-linger $USER';
/** v0.1.55: lệnh bật lại người gác cập nhật (trình nhận yêu cầu của nút Cập nhật ngay + lịch đêm) trên máy chủ. */
export const WATCHER_COMMAND = 'genh auto-update enable';
/** Câu dẫn trên lệnh khi lệnh KHÔNG phải để cập nhật tay (stalled: linger / trình nhận yêu cầu lỗi). */
export const COMMAND_LABEL_SERVER = 'Lệnh chạy một lần trên máy chủ:';

/**
 * v0.1.55 (G2): lý do + việc cần làm khi KHÔNG hiện nút "Cập nhật ngay" (api `request_block_reason`).
 * `title` = câu ngắn nêu lý do; `body` = giải thích; `action` = việc Sếp cần làm; `command` (nếu có) = lệnh chạy trên máy chủ.
 * Luật cứng: KHÔNG BAO GIỜ hiện lệnh mà không có câu lý do — mọi ca có `command` đều có `title` + `body` khác rỗng.
 */
export interface BlockReasonCopy {
  title: string;
  body: string;
  action: string;
  command?: string;
}

/** Hàm thuần: mã `request_block_reason` → câu hiển thị. Chuỗi cố định (không lấy chữ nào từ máy chủ). */
export function blockReasonCopy(reason: SystemUpdateBlockReason): BlockReasonCopy {
  switch (reason) {
    case 'not_owner':
      return {
        title: 'Chỉ Owner cập nhật được — nhờ Owner bấm',
        body: 'Cập nhật khởi động lại cả hệ thống nên chỉ tài khoản Owner được bấm.',
        action: 'Nhờ Owner mở mục này và bấm Cập nhật ngay.',
      };
    case 'genh_unlinked':
      return {
        title: 'Máy chủ chưa bật cập nhật bằng nút bấm',
        body: 'Máy chủ chưa có trình nhận yêu cầu cập nhật từ Console (hoặc thư mục nhận yêu cầu không ghi được), nên bấm nút cũng không ai làm.',
        action: 'Chạy lệnh dưới đây một lần trên máy chủ — lần sau chỉ cần bấm nút ở đây.',
        command: UPDATE_COMMAND,
      };
    case 'watcher_stalled':
      return {
        title: 'Người gác cập nhật đang lỗi',
        body: 'Người gác trên máy chủ (nhận yêu cầu từ nút Cập nhật ngay và chạy lịch đêm) đang báo lỗi, nên yêu cầu bấm nút sẽ nằm đó không ai làm.',
        action: 'Chạy lệnh dưới đây trên máy chủ để bật lại người gác, rồi bấm Cập nhật ngay.',
        command: WATCHER_COMMAND,
      };
    case 'in_progress':
      return {
        title: 'Đang cập nhật…',
        body: 'Hệ thống đang nhận hoặc chạy một lần cập nhật nên chưa bấm thêm được.',
        action: 'Chờ khoảng 2–5 phút — trang tự tải lại khi xong.',
      };
    case 'up_to_date':
      return {
        title: 'Đang là bản mới nhất',
        body: 'Chưa có bản nào mới hơn bản đang chạy.',
        action: 'Không cần làm gì.',
      };
  }
}

/**
 * Lý do cần HIỆN trên thẻ khi có bản mới mà nút bị ẩn: Owner không bấm được / máy chủ chưa nhận yêu cầu / người gác lỗi.
 * `in_progress` (thẻ đang hiện tiến trình) và `up_to_date` (đã nói "Đang dùng bản mới nhất") đã có câu riêng nên không lặp.
 * Bản không cài bằng genh (không có hộp thư chung) không có lệnh genh để chạy ⇒ không hiện lý do `genh_unlinked`.
 */
export function blockOf(d: Pick<SystemUpdate, 'request_block_reason' | 'linked'>): BlockReasonCopy | undefined {
  const r = d.request_block_reason;
  if (r === 'not_owner' || r === 'watcher_stalled') return blockReasonCopy(r);
  if (r === 'genh_unlinked' && d.linked) return blockReasonCopy(r);
  return undefined;
}

/** v0.1.42: khi đang cập nhật (requested/running) hỏi lại mỗi 4 giây — dòng báo ở Hôm nay tự đổi sang xong/lỗi. */
export function updatePollMs(state: string | null | undefined): number | false {
  return state === 'requested' || state === 'running' ? 4000 : false;
}

type StepState = 'done' | 'active' | 'todo';
export type UpdateView =
  | { kind: 'hidden'; block?: BlockReasonCopy }
  | {
      kind: 'available' | 'working' | 'finished' | 'failed' | 'stalled';
      tone: 'accent' | 'ok' | 'warn' | 'bad';
      title: string;
      kicker: string;
      body?: string;
      /** Nguyên văn thông điệp genh (mã lỗi, lệnh, bản sao lưu) — hiện trong "Chi tiết kỹ thuật". */
      detail?: string;
      showCommand?: boolean;
      /** v0.1.53 (F-99): lệnh hiện trong khối chép được (chuỗi cố định); thiếu ⇒ `UPDATE_COMMAND`. */
      command?: string;
      /** v0.1.53: câu dẫn trên lệnh; thiếu ⇒ câu mặc định của thẻ ("Chạy lệnh này một lần trên máy chủ…"). */
      commandLabel?: string;
      /** v0.1.55: vì sao nút "Cập nhật ngay" bị ẩn (lý do + việc cần làm + lệnh); thẻ hiện khối này thay cho nút. */
      block?: BlockReasonCopy;
      steps: Array<{ label: string; state: StepState }>;
    };

/** Lỗi cập nhật còn hiện trong 26 giờ (khớp UPDATE_FAILED_RECENT của API): lịch đêm 03:00 + trễ ngẫu nhiên tới 30 phút ⇒ hai lần
 * thử liền nhau cách tới 24 giờ 30 phút; hạn 24 giờ làm thẻ biến mất rồi hiện lại giữa hai lần. */
const RECENT_MS = 26 * 3600 * 1000;
/** Thời gian chín của lịch tự cập nhật đêm (genh `selfupdate.NightlyMinAge`): chỉ cài bản đã là bản chính thức ≥ 24 giờ. */
export const NIGHTLY_MIN_AGE_MS = 24 * 3600 * 1000;

/** Giờ lịch đêm chạy (genh `internal/autoupdate`: ~03:00 giờ máy). */
export const NIGHTLY_HOUR = 3;

/** Bản chính thức mà lịch đêm có thể chọn + lúc đủ thời gian chín 24 giờ (api `nightly_candidates`, v0.1.53). */
export type NightlyCandidate = { tag: string; eligible_at: string };

/** Lần chạy ~03:00 (giờ trình duyệt) đầu tiên không sớm hơn `from`. */
function nextNightlyRun(from: number): Date {
  const run = new Date(from);
  run.setHours(NIGHTLY_HOUR, 0, 0, 0);
  if (run.getTime() < from) run.setDate(run.getDate() + 1);
  return run;
}

/** "dd/mm" của ngày chạy (ngày của chính mốc 03:00). */
function ddmm(d: Date): string {
  return `${String(d.getDate()).padStart(2, '0')}/${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** Số lần ~03:00 tối đa xét: ứng viên chỉ chờ ≤ 24 giờ nên 2 đêm là đủ; dư vài đêm phòng lệch đồng hồ. */
const NIGHTLY_LOOKAHEAD = 5;

/**
 * v0.1.33: lịch đêm (~03:00) tự cài bản mới vào đêm nào — `published_at` là lúc bản đó thành bản chính thức
 * (gh/system_api/update.py official_since); cài ở lần ~03:00 đầu tiên sau khi bản đủ 24 giờ (và sau `now`). Tính theo
 * giờ trình duyệt, dạng "Tự cài đêm 30/09 (~03:00)" (ngày của chính mốc 03:00). null khi không biết lúc phát hành.
 *
 * v0.1.53 (F-96): lịch đêm chọn bản CAO NHẤT đã đủ 24 giờ, không phải lúc nào cũng là bản mới nhất — khi bản ra dồn
 * dập thì bản mới nhất còn "chưa chín" nhưng bản liền trước thì đã. Gọi với `candidates` (api `nightly_candidates`,
 * tăng dần theo semver): tìm lần ~03:00 đầu tiên có ứng viên đủ hạn; ứng viên đó là `latest` ⇒ như cũ, khác `latest` ⇒
 * "Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10)". Không có `candidates` (api cũ) hoặc
 * rỗng ⇒ cách cũ theo `published_at`. Gọi 2 tham số `(publishedAt, now)` vẫn là cách cũ.
 *
 * `blocked` (api `blocked_version` — bản đã lỗi lần trước): genh vẫn CHỌN bản cao nhất đủ hạn rồi BỎ QUA nếu đó là bản bị
 * chặn ⇒ đêm đó không cài gì. Đêm nào bản chọn được là bản bị chặn thì không hứa, xét đêm kế tiếp.
 */
export function autoInstallHint(
  ...args:
    | [publishedAt: string | null | undefined, now: number]
    | [
        candidates: readonly NightlyCandidate[] | null | undefined,
        latest: string | null | undefined,
        publishedAt: string | null | undefined,
        now: number,
        blocked?: string | null,
      ]
): string | null {
  if (args.length === 2) return legacyHint(args[0], args[1]);
  const [candidates, latest, publishedAt, now, blocked] = args;
  const usable = (Array.isArray(candidates) ? candidates : [])
    .map((c) => ({ tag: c?.tag, at: Date.parse(String(c?.eligible_at)) }))
    .filter((c): c is { tag: string; at: number } => typeof c.tag === 'string' && c.tag !== '' && Number.isFinite(c.at));
  if (usable.length === 0) return legacyHint(publishedAt, now);
  let run = nextNightlyRun(now);
  for (let i = 0; i < NIGHTLY_LOOKAHEAD; i += 1, run = nextNightlyRun(run.getTime() + 24 * 3600 * 1000)) {
    // Danh sách tăng dần theo semver ⇒ ứng viên đủ hạn CUỐI cùng là bản cao nhất lịch đêm sẽ chọn.
    const pick = [...usable].reverse().find((c) => c.at <= run.getTime());
    if (!pick || (blocked && pick.tag === blocked)) continue;
    const when = `đêm ${ddmm(run)} (~03:00)`;
    if (!latest || pick.tag === latest) return `Tự cài ${when}`;
    // Bản mới nhất đủ hạn lúc nào: theo ứng viên (api tính sẵn), không có thì theo mốc chính thức + 24 giờ.
    const own = usable.find((c) => c.tag === latest)?.at ?? (publishedAt ? Date.parse(publishedAt) + NIGHTLY_MIN_AGE_MS : NaN);
    const tail = Number.isFinite(own) ? ` — ${latest} tự cài sau khi đủ 24 giờ (đêm ${ddmm(nextNightlyRun(Math.max(own, run.getTime() + 1)))})` : '';
    return `Tự cài ${pick.tag} ${when}${tail}`;
  }
  return legacyHint(publishedAt, now);
}

function legacyHint(publishedAt: string | null | undefined, now: number): string | null {
  if (!publishedAt) return null;
  const t = Date.parse(publishedAt);
  if (!Number.isFinite(t)) return null;
  return `Tự cài đêm ${ddmm(nextNightlyRun(Math.max(t + NIGHTLY_MIN_AGE_MS, now)))} (~03:00)`;
}

/** Mã lỗi genh (GH-E9xx) cuối thông điệp hộp thư — genh ≥ v0.1.34 ghi "<việc> — <cách xử lý> (GH-E9xx)". */
export function updateErrorCode(message: string | null | undefined): string | null {
  const m = message?.match(/GH-E[0-9A-F]{3}/g);
  return m ? m[m.length - 1] : null;
}

/**
 * Lời dẫn thẻ "chưa thành công" theo mã lỗi genh — mỗi mã nghĩa khác nhau: chưa đụng gì (tải/sao lưu/ổ đĩa/cấu hình),
 * đã tự quay về bản cũ, bản mới đã chạy nhưng còn bước chép dữ liệu, hay quay về CŨNG thất bại (cần xử lý tay).
 * `rollbackFailed`: trường có cấu trúc từ api (run/update-blocked.json) — ưu tiên; dò chữ chỉ để đỡ genh cũ/nhánh
 * không ghi update-blocked. `canRequest=false` (máy chủ chưa nhận yêu cầu từ nút bấm): không có nút Thử lại — hướng
 * dẫn chạy lệnh bên dưới (thẻ hiện lệnh).
 */
function failedCopy(
  message: string | null,
  opts: { canRequest: boolean; rollbackFailed: boolean },
): { tone: 'warn' | 'bad'; kicker: string; body: string } {
  const code = updateErrorCode(message);
  const msg = message ?? '';
  const retry = opts.canRequest ? 'bấm Thử lại' : 'chạy lệnh bên dưới trên máy chủ';
  const Retry = retry.charAt(0).toUpperCase() + retry.slice(1);
  if (opts.rollbackFailed || /CŨNG THẤT BẠI|chưa trọn|can thiệp tay|xử lý tay/i.test(msg)) {
    return {
      tone: 'bad', kicker: 'Cần xử lý tay — tự quay về bản cũ chưa trọn',
      body: 'Hệ thống chưa tự đưa máy về trạng thái chạy ổn. Cần người quản trị máy chủ làm theo hướng dẫn trong Chi tiết kỹ thuật.',
    };
  }
  if (code === 'GH-E94C') {
    // v0.1.53 (F-97): genh không xoá được tệp yêu cầu trong run/request nên KHÔNG làm yêu cầu (làm tiếp sẽ khiến
    // trình nhận yêu cầu kích lặp) — chưa đụng gì. Thông điệp nguyên văn của genh nằm ở "Chi tiết kỹ thuật".
    return {
      tone: 'warn', kicker: 'Máy chủ không xoá được tệp yêu cầu — chưa đụng gì',
      body: `Bản đang dùng vẫn chạy bình thường. Nhờ người quản trị máy chủ kiểm quyền thư mục run/request trong thư mục cài đặt (genh phải xoá được tệp trong đó), rồi ${retry}.`,
    };
  }
  if (code === 'GH-E948') {
    return {
      tone: 'warn', kicker: 'Ổ đĩa máy chủ sắp đầy — chưa đụng gì, bản đang dùng vẫn chạy bình thường',
      body: `Cần giải phóng ổ đĩa trên máy chủ (ảnh Docker cũ, tệp lớn), rồi ${retry} — lịch đêm cũng sẽ tự thử lại.`,
    };
  }
  if (code === 'GH-E941' || code === 'GH-E940') {
    return {
      tone: 'warn', kicker: 'Chưa đụng gì — bản đang dùng vẫn chạy bình thường',
      body: code === 'GH-E941'
        ? `Chưa tải được bản mới (thường do mạng). Lịch đêm sẽ tự thử lại, hoặc ${retry}.`
        : `Chưa sao lưu được trước khi cập nhật nên hệ thống dừng lại. ${Retry}; nếu vẫn lỗi, chạy genh doctor trên máy chủ.`,
    };
  }
  if (code === 'GH-E900' || code === 'GH-E901') {
    return {
      tone: 'warn', kicker: 'Chưa đụng gì — bản đang dùng vẫn chạy bình thường',
      body: 'genh trên máy chủ chưa đọc được cấu hình cài đặt nên dừng lại trước khi làm gì. Chạy genh doctor trên máy chủ và xem Chi tiết kỹ thuật.',
    };
  }
  if (code === 'GH-E946' && /Cập nhật xong/i.test(msg)) {
    return {
      tone: 'warn', kicker: 'Bản mới đã chạy — còn bước chép dữ liệu cũ chưa xong',
      body: 'Dịch vụ đã lên bản mới, nhưng chép dữ liệu tệp cũ sang chỗ lưu mới chưa xong. Dữ liệu gốc vẫn còn trên máy chủ — làm theo Chi tiết kỹ thuật để chép nốt.',
    };
  }
  const rolledBack = /đã tự quay về/i.test(msg);
  if (code === 'GH-E94B') {
    // v0.1.37: genh nhận tín hiệu dừng (SIGINT/SIGTERM — máy tắt, bị kill) giữa chừng — không phải bản mới hỏng. Lịch
    // đêm CHỈ tự thử lại khi đã dừng gọn (chưa đụng gì / đã tự quay về); quay về chưa trọn thì genh chặn lịch đêm
    // (update-blocked.json) — không hứa tự thử lại (nhánh đó thường đã rơi vào "Cần xử lý tay" ở trên).
    if (/cần chạy tiếp/i.test(msg)) {
      // Máy tắt SAU khi CSDL đã sang bản mới: genh giữ bản mới (compose.yaml, CSDL) — chạy lại để đi tiếp, KHÔNG khôi
      // phục bản sao lưu cũ (mất mọi dữ liệu ghi từ lúc bật lại máy).
      return {
        tone: 'warn', kicker: 'Cập nhật bị dừng giữa chừng — dữ liệu đã chuyển sang bản mới, cần chạy lại để hoàn tất',
        body: `Đây không phải lỗi của bản mới — ${retry} để đi tiếp (hệ thống tự sao lưu lại trước khi làm). Không khôi phục bản sao lưu cũ: sẽ mất dữ liệu ghi sau lúc đó.`,
      };
    }
    if (rolledBack) {
      return {
        tone: 'warn', kicker: 'Cập nhật bị dừng giữa chừng',
        body: `Hệ thống đã tự quay về bản đang dùng, dữ liệu giữ nguyên. Đây không phải lỗi của bản mới — ${retry} để chạy lại từ đầu, hoặc đợi lịch đêm tự thử lại.`,
      };
    }
    if (/chưa đụng gì/i.test(msg)) {
      return {
        tone: 'warn', kicker: 'Cập nhật bị dừng giữa chừng — chưa đụng gì, bản đang dùng vẫn chạy bình thường',
        body: `Đây không phải lỗi của bản mới — ${retry} để chạy lại, hoặc đợi lịch đêm tự thử lại.`,
      };
    }
    return {
      tone: 'warn', kicker: 'Cập nhật bị dừng giữa chừng',
      body: `Đây không phải lỗi của bản mới. Xem Chi tiết kỹ thuật (hoặc logs/auto-update.log trên máy chủ) để biết máy đang ở bản nào, rồi ${retry}.`,
    };
  }
  if (code === 'GH-E94A') {
    // Phòng hờ: genh ≥ v0.1.37 KHÔNG ghi GH-E94A vào run/update-status.json (khoá được lấy trước hostlink.Start, chỉ in ra
    // stderr khi gõ tay — docs/handoff/05-installer.md). Giữ nhánh này cho genh khác/cũ hơn; chỉ có unit test.
    return {
      tone: 'warn', kicker: 'Đang có một lần cập nhật/khôi phục khác chạy — chờ xong rồi thử lại',
      body: `Chưa đụng gì — bản đang dùng vẫn chạy bình thường. Đợi lần đang chạy xong (vài phút), rồi ${retry}.`,
    };
  }
  if ((code === 'GH-E945' || code === 'GH-E949') && rolledBack) {
    return {
      tone: 'bad', kicker: 'Hệ thống đã tự quay về bản đang dùng — dữ liệu giữ nguyên',
      body: `Bản mới lỗi khi khởi động nên lịch đêm sẽ không tự cài lại bản này. ${Retry} nếu muốn thử ngay, hoặc đợi bản mới hơn.`,
    };
  }
  if ((code === 'GH-E946' || code === 'GH-E947') && rolledBack) {
    return {
      tone: 'bad', kicker: 'Hệ thống đã tự quay về bản đang dùng — dữ liệu giữ nguyên',
      body: `${Retry}, hoặc xem logs/auto-update.log trên máy chủ.`,
    };
  }
  if (code === null) {
    // genh cũ (≤ v0.1.33, không có mã) luôn tự quay về khi lỗi — giữ lời dẫn như trước; thân thẻ là thông điệp genh.
    return { tone: 'bad', kicker: 'Hệ thống đã tự quay về bản đang dùng — dữ liệu giữ nguyên', body: msg };
  }
  return {
    tone: 'warn', kicker: 'Cập nhật chưa xong — xem Chi tiết kỹ thuật',
    body: `Xem Chi tiết kỹ thuật (hoặc logs/auto-update.log trên máy chủ) để biết máy đang ở bản nào, rồi ${retry}.`,
  };
}

function recent(iso: string | null, now: number): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return Number.isFinite(t) && now - t < RECENT_MS;
}

/**
 * Trạng thái thẻ cập nhật từ `GET /system/update` + việc Owner vừa bấm (waitingFor) + api đang tắt để khởi động lại
 * (offline). Hàm thuần để test đủ các nhánh.
 */
export function updateView(
  d: SystemUpdate | undefined,
  opts: { waitingFor: string | null; offline: boolean; now?: number },
): UpdateView {
  const now = opts.now ?? Date.now();
  const target = d?.latest ?? opts.waitingFor ?? 'bản mới';
  const steps = (active: number) =>
    ['Nhận yêu cầu', 'Sao lưu dữ liệu, tải bản mới', 'Khởi động lại & kiểm tra'].map((label, i) => ({
      label,
      state: (i < active ? 'done' : i === active ? 'active' : 'todo') as StepState,
    }));

  if (opts.offline) {
    return { kind: 'working', tone: 'accent', title: `Đang cập nhật lên ${opts.waitingFor}`, kicker: 'Hệ thống đang khởi động lại — trang tự tải lại khi xong', steps: steps(2) };
  }
  if (!d) return { kind: 'hidden' };
  const block = blockOf(d);
  if (d.state === 'requested') {
    // v0.1.37: máy chủ đang chạy một lần cập nhật/khôi phục khác (vd lịch đêm) — yêu cầu xếp hàng, làm ngay sau đó.
    const kicker = d.host_busy
      ? 'Máy chủ đang chạy một lần cập nhật/khôi phục khác — sẽ làm yêu cầu này ngay khi lần đó xong'
      : 'Đã gửi yêu cầu, máy chủ sẽ bắt đầu trong vòng 1 phút';
    return { kind: 'working', tone: 'accent', title: `Đang cập nhật lên ${target}`, kicker, steps: steps(0) };
  }
  if (d.state === 'running') {
    return { kind: 'working', tone: 'accent', title: `Đang cập nhật lên ${target}`, kicker: 'Mất khoảng 2–5 phút — trang tự tải lại khi xong', steps: steps(1) };
  }
  if (d.state === 'stalled' && d.stalled_reason === 'process_gone' && !d.update_available && !!d.current && d.current === d.latest) {
    // genh bị ngắt SAU khi dịch vụ đã lên bản mới (trước lúc ghi kết quả): máy đang chạy đúng bản mới nhất — không nói
    // "Cập nhật lên vX bị dừng" về chính bản đang chạy, không cần Thử lại.
    return {
      kind: 'finished', tone: 'ok', title: `Đang dùng bản mới nhất ${d.current}`,
      kicker: 'Lần cập nhật trước bị ngắt trước khi kịp báo xong — máy đang chạy bản mới nhất, không cần làm gì', steps: [],
    };
  }
  if (d.state === 'stalled' && d.stalled_reason === 'process_gone') {
    // v0.1.37 (F-34): 'running' mà tiến trình genh trên máy chủ không còn (máy tắt/khởi động lại, bị dừng).
    return {
      kind: 'stalled', tone: 'warn', title: `Cập nhật lên ${target} bị dừng giữa chừng`,
      kicker: 'Tiến trình cập nhật trên máy chủ không còn chạy — có thể máy vừa tắt hoặc khởi động lại',
      body: `${d.can_request ? 'Bấm Thử lại' : 'Chạy lệnh dưới đây trên máy chủ'} để chạy lại từ đầu (hệ thống tự sao lưu trước khi làm). Lỗi lặp lại thì xem logs/auto-update.log trên máy chủ.`,
      showCommand: !d.can_request, steps: [],
    };
  }
  if (d.state === 'stalled' && d.stalled_reason === 'linger_off') {
    // v0.1.53 (F-99): nguyên nhân phổ biến nhất — linger tắt nên tiến trình nền (trình nhận yêu cầu, lịch đêm) chỉ chạy
    // khi có người đăng nhập máy chủ. Lệnh cố định, Sếp chạy một lần.
    return {
      kind: 'stalled', tone: 'warn', title: 'Máy chủ chưa nhận yêu cầu cập nhật',
      kicker: 'Tiến trình nền trên máy chủ chỉ chạy khi có người đăng nhập — cần bật linger',
      body: 'Chạy một lần lệnh dưới đây trên máy chủ (máy hỏi mật khẩu đăng nhập máy), rồi bấm Thử lại.',
      command: LINGER_COMMAND, commandLabel: COMMAND_LABEL_SERVER, showCommand: true, steps: [],
    };
  }
  if (d.state === 'stalled' && d.stalled_reason === 'watcher_failed') {
    // v0.1.53 (F-99): trình nhận yêu cầu (gen-harness-update-request.path/.service) báo lỗi — `genh update` cài lại.
    return {
      kind: 'stalled', tone: 'warn', title: 'Máy chủ chưa nhận yêu cầu cập nhật',
      kicker: 'Trình nhận yêu cầu trên máy chủ đang lỗi',
      body: 'Chạy lệnh dưới đây trên máy chủ một lần để bật lại, rồi bấm Thử lại.',
      command: UPDATE_COMMAND, commandLabel: COMMAND_LABEL_SERVER, showCommand: true, steps: [],
    };
  }
  if (d.state === 'stalled') {
    return {
      kind: 'stalled', tone: 'warn', title: 'Máy chủ chưa nhận yêu cầu cập nhật',
      kicker: 'Đã quá 15 phút mà chưa bắt đầu — có thể máy chủ đang tắt tiến trình nhận yêu cầu',
      body: 'Bấm Thử lại, hoặc chạy lệnh dưới đây trên máy chủ.', showCommand: true, steps: [],
    };
  }
  if (d.state === 'failed' && recent(d.finished_at, now)) {
    // Tiêu đề theo bản ĐÃ THỬ (`to` genh ghi), không theo bản mới nhất — v0.1.35 ra rồi thì lỗi đêm qua vẫn là của v0.1.34.
    const tried = d.to && d.to !== d.current ? d.to : target;
    const rollbackFailed = d.blocked_rollback_failed === true && !!d.blocked_version && d.blocked_version === tried;
    const copy = failedCopy(d.message, { canRequest: d.can_request, rollbackFailed });
    const coded = updateErrorCode(d.message) !== null;
    return {
      kind: 'failed', tone: copy.tone, title: `Cập nhật lên ${tried} chưa thành công`, kicker: copy.kicker,
      // genh cũ (không có mã): thông điệp đã là câu thân thiện — hiện thẳng như trước.
      body: coded ? copy.body : (d.message ?? 'Xem chi tiết trong logs/auto-update.log trên máy chủ.'),
      detail: coded ? (d.message ?? undefined) : undefined,
      // Không có nút Thử lại (máy chủ chưa nhận yêu cầu từ nút bấm) → hiện lệnh chạy tay như thẻ "Có bản mới".
      showCommand: !d.can_request,
      steps: [],
    };
  }
  if (opts.waitingFor && !d.update_available && (d.state === 'done' || d.state === 'idle')) {
    return { kind: 'finished', tone: 'ok', title: `Đã cập nhật lên ${d.current ?? target}`, kicker: 'Đang tải lại trang để dùng bản mới…', steps: [] };
  }
  if (d.update_available) {
    // Lịch đêm đợi bản ra đủ 24 giờ: nói rõ khi nào tự cài, kẻo Owner thấy "Có bản mới" tới 2 đêm mà không hiểu.
    // Chỉ nói khi genh báo lịch đêm đang BẬT (genh.json auto_update_enabled === true) — tắt/không rõ thì không hứa.
    // Bản mới nhất đã lỗi lần trước (genh ghi run/update-blocked.json): lịch đêm KHÔNG tự cài lại — không hứa "Tự cài".
    const blocked = !!d.blocked_version && d.blocked_version === d.latest;
    const hint =
      d.auto_update_enabled === true && !blocked ? autoInstallHint(d.nightly_candidates, d.latest, d.published_at, now, d.blocked_version) : null;
    if (block) {
      // v0.1.55: nút bị ẩn ⇒ nói rõ lý do + việc cần làm (+ lệnh), không để Owner đoán vì sao "mất nút".
      return { kind: 'available', tone: 'accent', title: `Có bản mới ${d.latest}`, kicker: `Đang dùng ${d.current} · chưa bấm Cập nhật ngay được`, block, steps: [] };
    }
    const action = d.can_request ? 'bấm Cập nhật ngay' : 'chạy lệnh bên dưới';
    return {
      kind: 'available', tone: 'accent', title: `Có bản mới ${d.latest}`,
      kicker: blocked
        ? `Đang dùng ${d.current} · bản này đã lỗi ở lần cập nhật trước nên lịch đêm không tự cài lại — ${action} để thử lại`
        : hint
          ? `Đang dùng ${d.current} · ${hint} — hoặc ${action}`
          : `Đang dùng ${d.current} · ${action} (mất khoảng 2–5 phút, tự sao lưu trước)`,
      body: d.can_request ? undefined : 'Máy chủ chưa bật cập nhật bằng nút bấm.',
      showCommand: !d.can_request, steps: [],
    };
  }
  return { kind: 'hidden', ...(block ? { block } : {}) };
}

/**
 * Nút "Cập nhật ngay" có hiện không: api mới ⇒ đúng khi `request_block_reason` là null; api cũ (thiếu trường) ⇒ như trước,
 * theo `can_request`.
 */
export function canClickUpdate(d: Pick<SystemUpdate, 'request_block_reason' | 'can_request'>): boolean {
  return d.request_block_reason === undefined ? d.can_request : d.request_block_reason === null;
}

/** v0.1.43 (F-62): tiền tố conventional commit đầu dòng/gạch đầu dòng; nhóm 2 = chữ đầu phần còn lại. */
const COMMIT_PREFIX = /^(\s*[-*]\s+)?(?:feat|fix|chore|docs|refactor|perf|test|ci|build|style|revert)(?:\([^)]*\))?!?:\s*(\S)/u;

/**
 * v0.1.28 (UX V11): ghi chú phát hành lấy từ GitHub có phần tự sinh bằng tiếng Anh ("What's Changed", "Full
 * Changelog", "by @x in https://…/pull/12") — bỏ phần đó, giữ phần mô tả; dịch tiêu đề thường gặp.
 */
export function readableNotes(md: string | null | undefined): string {
  if (!md) return '';
  const out: string[] = [];
  for (const raw of md.replace(/\r\n/g, '\n').split('\n')) {
    if (/full changelog/i.test(raw) || /^#+\s*new contributors/i.test(raw) || /made their first contribution/i.test(raw)) continue;
    // Chú thích HTML (vd dấu `<!-- genh:promoted_at=… -->` của job promote) — GitHub không hiện, Console cũng không.
    if (/^\s*<!--.*-->\s*$/.test(raw)) continue;
    const l = raw
      .replace(/^(#+)\s*what'?s changed\s*$/i, '$1 Điểm mới')
      .replace(/\s+by @[\w-]+(\[bot\])?\s+in\s+https?:\/\/\S+/gi, '')
      .replace(/\s*\(#\d+\)\s*$/, '')
      .replace(/https?:\/\/github\.com\/\S+/gi, '')
      // v0.1.43 (F-62): bỏ tiền tố conventional commit ("feat(x): …") ở đầu dòng/gạch đầu dòng, viết hoa chữ đầu.
      .replace(COMMIT_PREFIX, (_m, bullet: string | undefined, first: string) => (bullet ?? '') + first.toLocaleUpperCase('vi'))
      .trimEnd();
    out.push(l);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

/** Khoá sessionStorage: bản vừa cập nhật lên — sau khi trang tự tải lại thì báo "Đã cập nhật lên vX" một lần. */
export const UPDATED_FLAG = 'gh_updated_to';
