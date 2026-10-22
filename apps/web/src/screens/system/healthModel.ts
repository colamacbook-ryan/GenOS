/**
 * v0.1.36 (F-6): đổi `GET /system/health` (`SystemHealth`) thành các dòng chữ đã định dạng cho thẻ "Sức khoẻ hệ thống"
 * (Điều khiển hệ thống › Dữ liệu & lưu trữ) và sắp sự cố cho dải "Cần Sếp xử lý" (đầu Tổng quan). Hàm thuần — test được,
 * mọi giá trị trả ra là chuỗi (không bao giờ đưa object vào JSX).
 */
import type { HealthIssue, SystemHealth } from '@gen-harness/contracts';
import { DEFAULT_TZ, fmtAgo, fmtDM, fmtDMClock, fmtDec, fmtHM, fmtInt } from '../../lib/format';
import { offsiteNextStep, type OffsiteViewer } from './offsiteModel';

export type HealthTone = 'ok' | 'warn' | 'bad' | 'muted';

export interface HealthRow {
  key: 'worker' | 'browser' | 'bridge' | 'agy_box' | 'dlq' | 'backup' | 'offsite' | 'update' | 'disk' | 'autostart' | 'nightly' | 'watcher';
  label: string;
  value: string;
  tone: HealthTone;
  /** Câu ngắn dưới dòng: Sếp nên làm gì (hoặc không cần làm gì) khi dòng vàng/đỏ mà không có hướng dẫn riêng. */
  hint?: string;
}

export interface HealthTechRow {
  name: string;
  value: string;
  tone: HealthTone;
}

const GB = 1024 ** 3;

/** 12_884_901_888 → "12,0 GB". */
export function fmtGb(bytes: number): string {
  return `${fmtDec(bytes / GB, 1)} GB`;
}

/** Hàng lỗi tin khách vào: tin đã thử nhập kho 5 lần mà vẫn lỗi — hàng DUY NHẤT phát lại được (khớp `DLQ_REPLAYABLE` ở API). */
export const INBOUND_STREAM = 'gh.bridge.inbound';

/** Tên hàng lỗi theo loại (chữ cho Sếp); hàng chưa biết ⇒ "Việc nền khác" (tên kỹ thuật chỉ ở "Chi tiết kỹ thuật"). */
const QUEUE_LABELS: Record<string, string> = {
  [INBOUND_STREAM]: 'Tin khách chưa nhập kho được',
  'gh.bridge.status': 'Trạng thái kênh chưa ghi nhận được',
  'gh.bridge.directory': 'Danh bạ nhóm chưa cập nhật được',
  'gh.clean.ready': 'Tin chờ sàng lọc bị lỗi',
  'gh.action.requested': 'Đề xuất của Gen bị lỗi',
  'gh.action.decided': 'Quyết định duyệt bị lỗi',
};

export interface HealthQueue {
  /** Tên stream gốc (không `.dlq`) — đúng tham số của `POST /system/queues/{stream}/replay|clear`. */
  stream: string;
  label: string;
  count: number;
  /** Có nút "Phát lại" (chỉ tin khách vào); mọi hàng đều có "Đã xem, xoá". */
  replayable: boolean;
}

/** Các hàng lỗi đang có việc (> 0), tin khách vào trước. */
export function healthQueues(h: SystemHealth): HealthQueue[] {
  return h.queues
    .filter((q) => Number.isFinite(q.dlq) && q.dlq > 0)
    .map((q) => ({ stream: q.stream, label: QUEUE_LABELS[q.stream] ?? 'Việc nền khác', count: q.dlq, replayable: q.stream === INBOUND_STREAM }))
    .sort((a, b) => Number(b.replayable) - Number(a.replayable) || a.stream.localeCompare(b.stream));
}

