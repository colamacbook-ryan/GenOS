import { AUTONOMY_AUTO_LABEL, AUTONOMY_CHOICES, AUTONOMY_LEVELS, autonomyChoice, autonomyLegacyHint } from '@gen-harness/contracts';

/** Hai mức chỉ đặt được trong khối "Nâng cao" (nhãn lấy từ nguồn duy nhất `AUTONOMY_LEVELS`). */
const ADVANCED_LEVELS = [5, 6] as const;

export const AUTONOMY_ADVANCED_WARNING =
  'Ở mức này trợ lý tự làm việc nội bộ (nhắc việc, báo cáo) không hỏi lại. Tin gửi ra ngoài vẫn luôn chờ Sếp duyệt.';

/**
 * v0.1.43 (F-30) — chọn mức tự trị theo 3 mức dễ hiểu ("Chỉ ghi nhận" · "Gợi ý" · "Soạn sẵn chờ duyệt"); backend vẫn
 * giữ thang 0–6. Mức 5/6 chỉ đặt được trong khối <details> "Nâng cao" và hiện là "Tự làm (đặt ở Nâng cao)".
 *
 * Chỉ hiển thị không bao giờ ghi: `onChange(null)` nghĩa là "giữ nguyên mức đang lưu" — chỉ khi bấm đúng mức đang
 * lưu. Mức 1/2 (thang cũ) hiện trong nhóm "Chỉ ghi nhận" kèm câu nói rõ mức thật; bấm "Chỉ ghi nhận" khi đó GHI mức 0
 * (mức 2 vẫn gọi được công cụ nên không được coi là "chỉ ghi nhận"). Mở/đóng "Nâng cao" không gọi `onChange`.
 */
export function AutonomySelect({
  current,
  value,
  onChange,
  keepLabel,
  label = 'Mức tự trị',
}: {
  /** Mức đang lưu (0–6) — chỉ để hiển thị; `null` khi chưa đặt hoặc hộp hàng loạt. */
  current: number | null;
  /** Mức Sếp vừa chọn; `null` = giữ nguyên. */
  value: number | null;
  onChange: (level: number | null) => void;
  /** Hộp hàng loạt: thêm nút "Giữ nguyên" (→ `onChange(null)`). */
  keepLabel?: string;
  label?: string;
}) {
  const effective = value ?? current;
  const active = autonomyChoice(effective);
  const keeping = !!keepLabel && value === null;

  // Đúng mức đang lưu → giữ nguyên (không ghi lại). Mức 1/2 bấm "Chỉ ghi nhận" → ghi 0 thật.
  const pick = (level: number) => onChange(current !== null && level === current ? null : level);

  const pressedChoice = AUTONOMY_CHOICES.find((c) => !keeping && active?.key === c.key);
  const hint = keeping
    ? 'Giữ nguyên mức tự trị hiện tại của từng người.'
    : active?.key === 'auto'
      ? AUTONOMY_ADVANCED_WARNING
      : (autonomyLegacyHint(effective) ?? pressedChoice?.hint ?? 'Chưa đặt mức tự trị riêng — dùng mức mặc định.');

  return (
    <div className="dir-filter-row" data-testid="autonomy-select">
      <span className="dir-filter-row__label">{label}</span>
      <div className="dir-filter-row__opts" role="group" aria-label={label}>
        {keepLabel ? (
          <button type="button" className="dir-filter-row__opt" aria-pressed={keeping} onClick={() => onChange(null)}>
            {keepLabel}
          </button>
        ) : null}
        {AUTONOMY_CHOICES.map((c) => (
          <button
            key={c.key}
            type="button"
            className="dir-filter-row__opt"
            aria-pressed={!keeping && active?.key === c.key}
            title={c.hint}
            onClick={() => pick(c.level)}
          >
            {c.label}
          </button>
        ))}
        {!keeping && active?.key === 'auto' ? (
          <button type="button" className="dir-filter-row__opt" aria-pressed="true" disabled title="Mức 5–6 chỉ đổi trong khối Nâng cao">
            {AUTONOMY_AUTO_LABEL}
          </button>
        ) : null}
      </div>
      <p className="muted-note">{hint}</p>
      <details className="brain-advanced">
        <summary>Nâng cao — mức 5–6 (tự làm việc nội bộ)</summary>
        <div className="dir-filter-row__opts" role="group" aria-label="Mức tự trị nâng cao">
          {ADVANCED_LEVELS.map((n) => (
            <button key={n} type="button" className="dir-filter-row__opt" aria-pressed={!keeping && effective === n} onClick={() => pick(n)}>
              {`Mức ${n} · ${AUTONOMY_LEVELS[n]}`}
            </button>
          ))}
        </div>
        <p className="muted-note" role="note">
          {AUTONOMY_ADVANCED_WARNING}
        </p>
      </details>
    </div>
  );
}
