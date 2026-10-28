import { Icon } from '@gen-harness/ui';

/** Lý do dùng chung cho tooltip của mọi nút sửa/chạy đã chuyển sang chỉ đọc ở hai màn "đường lọc cũ". */
export const LEGACY_READONLY_REASON = 'Đường lọc cũ — chỉ đọc. Tình báo không dựa vào bộ lọc này nên không sửa hay chạy ở đây.';

/** v0.1.78 (G9): dải giải thích cho Quy tắc sàng lọc và Kho sạch — bộ lọc cũ chạy song song, chỉ đọc. */
export function LegacyFilterNotice({ screen }: { screen: 'rules' | 'clean' }) {
  return (
    <div className="legacy-notice" role="note" data-testid="legacy-filter-notice" data-screen={screen}>
      <span className="legacy-notice__tag">
        <Icon name="ph ph-clock-counter-clockwise" size={12} />
        Đường lọc cũ
      </span>
      <p className="legacy-notice__text">
        Đây là bộ lọc cũ chạy song song. Tình báo không dựa vào nó, nên màn này chỉ để xem. Sẽ xem xét bỏ ở bản sau.
      </p>
    </div>
  );
}
