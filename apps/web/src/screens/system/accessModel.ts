/** v0.1.46 (F-21) — thẻ "Truy cập từ xa": nhãn chế độ, cảnh báo và các lệnh `genh remote` (hàm thuần, dùng chung với test). */
import type { AccessInfo, AccessMode } from '@gen-harness/contracts';

export const MODE_LABEL: Record<AccessMode, string> = {
  local: 'Chỉ máy này',
  lan: 'Mạng nội bộ (LAN)',
  lan_legacy: 'Đang mở cho cả mạng (bản cài cũ)',
  tailscale: 'Tailscale',
  cloudflare: 'Cloudflare Tunnel',
  unknown: 'Chưa rõ',
};

export function modeLabel(mode: string | null | undefined): string {
  return MODE_LABEL[(mode ?? 'unknown') as AccessMode] ?? MODE_LABEL.unknown;
}

export interface AccessCommand {
  key: string;
  title: string;
  /** Lệnh hiển thị (có thể kèm chỗ trống <…> cho Sếp thay). */
  cmd: string;
  /** Chuỗi nút "Chép" đưa vào bộ nhớ tạm — không chứa chỗ trống <…> (dán vào bash thì `<` là chuyển hướng đầu vào, lệnh hỏng). */
  copy: string;
  note: string;
}

/** Câu báo khi không chép được — dùng chung cho hộp mời và thẻ Truy cập từ xa (cùng câu, cùng mức 'warn'). */
export const COPY_FAILED_TEXT = 'Không chép được — hãy bôi đen và chép tay.';

/** Lệnh Owner chạy TRÊN MÁY CHỦ để đổi cách truy cập (Console không có nút đổi — tránh tự cắt truy cập). */
export const ACCESS_COMMANDS: readonly AccessCommand[] = [
  {
    key: 'tailscale',
    title: 'Tailscale (khuyên dùng)',
    cmd: 'genh remote tailscale',
    copy: 'genh remote tailscale',
    note: 'Khoảng 5 phút: cài app Tailscale trên điện thoại và đăng nhập cùng tài khoản.',
  },
  { key: 'local', title: 'Chỉ máy này', cmd: 'genh remote --local', copy: 'genh remote --local', note: 'Chỉ mở được trên chính máy chủ — điện thoại và máy khác không vào được.' },
  { key: 'lan', title: 'Mạng nội bộ (LAN)', cmd: 'genh remote --lan', copy: 'genh remote --lan', note: 'Máy cùng Wi-Fi/mạng vào được — phải cài Chứng chỉ CA cho từng điện thoại.' },
  {
    key: 'cloudflare',
    title: 'Cloudflare Tunnel',
    cmd: 'genh remote cloudflare --hostname <tên-miền>',
    copy: 'genh remote cloudflare --hostname ',
    note: 'Dùng tên miền của Sếp qua Cloudflare. Nút Chép chỉ chép phần đầu lệnh — dán vào rồi gõ tiếp tên miền, ví dụ gen.congty.vn.',
  },
];

/** Cảnh báo theo chế độ (null = không cần). */
export function modeWarning(a: Pick<AccessInfo, 'mode' | 'public_url_local'>): string | null {
  if (a.mode === 'lan_legacy') {
    return 'Mọi máy cùng mạng (Wi-Fi văn phòng, khách…) đều thấy trang đăng nhập Gen-Harness. Hãy chọn Tailscale (khuyên dùng) hoặc Chỉ máy này.';
  }
  if (a.mode === 'lan') {
    return 'Mọi máy cùng mạng nội bộ đều thấy trang đăng nhập. Chỉ nên dùng ở mạng tin cậy; mỗi điện thoại cần cài Chứng chỉ CA.';
  }
  if (a.public_url_local) {
    return 'Địa chỉ đăng nhập hiện chỉ mở được trên chính máy chủ — nhân viên ở máy khác hoặc điện thoại chưa vào được.';
  }
  return null;
}