function dlqHint(hasInbound: boolean, hasOthers: boolean): string {
  const report = 'Vẫn lỗi thì gửi kèm khi báo lỗi (Trợ giúp › Báo lỗi).';
  if (hasInbound) {
    return `Tin khách chưa mất — đang nằm chờ. Bấm "Phát lại" để nhập kho lại. ${report}${hasOthers ? ' Việc khác đã thử lại nhiều lần mà vẫn lỗi: xem rồi bấm "Đã xem, xoá" để dọn.' : ''}`;
  }
  return `Việc này đã thử lại nhiều lần mà vẫn lỗi nên sẽ không tự hết. Xem rồi bấm "Đã xem, xoá" để dọn; ${report.replace(/^V/, 'v')}`;
}

/** Các dòng chính của thẻ "Sức khoẻ hệ thống" (theo đúng thứ tự hiển thị). */
export function healthRows(
  h: SystemHealth,
  now = Date.now(),
  tz = DEFAULT_TZ,
  who: OffsiteViewer = { isOwner: true, canManage: true },
): HealthRow[] {
  const rows: HealthRow[] = [];

  const w = h.worker;
  rows.push(
    w.state === 'ok'
      ? { key: 'worker', label: 'Bộ xử lý nền', value: w.last_seen_at ? `Đang chạy · lần cuối ${fmtAgo(w.last_seen_at, now, tz)}` : 'Đang chạy', tone: 'ok' }
      : w.state === 'silent'
        ? { key: 'worker', label: 'Bộ xử lý nền', value: w.silent_minutes != null ? `Đã ngừng ${fmtInt(w.silent_minutes)} phút` : 'Đã ngừng — chưa thấy chạy lại', tone: 'bad' }
        : { key: 'worker', label: 'Bộ xử lý nền', value: 'Chưa có tín hiệu', tone: 'warn' },
  );

  const b = h.browser;
  rows.push(
    b.state === 'ok'
      ? { key: 'browser', label: 'Trình duyệt nền', value: b.last_heartbeat_at ? `Đang chạy · lần cuối ${fmtAgo(b.last_heartbeat_at, now, tz)}` : 'Đang chạy', tone: 'ok' }
      : b.state === 'silent'
        ? { key: 'browser', label: 'Trình duyệt nền', value: b.last_heartbeat_at ? `Ngừng từ ${fmtAgo(b.last_heartbeat_at, now, tz)}` : 'Đã ngừng', tone: 'warn' }
        : { key: 'browser', label: 'Trình duyệt nền', value: 'Chưa bật', tone: 'muted' },
  );

  // F051: bộ nhận tin Zalo/WhatsApp (cầu nối) — khối chỉ có khi có nhịp hoặc có kênh đang "kết nối" ⇒ vắng khối thì không dòng.
  const br = h.bridge;
  if (br) {
    rows.push(
      br.state === 'silent'
        ? {
            key: 'bridge',
            label: 'Bộ nhận tin Zalo/WhatsApp',
            value: br.silent_minutes != null ? `Đã ngừng ${fmtInt(br.silent_minutes)} phút — tin khách chưa vào được` : 'Đã ngừng — tin khách chưa vào được',
            tone: 'bad',
          }
        : { key: 'bridge', label: 'Bộ nhận tin Zalo/WhatsApp', value: br.last_seen_at ? `Đang chạy · lần cuối ${fmtAgo(br.last_seen_at, now, tz)}` : 'Đang chạy', tone: 'ok' },
    );
  }

  // v0.1.63 (H1): hộp riêng Antigravity — khối chỉ có khi hộp đã được cấu hình ⇒ vắng khối thì không dòng.
  const ab = h.agy_box;
  if (ab) {
    rows.push(
      ab.state === 'ok'
        ? { key: 'agy_box', label: 'Hộp riêng Antigravity', value: ab.agy_version ? `Đang chạy · Antigravity ${ab.agy_version}` : 'Đang chạy', tone: 'ok' }
        : {
            key: 'agy_box',
            label: 'Hộp riêng Antigravity',
            value: 'Chưa chạy — việc có người ngoài tạm dùng nguồn AI khác',
            tone: 'warn',
            hint: 'Thường tự hết sau khi hộp khởi động lại. Kéo dài thì xem hướng dẫn bên dưới.',
          },
    );
  }

  // Thuật ngữ "DLQ" chỉ ở "Chi tiết kỹ thuật" (healthTechRows). Hàng lỗi KHÔNG tự hết (việc đã thử 5 lần mà vẫn lỗi):
  // tin khách vào có nút "Phát lại", mọi hàng đều có "Đã xem, xoá" (HealthCard) — câu gợi ý nói đúng như vậy.
  const dq = healthQueues(h);
  const inbound = dq.find((q) => q.replayable);
  const others = dq.filter((q) => !q.replayable).reduce((sum, q) => sum + q.count, 0);
  const dlq = dq.reduce((sum, q) => sum + q.count, 0);
  rows.push(
    dlq > 0
      ? {
          key: 'dlq',
          label: 'Việc nền bị lỗi',
          value: inbound ? `${fmtInt(inbound.count)} tin khách chưa nhập kho${others > 0 ? ` · ${fmtInt(others)} việc khác` : ''}` : `${fmtInt(dlq)} việc`,
          tone: 'warn',
          hint: dlqHint(!!inbound, others > 0),
        }
      : { key: 'dlq', label: 'Việc nền bị lỗi', value: 'Không có', tone: 'ok' },
  );

  const bk = h.backup;
  rows.push(
    !bk.configured
      ? { key: 'backup', label: 'Sao lưu', value: 'Chưa cấu hình', tone: 'warn' }
      : bk.stale
        ? { key: 'backup', label: 'Sao lưu', value: `Quá ${bk.stale_after || '36 giờ'} chưa sao lưu`, tone: 'bad' }
        : bk.latest_at
          ? { key: 'backup', label: 'Sao lưu', value: `Bản mới nhất ${fmtAgo(bk.latest_at, now, tz)}`, tone: 'ok' }
          : { key: 'backup', label: 'Sao lưu', value: 'Chưa có bản nào', tone: 'warn' },
  );

  // v0.1.40 (F-12): bản sao ngoài máy (ổ USB/NAS) — khối chỉ có khi api có hộp thư với genh ⇒ vắng khối thì không có dòng.
  const off = h.offsite;
  if (off) {
    const days = typeof off.age_days === 'number' && Number.isFinite(off.age_days) ? Math.floor(off.age_days) : null;
    const failed = off.state === 'failed' || off.state === 'not_mounted' || (!!off.error_code && off.state !== 'ok' && off.state !== 'running');
    rows.push(
      !off.configured
        ? { key: 'offsite', label: 'Bản sao ngoài máy', value: 'Chưa có — chưa chọn nơi lưu', tone: 'warn' }
        : failed
          ? { key: 'offsite', label: 'Bản sao ngoài máy', value: off.state === 'not_mounted' ? 'Lần gần nhất lỗi — chưa thấy ổ USB/NAS' : 'Lần gần nhất lỗi', tone: days != null && days > 30 ? 'bad' : 'warn' }
          : !off.last_success_at
            ? { key: 'offsite', label: 'Bản sao ngoài máy', value: 'Chưa có bản nào', tone: 'warn' }
            : off.stale
              ? { key: 'offsite', label: 'Bản sao ngoài máy', value: `Cũ — ${days ?? 'nhiều'} ngày chưa sao lưu ra ổ ngoài`, tone: days != null && days > 30 ? 'bad' : 'warn' }
              : { key: 'offsite', label: 'Bản sao ngoài máy', value: `Bản mới nhất ${fmtAgo(off.last_success_at, now, tz)}`, tone: 'ok' },
    );
    const last = rows[rows.length - 1];
    // Gợi ý theo trạng thái + mã lỗi + vai trò: chưa chọn nơi lưu thì nút "Sao lưu ngay" đang khoá; "Chọn nơi lưu" chỉ
    // Owner có; GH-EB07/GH-EB04 thì cắm ổ rồi sao lưu lại không giúp được (chọn nơi khác / giải phóng chỗ).
    if (last.tone !== 'ok') last.hint = `Xem thẻ "Bản sao ngoài máy" bên dưới — ${offsiteNextStep(!!off.configured, who, failed ? off.error_code : null).replace(/^./, (c) => c.toLowerCase())}`;
  }

  const u = h.update;
  rows.push(
    // `failed` = lỗi trong 24 giờ qua (cùng điều kiện thẻ cập nhật) — quá hạn thì API trả false dù state vẫn 'failed'.
    // v0.1.37: dừng do tín hiệu (máy tắt/khởi động lại — GH-E94B) mà không dở dang ⇒ vàng, cùng lời với thẻ cập nhật.
    u.failed && u.interrupted
      ? { key: 'update', label: 'Cập nhật', value: 'Cập nhật bị dừng giữa chừng', tone: 'warn' }
    : u.failed
      ? { key: 'update', label: 'Cập nhật', value: u.blocked_version ? `Lần cập nhật gần nhất lỗi (${u.blocked_version})` : 'Lần cập nhật gần nhất lỗi', tone: 'bad' }
      : u.state === 'stalled' && u.stalled_reason === 'process_gone'
        ? { key: 'update', label: 'Cập nhật', value: 'Cập nhật bị dừng giữa chừng', tone: 'warn' }
        : u.state === 'stalled'
          ? { key: 'update', label: 'Cập nhật', value: 'Máy chủ chưa nhận yêu cầu cập nhật', tone: 'warn' }
          : u.state === 'requested' || u.state === 'running'
            ? { key: 'update', label: 'Cập nhật', value: 'Đang cập nhật', tone: 'muted' }
            : u.state === 'unknown'
              ? { key: 'update', label: 'Cập nhật', value: 'Chưa rõ', tone: 'muted' }
              : { key: 'update', label: 'Cập nhật', value: 'Bình thường', tone: 'ok' },
  );

  // Ổ đĩa chỉ được đo khi genh chạy `genh update` (disk-status.json) — số có thể cũ cả ngày ⇒ luôn ghi giờ đo.
  const d = h.disk;
  const measured = d.checked_at ? ` · đo lúc ${fmtDM(d.checked_at, tz)} ${fmtHM(d.checked_at, tz)}` : '';
  rows.push(
    d.state === 'low'
      ? { key: 'disk', label: 'Ổ đĩa', value: `${d.free_bytes != null ? `Sắp hết chỗ — còn ${fmtGb(d.free_bytes)}` : 'Sắp hết chỗ'}${measured}`, tone: 'bad' }
      : d.state === 'ok' && d.free_bytes != null
        ? { key: 'disk', label: 'Ổ đĩa', value: `Còn ${fmtGb(d.free_bytes)} trống${measured}`, tone: 'ok' }
        : { key: 'disk', label: 'Ổ đĩa', value: 'Chưa đo', tone: 'muted' },
  );

  // v0.1.37 (F-73): máy chủ có tự chạy lại Gen-Harness khi bật máy không — genh chỉ kiểm khi chạy genh status/doctor
  // hoặc lần cập nhật kế tiếp ⇒ luôn ghi giờ kiểm. api cũ/không có hộp thư với genh ⇒ không có khối ⇒ không có dòng.
  const a = h.autostart;
  if (a) {
    const checked = a.checked_at ? ` · kiểm lúc ${fmtDM(a.checked_at, tz)} ${fmtHM(a.checked_at, tz)}` : '';
    rows.push(
      a.state === 'warn'
        ? { key: 'autostart', label: 'Tự chạy lại khi bật máy', value: `Chưa bật${checked}`, tone: 'warn' }
        : a.state === 'ok'
          ? { key: 'autostart', label: 'Tự chạy lại khi bật máy', value: `Có${checked}`, tone: 'ok' }
          : { key: 'autostart', label: 'Tự chạy lại khi bật máy', value: 'Chưa rõ', tone: 'muted' },
    );
  }

  // v0.1.53 (F-99): lịch tự cập nhật đêm (~03:00) — khối chỉ có khi api có hộp thư với genh ⇒ vắng khối thì không có
  // dòng. 'warn' = đang tắt / im quá 36 giờ / Sếp đã tắt mà lịch vẫn bật (hướng dẫn ở `healthTips`); 'off' = Sếp đã chủ
  // động tắt; 'other' = lịch của máy do bản cài khác quản lý (không cảnh báo, không có lệnh bật — lệnh đó bị từ chối).
  const n = h.nightly;
  if (n) {
    const last = n.last_run_at ? `${fmtDM(n.last_run_at, tz)} ${fmtHM(n.last_run_at, tz)}` : null;
    rows.push(
      n.state === 'warn'
        ? { key: 'nightly', label: 'Tự cập nhật đêm', value: nightlyWarnValue(n), tone: 'warn' }
        : n.state === 'ok'
          ? { key: 'nightly', label: 'Tự cập nhật đêm', value: last ? `Bình thường · chạy lần cuối ${last}` : 'Bình thường · chưa tới giờ chạy lần đầu', tone: 'ok' }
          : n.state === 'off'
            ? { key: 'nightly', label: 'Tự cập nhật đêm', value: 'Tắt (Sếp đã tắt)', tone: 'muted' }
            : n.state === 'other'
              ? { key: 'nightly', label: 'Tự cập nhật đêm', value: 'Lịch đêm do bản cài khác trên máy này quản lý', tone: 'muted' }
              : { key: 'nightly', label: 'Tự cập nhật đêm', value: 'Chưa rõ', tone: 'muted' },
    );
    // v0.1.54: người gác yêu cầu (.path) lỗi — chỉ có dòng này khi api báo `watcher` (không ok).
    const w = n.watcher;
    if (w) {
      rows.push({ key: 'watcher', label: 'Người gác cập nhật', value: watcherRowValue(w), tone: w.state === 'failed' ? 'bad' : 'warn' });
    }
  }
  return rows;
}

