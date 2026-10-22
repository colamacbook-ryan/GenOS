import { useState } from 'react';
import { Link } from 'react-router-dom';
import { ApiError } from '@gen-harness/contracts';
import type { BackgroundSources } from '@gen-harness/contracts';
import { Button, Dialog, Icon, Switch } from '@gen-harness/ui';
import { errorDetail, errorText } from '../../lib/errorText';
import { detailToText } from '../../lib/friendlyError';
import { useCan } from '../../lib/permissions';
import { AGY_BACKGROUND_NOTE } from '../../lib/agyLayers';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, PinHint, SkeletonLines } from '../common';
import { useBackgroundSources, useSetBackgroundSources } from './queries';

/** Nguồn CLI duy nhất Owner phải bật cho việc nền (QD-12). Antigravity CLI chạy việc nền không cần công tắc (v0.1.62). */
const CLAUDE_CLI = 'claude_code_cli';

/**
 * Tên mục đích việc nền cho người đọc. Máy chủ trả nhãn ("Sàng lọc tin", "Trực việc (agent soạn nháp)", "Bản tin Gen" —
 * gh/providers/router.py BACKGROUND_PURPOSE_LABELS); mã purpose (refinery…) vẫn nhận để tương thích.
 */
const PURPOSE_LABEL: Record<string, string> = {
  refinery: 'sàng lọc tin',
  duty_decide: 'trực việc',
  'gen.briefing': 'Bản tin Gen',
  'Sàng lọc tin': 'sàng lọc tin',
  'Trực việc (agent soạn nháp)': 'trực việc',
  'Bản tin Gen': 'Bản tin Gen',
};
const DEFAULT_PURPOSES = ['refinery', 'duty_decide', 'gen.briefing'];

/** Câu đầu thẻ: "sàng lọc tin, trực việc, Bản tin Gen" — theo `purposes` của máy chủ, thiếu thì dùng bộ mặc định. */
function purposesText(p: unknown): string {
  const list = Array.isArray(p) && p.length ? p.filter((x): x is string => typeof x === 'string') : DEFAULT_PURPOSES;
  return list.map((x) => PURPOSE_LABEL[x] ?? x).join(', ');
}

/** 422: lỗi trường `accept_risk` / `allow_cli` (chuỗi); không có thì câu chung (errorText — luôn là chuỗi). */
function fieldError(e: unknown): string {
  if (e instanceof ApiError && e.status === 422) {
    const f = e.fieldErrors;
    const msg = detailToText(f.accept_risk) || detailToText(f.allow_cli);
    if (msg) return msg;
  }
  return errorText(e);
}

/**
 * v0.1.41 (F-86, QD-12): "Nguồn AI cho việc nền" ở Bộ não AI — việc nền (sàng lọc tin, trực việc, Bản tin Gen) chạy bằng
 * khoá API và (v0.1.62) Antigravity CLI. Owner có thể cho Claude Code CLI chạy việc nền: hộp cảnh báo hiện `risk_text`
 * NGUYÊN VĂN từ máy chủ + ô tích xác nhận + PIN `ai.background_cli` (423 ⇒ hộp PIN tự mở qua lib/api.ts rồi gửi lại). Tắt
 * không cần cảnh báo. Antigravity CLI không có công tắc (luôn chạy được). Vai trò khác chỉ đọc.
 */
export function BackgroundSourcesCard() {
  const canRead = useCan('system.read');
  const q = useBackgroundSources(canRead);
  if (!canRead) return null;
  return (
    <Panel title="Nguồn AI cho việc nền" kicker={`Dùng cho ${purposesText(q.data?.purposes)}`} label="Nguồn AI cho việc nền" bodyClass="bg-src">
      {q.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <BackgroundBody data={q.data} />
      )}
    </Panel>
  );
}

