import { useState } from 'react';
import { ApiError } from '@gen-harness/contracts';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { errorDetail, errorText } from '../lib/errorText';
import { toast } from '../lib/toast';
import { InlineError, PinHint } from '../screens/common';
import { DefaultBadge } from './DefaultBadge';
import {
  APPLY_STANDARD_LABEL,
  RESET_ALL_LABEL,
  STANDARD_ANCHOR,
  STANDARD_MODE_TITLE,
  asText,
  changedAutonomy,
  changedCount,
  customCoreBindingCount,
  standardModeText,
  suggestionOf,
} from './defaultsModel';
import { useApplyStandard, useDefaults, useResetAll } from './queries';

/**
 * v0.1.55 — dải ở đầu Cài đặt › Bộ não AI: "Chế độ tiêu chuẩn: đang dùng / đã đổi N mục · [Về mặc định tất cả]".
 * - "Về mặc định tất cả": hộp Xác nhận ⇒ `POST /defaults/reset-all`; máy chủ đòi mã PIN (423) ⇒ hộp PIN toàn cục (PinDialogHost)
 *   tự mở rồi gửi lại. Không đụng khoá API, phiên CLI, mã PIN, mật khẩu, Gen-hub, Telegram, tài khoản Facebook.
 * - "Áp model chuẩn theo vai": chỉ hiện khi máy chủ gợi ý `apply_standard` (nhiều việc đang dùng chung 1 model); neo `#chuan`.
 * CHỈ Owner (vai trò khác không vẽ, không gọi `/defaults`).
 */
export function StandardModeStrip() {
  const q = useDefaults();
  const data = q.data;
  const [resetOpen, setResetOpen] = useState(false);
  const [applyOpen, setApplyOpen] = useState(false);
  const resetAll = useResetAll();
  const apply = useApplyStandard();
  // Lần tải đầu của Owner (hoặc vai trò khác: query tắt ⇒ chưa có dữ liệu): chưa có gì để hiện.
  if (q.isError && !data) {
    const status = q.error instanceof ApiError ? q.error.status : 0;
    // Máy chủ cũ (404) / không phải Owner (403): không vẽ dải. Lỗi khác: câu thân thiện + Chi tiết kỹ thuật.
    if (status === 404 || status === 403) return null;
    return (
      <div id={STANDARD_ANCHOR} className="gh-card" data-testid="standard-strip-error">
        <InlineError detail={errorDetail(q.error)}>{errorText(q.error)}</InlineError>
      </div>
    );
  }
  if (!data) return null;
  const n = changedCount(data);
  const customBindings = customCoreBindingCount(data);
  const autonomy = changedAutonomy(data);
  const suggestion = suggestionOf(data, 'apply_standard');

  const closeReset = () => {
    if (resetAll.isPending) return;
    setResetOpen(false);
    resetAll.reset();
  };
  const closeApply = () => {
    if (apply.isPending) return;
    setApplyOpen(false);
    apply.reset();
  };

  return (
    <div id={STANDARD_ANCHOR} className="gh-card" data-testid="standard-strip" data-changed={n}>
      <div className="gh-card__body" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Icon name="ph ph-seal-check" size={16} />
        <strong data-testid="standard-strip-text">{standardModeText(n)}</strong>
        <DefaultBadge customized={n > 0} testId="standard-strip-badge" />
        <span style={{ flex: 1 }} />
        {suggestion ? (
          <Button variant="secondary" size="sm" icon="ph ph-magic-wand" data-testid="apply-standard" onClick={() => setApplyOpen(true)}>
            {APPLY_STANDARD_LABEL}
          </Button>
        ) : null}
        <Button variant="secondary" size="sm" icon="ph ph-arrow-counter-clockwise" data-testid="reset-all" disabled={n === 0} onClick={() => setResetOpen(true)}>
          {RESET_ALL_LABEL}
        </Button>
        <PinHint text="Cần mã PIN 6 số" title="Về mặc định tất cả cần mã PIN" />
      </div>
      {suggestion ? (
        <div className="gh-card__body" data-testid="apply-standard-hint" style={{ paddingTop: 0 }}>
          <p className="muted-note">
            <strong>{asText(suggestion.title)}</strong> {asText(suggestion.body)}
          </p>
        </div>
      ) : null}

      <Dialog
        open={resetOpen}
        onClose={closeReset}
        width={460}
        title={`${RESET_ALL_LABEL}?`}
        kicker="Cần mã PIN — Sếp xác nhận trước khi đổi"
        actions={
          <>
            <Button variant="secondary" disabled={resetAll.isPending} onClick={closeReset}>
              Huỷ
            </Button>
            <Button
              variant="primary"
              icon="ph ph-check"
              loading={resetAll.isPending}
              onClick={() =>
                resetAll.mutate(undefined, {
                  onSuccess: () => {
                    toast(`Đã về ${STANDARD_MODE_TITLE.toLowerCase()}`, 'ok');
                    setResetOpen(false);
                  },
                })
              }
            >
              Xác nhận
            </Button>
          </>
        }
      >
        <p className="muted-note" data-testid="reset-all-body">
          Em đưa {n} mục Sếp đã đổi về mặc định (model theo vai, lọc tin, lịch sao lưu, trần chi phí AI, tuỳ chọn Gen…). Khoá API,
          phiên đăng nhập CLI, mã PIN, mật khẩu, kết nối Gen-hub, Telegram và tài khoản Facebook của Sếp được giữ nguyên.
        </p>
        {autonomy ? (
          <p className="muted-note" data-testid="reset-all-autonomy">
            <strong>Có cả mức tự trị của tổ chức:</strong> đang là “{asText(autonomy.current_text)}”, em đưa về “{asText(autonomy.default_text)}”. Sếp
            chưa muốn đổi mức này thì Huỷ rồi về mặc định từng mục khác.
          </p>
        ) : null}
        {resetAll.isError ? <InlineError detail={errorDetail(resetAll.error)}>{errorText(resetAll.error)}</InlineError> : null}
      </Dialog>

      <Dialog
        open={applyOpen}
        onClose={closeApply}
        width={460}
        title={`${APPLY_STANDARD_LABEL}?`}
        kicker="Sếp xác nhận trước khi đổi"
        actions={
          <>
            <Button variant="secondary" disabled={apply.isPending} onClick={closeApply}>
              Huỷ
            </Button>
            <Button
              variant="primary"
              icon="ph ph-check"
              loading={apply.isPending}
              onClick={() =>
                apply.mutate(undefined, {
                  onSuccess: () => {
                    toast('Đã áp model chuẩn theo vai', 'ok');
                    setApplyOpen(false);
                  },
                })
              }
            >
              Xác nhận
            </Button>
          </>
        }
      >
        <p className="muted-note" data-testid="apply-standard-body">
          Em bỏ {customBindings > 0 ? `${customBindings} dòng` : 'các dòng'} gán model của Gen, Bản tin Gen, lọc tin và soạn nháp; từ đó em tự
          chọn model hợp từng việc (việc nền dùng model nhanh, rẻ). Khoá API và nguồn AI giữ nguyên; Sếp gán lại bất cứ lúc nào ở API &amp; Model.
        </p>
        {apply.isError ? <InlineError detail={errorDetail(apply.error)}>{errorText(apply.error)}</InlineError> : null}
      </Dialog>
    </div>
  );
}