/** Lệnh sửa gốc khi người gác yêu cầu hết hạn mức inotify (cùng chuỗi genh/api — không lấy chữ nào từ tệp trên máy chủ). */
export const INOTIFY_FIX_COMMAND = 'sudo sysctl -w fs.inotify.max_user_instances=1024';

type HealthWatcher = NonNullable<NonNullable<SystemHealth['nightly']>['watcher']>;

function watcherCause(w: HealthWatcher): string {
  if (w.reason === 'inotify') return 'hết hạn mức inotify';
  if (w.reason === 'resources') return 'lỗi tài nguyên — thường là hạn mức inotify';
  return 'người gác bị lỗi';
}

/** Chữ dòng "Người gác cập nhật": đang chạy dự phòng (quét mỗi phút) hoặc lỗi chưa có dự phòng. */
function watcherRowValue(w: HealthWatcher): string {
  return w.state === 'failed'
    ? `Đang lỗi — nút Cập nhật ngay chưa có người nhận (${watcherCause(w)})`
    : `Đang chạy dự phòng (${watcherCause(w)})`;
}

/**
 * Chữ dòng "Tự cập nhật đêm" khi 'warn' — cùng cách nói với tiêu đề sự cố host.nightly (gh/health._eval_nightly): theo
 * `reason` của api ('disabled' ⇒ "Đang tắt"; 'stale' ⇒ "Hơn 1 ngày chưa chạy" khi < 2 ngày, còn lại "Chưa chạy N ngày";
 * 'opted_out_running' ⇒ Sếp đã tắt mà lịch vẫn bật). api cũ không có `reason` ⇒ suy từ số ngày như trước.
 */
