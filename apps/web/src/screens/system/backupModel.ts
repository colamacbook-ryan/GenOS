import type { BackupJob, BackupRestoreStatus, BackupSchedule, BackupTrigger } from '@gen-harness/contracts';

export const BACKUPS_KEY = ['system', 'backups'] as const;
export const RESTORE_CONFIRM_TEXT = 'KHÔI PHỤC';
/** Máy chủ chưa có watcher nhận yêu cầu khôi phục (genh < v0.1.20): chạy một lần là có. */
export const ENABLE_COMMAND = '~/.gen-harness/bin/genh update';

export const TRIGGER_LABEL: Record<BackupTrigger, string> = {
  manual: 'Sao lưu tay',
  scheduled: 'Theo lịch',
  'pre-update': 'Trước khi cập nhật',
  'pre-restore': 'Trước khi khôi phục',
  'pre-import': 'Trước khi nhập gói',
};

export const TRIGGER_ICON: Record<BackupTrigger, string> = {
  manual: 'ph ph-user',
  scheduled: 'ph ph-clock',
  'pre-update': 'ph ph-arrow-circle-up',
  'pre-restore': 'ph ph-shield-check',
  'pre-import': 'ph ph-download-simple',
};

export const FREQUENCY_OPTIONS: Array<{ value: BackupSchedule['frequency']; label: string }> = [
  { value: 'daily', label: 'Hằng ngày' },
  { value: 'weekly', label: 'Hằng tuần' },
  { value: 'monthly', label: 'Hằng tháng' },
];

export const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

export function triggerLabel(t: BackupTrigger | null): string {
  return t ? (TRIGGER_LABEL[t] ?? t) : 'Không rõ';
}

/** "Hằng ngày lúc 02:00" / "Hằng tuần (thứ Hai) lúc 03:30" / "Chưa đặt lịch". */
export function scheduleText(s: BackupSchedule | null): string {
  if (!s) return 'Chưa đặt lịch tự động';
  const when =
    s.frequency === 'weekly' ? 'Hằng tuần (thứ Hai)' : s.frequency === 'monthly' ? 'Hằng tháng (ngày 1)' : 'Hằng ngày';
  return `${when} lúc ${s.time_of_day}`;
}

/** Tên tệp khi tải về: `gen-harness-<phần cuối của khoá>`. */
export function downloadName(key: string): string {
  return `gen-harness-${key.split('/').pop() ?? 'backup.enc'}`;
}

/** F106: câu cố định khi máy chủ không kèm câu riêng — lỗi gốc (pg_dump…) chỉ nằm trong "Chi tiết kỹ thuật". */
export const BACKUP_FAILED_TEXT = 'Chưa sao lưu được — thử lại; nếu vẫn lỗi gửi gói chẩn đoán.';
const RESTORE_FAILED_TEXT = 'Dữ liệu đã quay về như trước khi khôi phục. Thử lại; nếu vẫn lỗi gửi gói chẩn đoán.';

export type JobView =
  | { kind: 'none' }
  | { kind: 'working'; text: string }
  | { kind: 'failed'; text: string; technical: string | null }
  | { kind: 'stalled'; text: string };

export function jobView(job: BackupJob | null | undefined): JobView {
  if (!job) return { kind: 'none' };
  if (job.state === 'queued') return { kind: 'working', text: 'Đang chờ Bộ xử lý nền nhận việc sao lưu…' };
  if (job.state === 'running') return { kind: 'working', text: 'Đang sao lưu dữ liệu — thường mất dưới một phút…' };
  if (job.state === 'stalled') {
    return { kind: 'stalled', text: 'Việc sao lưu chưa chạy sau 30 phút — Bộ xử lý nền có thể đang tắt. Bấm Sao lưu ngay để thử lại.' };
  }
  if (job.state === 'failed') {
    const technical = typeof job.technical === 'string' && job.technical.trim() ? job.technical.trim() : null;
    return { kind: 'failed', text: job.message?.trim() || BACKUP_FAILED_TEXT, technical };
  }
  return { kind: 'none' };
}

