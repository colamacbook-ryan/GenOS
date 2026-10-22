import { Link } from 'react-router-dom';
import { Icon } from '@gen-harness/ui';

/**
 * v0.1.29 (Boss 30/09): bước 4 "Để sau" được — khi chưa có model nào gán cho Gen/Sàng lọc thì nói thẳng hậu quả và
 * cho một nút sửa ngay. Dùng ở dải "Cần Sếp xử lý" đầu Tổng quan (v0.1.68: bỏ hàm `NoModelBanner` không còn nơi nào dùng — F040). v0.1.59: nút "Chọn model" của MỌI nơi dẫn tới
 * `/setup?buoc=nguon-ai` (màn Thiết lập nguồn AI) — `to` do nơi gọi truyền, đều lấy từ `AI_SOURCE_PATH`.
 */
export function NoModelNotice({ to }: { to: string }) {
  return (
    <div className="risk-box no-model" role="note" data-testid="no-model">
      <Icon name="ph ph-warning-octagon" size={16} color="var(--color-bad)" />
      <div className="no-model__body">
        <div className="risk-box__title">Chưa có model</div>
        <p className="risk-box__text">
          Gen (trợ lý) và sàng lọc tin sẽ không chạy tới khi Sếp chọn model. Tin nhắn vẫn được gom về kho thô.
        </p>
      </div>
      <Link to={to} className="gh-btn gh-btn--primary btn-24">
        <Icon name="ph ph-brain" size={12} />
        Chọn model
      </Link>
    </div>
  );
}