function nightlyWarnValue(n: NonNullable<SystemHealth['nightly']>): string {
  if (n.reason === 'opted_out_running') return 'Sếp đã tắt nhưng lịch vẫn bật — máy vẫn tự lên bản mới';
  if (n.reason === 'disabled') return 'Đang tắt';
  const days = typeof n.days_since === 'number' && Number.isFinite(n.days_since) ? Math.floor(n.days_since) : null;
  if (days == null || days < 1) return n.reason === 'stale' ? 'Hơn 1 ngày chưa chạy' : 'Đang tắt';
  return days < 2 ? 'Hơn 1 ngày chưa chạy' : `Chưa chạy ${fmtInt(days)} ngày`;
}

export interface HealthTipStep {
  text: string;
  /** Lệnh chạy trên máy chủ (cửa sổ dòng lệnh) — hiện dạng mã, có thể chép. */
  cmd?: string;
}
export interface HealthTip {
  key: 'disk' | 'worker' | 'bridge' | 'agy_box' | 'autostart' | 'nightly' | 'watcher';
  title: string;
  steps: HealthTipStep[];
  /** Cảnh báo rủi ro (Sếp tự quyết, nhưng phải thấy rõ). */
  warning?: string;
}

/**
 * Hướng dẫn tự xử lý ngay trong thẻ — đích của nút "Xem cách giải phóng" (disk.low), "Xem sức khoẻ" (worker.silent),
 * "Xem cách bật" (host.autostart, cả chuông) và "Xem cách bật lại" (host.nightly, v0.1.53) ở dải "Cần Sếp xử lý": nút
 * hứa gì thì trang đích phải có đúng cái đó.
 */
