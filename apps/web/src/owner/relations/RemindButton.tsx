/**
 * v0.1.59 (G4) — nút "Tạo nhắc" dùng chung của Quan hệ (dòng bảng, hồ sơ) và Thông tin (thanh hành động).
 *
 * Một lần bấm tạo MỘT nhắc việc hẹn 09:00 sáng mai (`useCreateReminder` → `POST /tasks`, gắn với người). Không gửi gì ra ngoài.
 * Xong thì nút đổi thành "Đã tạo nhắc" (khoá, tránh tạo trùng) kèm dòng báo; lỗi = câu thân thiện + "Chi tiết kỹ thuật".
 */
import { Link } from 'react-router-dom';
import { Icon } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { InlineError } from '../../screens/common';
import { OWNER_PATHS } from '../ownerModel';
import { tomorrowMorning, useCreateReminder, type ReminderInput } from '../actions/useOwnerActions';
import './relations.css';

export interface RemindButtonProps {
  /** Nội dung nhắc, vd "Liên hệ lại Bùi Đức Thắng". */
  title: string;
  subject?: ReminderInput['subject'];
  label?: string;
  variant?: 'secondary' | 'ghost' | 'primary';
  /** Tiền tố data-testid: nút = `<testId>`, dòng báo = `<testId>-note`. */
  testId?: string;
}

export function RemindButton({ title, subject, label = 'Tạo nhắc', variant = 'secondary', testId }: RemindButtonProps) {
  const remind = useCreateReminder();
  const done = remind.isSuccess;
  return (
    <span className="rel-remind">
      <button
        type="button"
        className={`kit-btn kit-btn--${variant}`}
        data-testid={testId}
        disabled={remind.isPending || done}
        aria-busy={remind.isPending || undefined}
        onClick={() => remind.mutate({ title, subject, due_at: tomorrowMorning() })}
      >
        {done ? <Icon name="ph ph-check" size={16} /> : null}
        {done ? 'Đã tạo nhắc' : remind.isPending ? 'Đang tạo…' : label}
      </button>
      {done ? (
        <span className="rel-note" role="status" data-testid={testId ? `${testId}-note` : undefined}>
          Nhắc lúc 09:00 sáng mai — xem ở <Link to={OWNER_PATHS.work}>Việc</Link>
        </span>
      ) : null}
      {remind.isError ? (
        <span className="rel-note rel-note--bad" data-testid={testId ? `${testId}-error` : undefined}>
          <InlineError detail={errorDetail(remind.error)}>{errorText(remind.error)}</InlineError>
        </span>
      ) : null}
    </span>
  );
}