function BackgroundBody({ data }: { data: BackgroundSources }) {
  const isOwner = useMe().data?.role?.code === 'owner';
  const canManage = useCan('system.manage');
  const save = useSetBackgroundSources();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const allow = Array.isArray(data.allow_cli) ? data.allow_cli : [];
  const hasClaude = sources.some((s) => s.kind === CLAUDE_CLI);
  const cliOn = allow.includes(CLAUDE_CLI);

  const turnOff = () =>
    save.mutate(
      { allow_cli: [], accept_risk: false },
      { onSuccess: () => toast('Đã tắt Claude Code CLI cho việc nền — việc nền dùng khoá API hoặc Antigravity CLI') },
    );

  return (
    <>
      {sources.length === 0 ? (
        <p className="muted-note">Chưa có nguồn AI nào.</p>
      ) : (
        <ol className="bg-src__list">
          {sources.map((s, i) => (
            <li className="bg-src__row" key={s.provider_id} data-testid={`bg-src-${s.provider_id}`}>
              <span className="mono bg-src__rank">{String(i + 1).padStart(2, '0')}</span>
              <div className="bg-src__body">
                <div className="bg-src__name">{s.name}</div>
                {s.reason ? <div className="bg-src__reason">{detailToText(s.reason)}</div> : null}
              </div>
              <span className="bg-src__pill" data-used={s.used ? 'yes' : 'no'}>
                {s.used ? 'Dùng cho việc nền' : 'Không dùng'}
              </span>
            </li>
          ))}
        </ol>
      )}

      {!data.has_api_source && !data.has_agy_source ? (
        <div className="risk-box bg-src__nokey" role="note" data-testid="bg-src-no-key" data-cli={cliOn ? 'on' : 'off'}>
          <Icon name="ph ph-key" size={16} color="var(--color-warn)" />
          <div className="risk-box__text">
            {cliOn ? (
              // Owner đã cho Claude Code CLI chạy việc nền ⇒ việc nền VẪN chạy; chỉ khuyên thêm khoá API hoặc Antigravity CLI.
              <div className="risk-box__title">Việc nền đang chạy bằng Claude Code CLI — nên dán khoá API OpenRouter/Gemini để an toàn hơn</div>
            ) : (
              <>
                <div className="risk-box__title">Chưa có nguồn AI cho việc nền — dán khoá OpenRouter/Gemini hoặc đăng nhập Antigravity CLI</div>
                Bản tin Gen vẫn gửi đúng giờ phần không cần model; sàng lọc tin cần một nguồn AI (khoá API hoặc Antigravity CLI), trực việc cần khoá API.
              </>
            )}
            {canManage ? (
              <div className="bg-src__cta">
                <Link to="/api?add=openrouter" className="gh-btn gh-btn--secondary btn-27">
                  <Icon name="ph ph-plus" size={13} />
                  Thêm nhà cung cấp
                </Link>
              </div>
            ) : (
              <div className="bg-src__cta">Báo Owner thêm khoá API ở API &amp; Model.</div>
            )}
          </div>
        </div>
      ) : null}

      {hasClaude ? (
        <div className="bg-src__cli">
          <div className="triage-row">
            <Switch
              checked={cliOn}
              label="Cho Claude Code CLI chạy việc nền"
              disabled={!isOwner || save.isPending}
              onChange={(v) => {
                save.reset();
                if (v) setConfirmOpen(true);
                else turnOff();
              }}
            />
            <span>Cho Claude Code CLI chạy việc nền</span>
            {isOwner ? <PinHint /> : null}
          </div>
          {cliOn ? <p className="muted-note">Sếp đã chấp nhận rủi ro — có thể tắt bất cứ lúc nào.</p> : null}
          {!isOwner ? <p className="muted-note">Chỉ Sếp (Owner) thay đổi được nguồn AI cho việc nền.</p> : null}
          {save.isError && !confirmOpen ? <InlineError detail={errorDetail(save.error)}>{fieldError(save.error)}</InlineError> : null}
        </div>
      ) : null}
      <p className="muted-note" data-testid="bg-src-agy-note">
        {AGY_BACKGROUND_NOTE}
      </p>

      {confirmOpen ? <ConfirmCliDialog riskText={data.risk_text} onClose={() => setConfirmOpen(false)} save={save} /> : null}
    </>
  );
}

function ConfirmCliDialog({
  riskText,
  onClose,
  save,
}: {
  riskText: string;
  onClose: () => void;
  save: ReturnType<typeof useSetBackgroundSources>;
}) {
  const [ack, setAck] = useState(false);
  const submit = () => {
    if (!ack) return;
    save.mutate(
      { allow_cli: [CLAUDE_CLI], accept_risk: true },
      {
        onSuccess: () => {
          toast('Đã cho Claude Code CLI chạy việc nền');
          onClose();
        },
      },
    );
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={460}
      title="Cho Claude Code CLI chạy việc nền"
      kicker="Đọc kỹ cảnh báo trước khi cho phép"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" disabled={!ack} loading={save.isPending} onClick={submit}>
            Cho phép
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <div className="risk-box" role="note" data-testid="bg-cli-risk">
          <Icon name="ph ph-warning" size={16} color="var(--color-warn)" />
          <div className="risk-box__text bg-src__risk">{typeof riskText === 'string' ? riskText : ''}</div>
        </div>
        <label className="gh-check">
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-autofocus />
          <span>Tôi đã đọc cảnh báo và tự chịu rủi ro</span>
        </label>
        <PinHint />
        {save.isError ? <InlineError detail={errorDetail(save.error)}>{fieldError(save.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}