export function healthTips(h: SystemHealth): HealthTip[] {
  const tips: HealthTip[] = [];
  if (h.disk.state === 'low') {
    const need = h.disk.min_bytes != null ? fmtGb(h.disk.min_bytes) : null;
    tips.push({
      key: 'disk',
      title: 'Cách giải phóng chỗ trống',
      steps: [
        { text: 'Trên máy chủ, xem dịch vụ và dung lượng đang dùng:', cmd: 'genh status' },
        { text: 'Xoá container đã dừng, ảnh Docker không gắn tên và bộ nhớ đệm build không còn dùng (không đụng dữ liệu Gen-Harness):', cmd: 'docker system prune' },
        { text: 'Chép bản sao lưu cũ, video, tệp tải về… sang ổ khác rồi xoá khỏi máy chủ.' },
        // Ổ đĩa chỉ được đo lại khi genh cập nhật (apps/genh/internal/ops/update.go ensureDiskSpace), không phải mỗi phút.
        {
          text: `${need ? `Còn trống từ ${need} trở lên thì` : 'Đủ chỗ trống thì'} chạy lệnh dưới để đo lại ổ đĩa và cập nhật luôn (hoặc chờ lần cập nhật tự động đêm nay) — cảnh báo sẽ tự hết:`,
          cmd: 'genh update',
        },
      ],
      warning: 'Không xoá thư mục cài Gen-Harness hay volume Docker (không dùng "docker volume prune" hoặc cờ --volumes) — đó là dữ liệu của Sếp.',
    });
  }
  if (h.worker.state === 'silent') {
    tips.push({
      key: 'worker',
      title: 'Cách khởi động lại Bộ xử lý nền',
      steps: [
        { text: 'Trên máy chủ, dừng rồi bật lại toàn bộ dịch vụ (không mất dữ liệu, Console tạm gián đoạn khoảng 1 phút):', cmd: 'genh stop' },
        { text: 'Rồi:', cmd: 'genh start' },
        { text: 'Vẫn ngừng thì xem lỗi của Bộ xử lý nền và gửi kèm khi báo lỗi (Trợ giúp › Báo lỗi):', cmd: 'genh logs worker' },
      ],
    });
  }
  if (h.bridge?.state === 'silent') {
    // F051: đích của nút "Xem sức khoẻ" (bridge.silent) — dịch vụ bridge chạy riêng, khởi động lại không mất dữ liệu.
    tips.push({
      key: 'bridge',
      title: 'Cách khởi động lại Bộ nhận tin Zalo/WhatsApp',
      steps: [
        { text: 'Trên máy chủ, xem lỗi của bộ nhận tin (dịch vụ bridge) và gửi kèm khi báo lỗi nếu cần (Trợ giúp › Báo lỗi):', cmd: 'genh logs bridge' },
        { text: 'Dừng rồi bật lại toàn bộ dịch vụ (không mất dữ liệu, Console tạm gián đoạn khoảng 1 phút):', cmd: 'genh stop' },
        { text: 'Rồi:', cmd: 'genh start' },
        { text: 'Tin khách gửi trong lúc bộ nhận tin dừng có thể không về — hỏi lại khách nếu cần. Chạy lại xong, thẻ kênh và dòng này tự về bình thường.' },
      ],
    });
  }
  if (h.agy_box?.state === 'down') {
    // v0.1.63 (H1): đích của nút "Xem sức khoẻ" (agy_box.down) — hộp chạy riêng, dựng lại không mất dữ liệu.
    tips.push({
      key: 'agy_box',
      title: 'Cách bật lại Hộp riêng Antigravity',
      steps: [
        { text: 'Trên máy chủ, xem lỗi của hộp (dịch vụ agy-box) và gửi kèm khi báo lỗi nếu cần (Trợ giúp › Báo lỗi):', cmd: 'genh logs agy-box' },
        // Review v0.1.63 (P3): `genh start` dựng lại container đã dừng; `genh update` là tải bản mới (đã mới nhất thì không làm gì).
        { text: 'Dựng lại hộp (không mất dữ liệu, giữ nguyên đăng nhập Antigravity):', cmd: 'genh start' },
        { text: 'Trong lúc hộp chưa chạy, soạn trả lời khách và sàng lọc tin người lạ dùng nguồn AI khác — không dùng Antigravity chạy chung với khoá hệ thống.' },
      ],
    });
  }
  const a = h.autostart;
  if (a && a.state === 'warn') {
    // Lệnh cố định (cùng chuỗi genh/API in) — dựng từ giá trị đã lọc của API, không lấy chữ nào từ tệp trên máy chủ.
    const steps: HealthTipStep[] = [];
    if (a.docker_enabled === 'no') {
      steps.push(
        a.docker_mode === 'rootless'
          ? { text: 'Trên máy chủ, bật Docker (rootless) tự chạy khi mở máy — chạy một lần:', cmd: 'systemctl --user enable docker' }
          : { text: 'Trên máy chủ, bật Docker tự chạy khi mở máy — chạy một lần (máy hỏi mật khẩu đăng nhập máy):', cmd: 'sudo systemctl enable docker' },
      );
    }
    if (a.linger_required === true && a.linger === 'no') {
      steps.push({
        text: a.docker_mode === 'rootless'
          ? 'Cho Docker rootless, lịch tự cập nhật và nút Cập nhật ngay chạy cả khi không ai đăng nhập — chạy một lần:'
          : 'Cho lịch tự cập nhật và nút Cập nhật ngay chạy cả khi không ai đăng nhập — chạy một lần:',
        cmd: 'sudo loginctl enable-linger $USER',
      });
    }
    // Không hứa "đợi tới đêm": thiếu linger thì lịch đêm không chạy, tắt tự cập nhật thì không có lần chạy đêm nào.
    steps.push({ text: 'Chạy xong thì kiểm lại để cảnh báo tự hết:', cmd: 'genh status' });
    tips.push({ key: 'autostart', title: 'Cách bật tự chạy lại khi bật máy', steps });
  }
  const n = h.nightly;
  if (n && n.state === 'warn' && n.reason === 'opted_out_running') {
    // v0.1.53: Sếp đã tắt mà lịch vẫn bật — nói thật, đưa cả hai lựa chọn (cùng chuỗi thân sự cố host.nightly).
    tips.push({
      key: 'nightly',
      title: 'Sếp đã tắt tự cập nhật đêm nhưng lịch vẫn bật',
      steps: [
        { text: 'Trên máy chủ, xem tình trạng lịch:', cmd: 'genh auto-update status' },
        { text: 'Muốn tắt hẳn (chạy bằng đúng tài khoản đã cài Gen-Harness):', cmd: 'genh auto-update disable' },
        { text: 'Hoặc muốn giữ tự cập nhật mỗi đêm:', cmd: 'genh auto-update enable' },
      ],
    });
  } else if (n && n.state === 'warn') {
    // v0.1.53 (F-99): lệnh cố định (cùng chuỗi API ghép vào thân sự cố host.nightly) — không lấy chữ nào từ tệp trên máy chủ.
    // linger 'not_applicable' khi lịch là crontab/LaunchAgent/Task Scheduler ⇒ không nhắc enable-linger.
    const steps: HealthTipStep[] = [{ text: 'Trên máy chủ, xem tình trạng lịch:', cmd: 'genh auto-update status' }];
    if (n.linger === 'no') {
      steps.push({
        text: 'Cho tiến trình nền (lịch tự cập nhật, nút Cập nhật ngay) chạy cả khi không ai đăng nhập — chạy một lần (máy hỏi mật khẩu đăng nhập máy):',
        cmd: 'sudo loginctl enable-linger $USER',
      });
    }
    steps.push({ text: 'Sau đó bật lại lịch:', cmd: 'genh auto-update enable' });
    tips.push({ key: 'nightly', title: 'Cách bật lại lịch tự cập nhật đêm', steps });
  }
  // v0.1.54: người gác yêu cầu (.path) lỗi — lệnh cố định, chép được; không lấy chữ nào từ `watcher.hint`.
  const w = n?.watcher;
  if (w) {
    const lead = w.state === 'failed'
      ? `Người gác cập nhật đang lỗi, nút Cập nhật ngay chưa có người nhận (${watcherCause(w)}).`
      : `Người gác cập nhật đang chạy dự phòng (${watcherCause(w)}).`;
    const steps: HealthTipStep[] =
      w.reason === 'other'
        ? [
            { text: `${lead} Trên máy chủ, xem lỗi:`, cmd: 'systemctl --user status gen-harness-update-request.path' },
            { text: 'Sửa xong thì chạy:', cmd: 'genh auto-update enable' },
          ]
        : [
            { text: `${lead} Chạy trên máy:`, cmd: INOTIFY_FIX_COMMAND },
            { text: 'rồi:', cmd: 'genh auto-update enable' },
          ];
    tips.push({ key: 'watcher', title: w.state === 'failed' ? 'Người gác cập nhật đang lỗi' : 'Người gác cập nhật đang chạy dự phòng', steps });
  }
  return tips;
}

