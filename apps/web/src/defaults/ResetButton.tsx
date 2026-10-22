import { useState } from 'react';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { toast } from '../lib/toast';
import { InlineError } from '../screens/common';
import { DefaultBadge } from './DefaultBadge';
import { RESET_LABEL, asText, confirmLines, findDefault } from './defaultsModel';
import { useDefaults, useResetDefault } from './queries';

/**
 * v0.1.55 — nút "Về mặc định" của MỘT mục trong sổ mặc định. Bấm ⇒ hộp Xác nhận nói rõ đang dùng gì và mặc định là gì ⇒
 * Xác nhận mới gọi `POST /defaults/{key}/reset`. Lỗi = câu thân thiện + "Chi tiết kỹ thuật" (không bao giờ render object).
 * Chỉ hiện khi mục "Đã đổi" và cho phép reset; Mặc định rồi / chỉ-xem ⇒ không vẽ gì.
 */
export function ResetButton({ itemKey, onDone }: { itemKey: string; onDone?: () => void }) {
  const q = useDefaults();
  const item = findDefault(q.data, itemKey);
  const [open, setOpen] = useState(false);
  const reset = useResetDefault();
  if (!item || !item.customized || !item.resettable) return null;
  const { current, standard } = confirmLines(item);
  const close = () => {
    if (reset.isPending) return;
    setOpen(false);
    reset.reset();
  };
  const confirm = () =>
    reset.mutate(itemKey, {
      onSuccess: () => {
        toast(`Đã về mặc định: ${asText(item.label)}`, 'ok');
        setOpen(false);
        onDone?.();
      },
    });
  return (
    <>
      <Button variant="secondary" size="sm" icon="ph ph-arrow-counter-clockwise" data-testid={`reset-${itemKey}`} onClick={() => setOpen(true)}>
        {RESET_LABEL}
      </Button>
      <Dialog
        open={open}
        onClose={close}
        width={440}
        title={`Về mặc định: ${asText(item.label)}?`}
        kicker="Sếp xác nhận trước khi đổi"
        actions={
          <>
            <Button variant="secondary" disabled={reset.isPending} onClick={close}>
              Huỷ
            </Button>
            <Button variant="primary" icon="ph ph-check" loading={reset.isPending} onClick={confirm}>
              Xác nhận
            </Button>
          </>
        }
      >
        <p className="muted-note" data-testid="reset-current">
          <Icon name="ph ph-sliders-horizontal" size={12} /> Đang dùng: {current}
        </p>
        <p className="muted-note" data-testid="reset-default">
          <Icon name="ph ph-arrow-counter-clockwise" size={12} /> Mặc định: {standard}
        </p>
        <p className="muted-note">Khoá API, mã PIN và các kết nối của Sếp không bị đụng tới.</p>
        {reset.isError ? <InlineError detail={errorDetail(reset.error)}>{errorText(reset.error)}</InlineError> : null}
      </Dialog>
    </>
  );
}

/** Chip "Mặc định / Đã đổi" + nút "Về mặc định" của một khoá — gắn vào `aside` của thẻ cài đặt. Vai trò khác: không vẽ gì. */
export function DefaultControls({ itemKey }: { itemKey: string }) {
  const q = useDefaults();
  const item = findDefault(q.data, itemKey);
  if (!item) return null;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }} data-testid={`default-controls-${itemKey}`}>
      <DefaultBadge customized={item.customized} testId={`default-badge-${itemKey}`} />
      <ResetButton itemKey={itemKey} />
    </span>
  );
}