type StepState = 'done' | 'active' | 'todo';
export type RestoreView =
  | { kind: 'hidden' }
  | {
      kind: 'working' | 'finished' | 'failed' | 'stalled' | 'done';
      tone: 'accent' | 'ok' | 'warn' | 'bad';
      title: string;
      body?: string;
      /** Thông điệp gốc của trình khôi phục — chỉ cho "Chi tiết kỹ thuật". */
      technical?: string | null;
      command?: string;
      steps: Array<{ label: string; state: StepState }>;
    };

const RECENT_MS = 24 * 3600 * 1000;

function recent(iso: string | null, now: number): boolean {
  if (!iso) return false;
  const t = Date.parse(iso);
  return Number.isFinite(t) && now - t < RECENT_MS;
}

/**
 * Tiến trình khôi phục từ `restore` (GET /system/backups) + việc Sếp vừa bấm (waiting) + máy chủ đang tắt vì genh dừng
 * máy chủ/Bộ xử lý nền để khôi phục (offline). Hàm thuần để test đủ các nhánh — cùng khuôn `updateView`.
 */
export function restoreView(
  r: BackupRestoreStatus | undefined,
  opts: { waiting: boolean; offline: boolean; label?: string; now?: number },
): RestoreView {
  const now = opts.now ?? Date.now();
  const what = opts.label ? `bản ${opts.label}` : 'bản sao lưu';
  const steps = (active: number) =>
    ['Nhận yêu cầu', 'Sao lưu an toàn, khôi phục dữ liệu', 'Khởi động lại & kiểm tra'].map((label, i) => ({
      label,
      state: (i < active ? 'done' : i === active ? 'active' : 'todo') as StepState,
    }));
  if (opts.waiting && opts.offline) {
    return { kind: 'working', tone: 'accent', title: `Đang khôi phục ${what}`, body: 'Hệ thống đang dừng tạm để khôi phục — trang tự tải lại khi xong.', steps: steps(1) };
  }
  if (!r) return { kind: 'hidden' };
  if (r.state === 'requested') {
    return { kind: 'working', tone: 'accent', title: `Đang khôi phục ${what}`, body: 'Đã gửi yêu cầu, máy chủ sẽ bắt đầu trong vòng 1 phút.', steps: steps(0) };
  }
  if (r.state === 'running') {
    return { kind: 'working', tone: 'accent', title: `Đang khôi phục ${what}`, body: 'Mất khoảng 2–5 phút; Console tạm ngắt — trang tự tải lại khi xong.', steps: steps(1) };
  }
  if (r.state === 'stalled') {
    return {
      kind: 'stalled', tone: 'warn', title: 'Máy chủ chưa nhận yêu cầu khôi phục',
      body: 'Đã quá 15 phút mà chưa bắt đầu. Bấm Khôi phục lại, hoặc chạy lệnh dưới đây trên máy chủ.',
      command: r.key ? `~/.gen-harness/bin/genh restore ${r.key}` : undefined, steps: [],
    };
  }
  if (r.state === 'failed' && recent(r.finished_at, now)) {
    return {
      kind: 'failed', tone: 'bad', title: 'Khôi phục chưa thành công',
      body: RESTORE_FAILED_TEXT, technical: r.message?.trim() || null, steps: [],
    };
  }
  if (r.state === 'done' && opts.waiting) {
    return { kind: 'finished', tone: 'ok', title: 'Đã khôi phục xong', body: 'Đang tải lại trang…', steps: [] };
  }
  if (r.state === 'done' && recent(r.finished_at, now)) {
    return {
      kind: 'done', tone: 'ok', title: 'Đã khôi phục dữ liệu',
      body: r.safety_key ? 'Dữ liệu ngay trước khi khôi phục được giữ trong danh sách (nguồn "Trước khi khôi phục") — cần quay lại thì khôi phục bản đó.' : undefined,
      steps: [],
    };
  }
  return { kind: 'hidden' };
}