/** "Chi tiết kỹ thuật": lịch chạy (tên hàm + giờ chạy cuối theo múi giờ tổ chức) và từng hàng lỗi `<stream>.dlq`. */
export function healthTechRows(h: SystemHealth, tz = DEFAULT_TZ): { crons: HealthTechRow[]; queues: HealthTechRow[] } {
  const crons = h.crons.map((c) => ({
    name: c.name,
    value: c.last_at ? `${fmtDMClock(c.last_at, tz)} · ${c.ok === false ? 'lỗi' : c.ok === true ? 'ok' : '—'}` : 'chưa chạy',
    tone: (c.ok === false ? 'bad' : c.ok === true ? 'ok' : 'muted') as HealthTone,
  }));
  const queues = h.queues.map((q) => ({
    name: `${q.stream}.dlq`,
    value: `${fmtInt(q.dlq)}${q.dlq > 0 ? ` · ${QUEUE_LABELS[q.stream] ?? 'Việc nền khác'}` : ''}`,
    tone: (q.dlq > 0 ? 'warn' : 'ok') as HealthTone,
  }));
  return { crons, queues };
}

/** Sự cố cho dải "Cần Sếp xử lý": 'bad' trước 'warn', cùng mức thì mới nhất trước. */
export function sortIssues(issues: readonly HealthIssue[]): HealthIssue[] {
  const rank = (s: HealthIssue['severity']) => (s === 'bad' ? 0 : 1);
  return [...issues].sort((a, b) => rank(a.severity) - rank(b.severity) || String(b.raised_at).localeCompare(String(a.raised_at)));
}

/** Màu theo tông (token sẵn có — tự đổi theo theme sáng/tối). */
export const TONE_COLOR: Record<HealthTone, string> = {
  ok: 'var(--color-ok)',
  warn: 'var(--color-warn)',
  bad: 'var(--color-bad)',
  muted: 'var(--color-neutral-500)',
};
