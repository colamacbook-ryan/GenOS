import { useEffect, useState } from 'react';
import { Segmented, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { StepFrame } from './StepFrame';
import { describeError, type StepProps } from './types';

type Frequency = 'daily' | 'weekly' | 'monthly';

const FREQUENCY_OPTIONS: Array<{ value: Frequency; label: string }> = [
  { value: 'daily', label: 'Hằng ngày' },
  { value: 'weekly', label: 'Hằng tuần' },
  { value: 'monthly', label: 'Hằng tháng' },
];
const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Bước 11 — Sao lưu. v0.1.55 (Thiết lập gọn): sao lưu TỰ BẬT hằng ngày 02:00, giữ theo vòng đời GFS (7 bản theo ngày, 4 theo tuần, 12 theo tháng) — đi tiếp hay "Để sau" đều ghi lịch mặc
 * định; đổi giờ / tần suất nằm trong mục "Đổi lịch" (đóng sẵn). (PLAN 4.6) Chỉ lưu LỊCH (`frequency`/`time_of_day`) — đích LUÔN trong máy và
 * vòng đời GIỮ LUÔN CỐ ĐỊNH theo GFS 7 ngày/4 tuần/12 tháng (`gh/backup.py`, v0.1.1 mục 5.6), không cấu hình
 * được ở đây (nên KHÔNG có ô "giữ bao nhiêu bản" hay đích MinIO — MinIO đã gỡ khỏi hệ thống ở v0.1.1). Vẫn gửi
 * `retention_count`/`destination` mặc định lên API để tương thích với client cũ đọc lại state này. */
export function Step11Backup({ meta, status, description, onBack, onSaved, formRef, onSkip, skipping, skipError }: StepProps) {
  const [frequency, setFrequency] = useState<Frequency>('daily');
  const [timeOfDay, setTimeOfDay] = useState('02:00');
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [timeTouched, setTimeTouched] = useState(false);

  useEffect(() => setFormError(null), [frequency, timeOfDay]);

  // F103: lịch mặc định chỉ được GHI khi Sếp bấm Tiếp tục / Để sau ở bước này — chưa bấm thì chưa "đã tự bật".
  const scheduled = status === 'done' || status === 'skipped';

  const timeInvalid = timeTouched && !TIME_RE.test(timeOfDay);
  const canContinue = TIME_RE.test(timeOfDay);

  const save = async () => {
    setBusy(true);
    setFormError(null);
    try {
      // retention_count/destination: giữ nguyên giá trị mặc định cũ để tương thích API — backend không dùng
      // nữa (vòng đời GFS cố định), chỉ còn ý nghĩa hiển thị/tương thích ngược (xem docstring `gh/backup.py`).
      const state = await api.setup.step11({ frequency, time_of_day: timeOfDay, retention_count: 7, destination: 'local' });
      onSaved(state);
    } catch (e) {
      setFormError(describeError(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <StepFrame
      n={meta.n}
      title={meta.title}
      description={description}
      formRef={formRef}
      canContinue={canContinue}
      busy={busy}
      onContinue={() => void save()}
      onBack={onBack}
      onSkip={onSkip}
      skipping={skipping}
      formError={formError ?? skipError}
    >
      <div className="setup-section" data-testid="step11-default">
        <div className="setup-section__title">{scheduled ? 'Sao lưu đã tự bật' : 'Sao lưu sẽ tự bật khi Sếp đi tiếp'}</div>
        <p className="muted-note">
          {scheduled
            ? 'Hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng, lưu mã hoá ngay trong máy chủ. Đi tiếp hay bấm Để sau thì lịch này vẫn chạy; muốn đổi giờ hay tần suất thì mở “Đổi lịch” bên dưới.'
            : 'Hằng ngày lúc 02:00, giữ 7 bản theo ngày, 4 theo tuần, 12 theo tháng, lưu mã hoá ngay trong máy chủ. Lịch này chỉ được ghi khi Sếp bấm Tiếp tục hoặc Để sau ở bước này; rời đi trước đó thì máy chưa tự sao lưu. Muốn đổi giờ hay tần suất thì mở “Đổi lịch” bên dưới.'}
        </p>
      </div>
      <details className="setup-section brain-advanced">
        <summary>Đổi lịch (không bắt buộc)</summary>
        <div className="setup-section__title">Lịch sao lưu</div>
        <div className="setup-grid">
          <div className="seg-field">
            <span className="seg-field__label">Tần suất</span>
            <Segmented label="Tần suất sao lưu" value={frequency} onChange={(v) => setFrequency(v as Frequency)} options={FREQUENCY_OPTIONS} />
          </div>
          <TextField
            label="Giờ chạy (HH:MM)"
            value={timeOfDay}
            onChange={(e) => setTimeOfDay(e.target.value)}
            onBlur={() => setTimeTouched(true)}
            error={timeInvalid ? 'Giờ chạy sao lưu dạng HH:MM (00:00–23:59)' : null}
            placeholder="02:00"
            maxLength={5}
          />
        </div>
      </details>
      <div className="setup-section">
        <div className="setup-section__title">Đích lưu &amp; giữ bao lâu</div>
        <p className="muted-note">
          Bản sao lưu được mã hoá và lưu ngay trong máy chủ này. Vòng đời giữ bản theo lịch cố định
          (GFS): 7 bản gần nhất theo ngày, 4 bản theo tuần, 12 bản theo tháng — bản cũ hơn tự động dọn,
          không cần chỉnh số lượng giữ.
        </p>
      </div>
    </StepFrame>
  );
}
