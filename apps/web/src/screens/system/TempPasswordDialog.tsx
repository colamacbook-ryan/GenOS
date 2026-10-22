import { useState } from 'react';
import type { TempPasswordResult } from '@gen-harness/contracts';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { errorDetail } from '../../lib/errorText';
import { toast } from '../../lib/toast';
import { COPY_FAILED_TEXT } from './accessModel';
import { useAccess } from './queries';
import { roleLabel } from './systemModel';
import { inviteMessage } from './usersModel';

/**
 * Hộp "mật khẩu tạm" sau khi mời / đặt lại mật khẩu (tách từ UsersTab, v0.1.46 F-21). Địa chỉ đăng nhập trong lời nhắn
 * là `login_url` của `GET /system/access` (GH_PUBLIC_URL) — KHÔNG phải địa chỉ trình duyệt của Owner đang mở: Owner ngồi
 * ở máy chủ mở bằng localhost thì nhân viên ở máy khác không vào được, nên báo đỏ ngay trong hộp.
 */
export function TempPasswordDialog({ title, result, onClose }: { title: string; result: TempPasswordResult; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const access = useAccess({ pollWhileLocalMs: 5_000 });
  const loginUrl = access.data?.login_url ?? null;
  const copy = () => {
    const done = () => setCopied(true);
    const fail = () => toast(COPY_FAILED_TEXT, 'warn');
    // navigator.clipboard thiếu (trang không phải HTTPS) ⇒ ném đồng bộ — đổi thành Promise bị từ chối để báo toast.
    const writeText = (t: string): Promise<void> => {
      try {
        return navigator.clipboard.writeText(t);
      } catch (e) {
        return Promise.reject(e);
      }
    };
    // Địa chỉ đã là từ xa ⇒ chép ngay bản đang hiện, không chờ mạng.
    if (access.data && !access.data.public_url_local) {
      writeText(inviteMessage(result, loginUrl)).then(done, fail);
      return;
    }
    // Địa chỉ còn là localhost (hoặc chưa đọc được) ⇒ đọc lại lúc chép: Owner vừa chạy `genh remote …` theo cảnh báo đỏ
    // thì bản trong bộ nhớ đệm còn cũ — chép bản cũ là gửi nhân viên địa chỉ không mở được.
    const text = access.refetch().then(
      (fresh) => inviteMessage(result, fresh.data?.login_url ?? loginUrl),
      () => inviteMessage(result, loginUrl),
    );
    // Safari/WebKit chỉ cho chép khi lệnh chép được gọi NGAY trong lượt bấm — `await` mạng xong mới writeText là bị
    // NotAllowedError. ClipboardItem nhận Promise<Blob> ⇒ gọi clipboard.write đồng bộ ở đây, nội dung tới sau.
    if (typeof ClipboardItem !== 'undefined' && typeof navigator.clipboard?.write === 'function') {
      let item: ClipboardItem;
      try {
        item = new ClipboardItem({ 'text/plain': text.then((t) => new Blob([t], { type: 'text/plain' })) });
      } catch {
        void text.then(writeText).then(done, fail);
        return;
      }
      navigator.clipboard.write([item]).then(done, () => {
        // Trình duyệt cũ không nhận Promise trong ClipboardItem — thử writeText (Chrome/Firefox còn cho trong vài giây).
        void text.then(writeText).then(done, fail);
      });
      return;
    }
    void text.then(writeText).then(done, fail);
  };
  return (
    <Dialog
      open
      onClose={onClose}
      dismissable={false}
      width={460}
      title={title}
      kicker="Mật khẩu tạm chỉ hiện MỘT lần"
      actions={
        <>
          <Button variant="secondary" icon={copied ? 'ph ph-check' : 'ph ph-copy'} disabled={access.isPending} onClick={copy}>
            {copied ? 'Đã chép' : 'Chép lời nhắn gửi nhân viên'}
          </Button>
          <Button variant="primary" onClick={onClose}>
            Đã gửi, đóng
          </Button>
        </>
      }
    >
      {access.isError ? (
        <div className="inline-error" style={{ marginBottom: 10, fontSize: 12 }} role="alert" data-testid="invite-local-warning">
          Chưa đọc được địa chỉ đăng nhập — gửi kèm địa chỉ Console mà nhân viên mở được (lời nhắn đã chép không có dòng địa chỉ).
          {errorDetail(access.error) ? (
            <details className="tech-detail">
              <summary>Chi tiết kỹ thuật</summary>
              <code>{errorDetail(access.error)}</code>
            </details>
          ) : null}
        </div>
      ) : access.data?.public_url_local ? (
        <div className="inline-error" style={{ marginBottom: 10, fontSize: 12 }} role="alert" data-testid="invite-local-warning">
          Địa chỉ này chỉ mở được trên chính máy chủ — nhân viên ở máy khác hoặc điện thoại sẽ KHÔNG vào được. Trên máy chủ chạy <code className="mono">genh remote tailscale</code> (khuyên dùng) hoặc{' '}
          <code className="mono">genh remote --lan</code>, rồi bấm Chép lời nhắn lại.
        </div>
      ) : null}
      <div className="invite-result-row" data-gen-target="system.users.temp_password">
        <Icon name="ph ph-user-plus" size={15} color="var(--color-accent-300)" />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="invite-result-row__name">
            {result.user.display_name} · {roleLabel(result.user.role.code, result.user.role.name)}
          </div>
          <div className="invite-result-row__email">{result.user.email}</div>
        </div>
        <span className="mono invite-result-row__pw" aria-label="Mật khẩu tạm">
          {result.temp_password}
        </span>
      </div>
      <p className="muted-note">
        Gửi cho người này qua kênh riêng (Zalo, email cá nhân…) — lời nhắn đã chép gồm {loginUrl ? <>địa chỉ đăng nhập {loginUrl}, </> : 'địa chỉ đăng nhập (nếu đọc được), '}email và mật khẩu tạm. Đăng nhập lần đầu
        sẽ bắt đặt mật khẩu mới. Hộp này chỉ đóng bằng nút "Đã gửi, đóng" — đóng rồi là không xem lại được, quên thì bấm Đặt lại mật khẩu.
      </p>
    </Dialog>
  );
}
