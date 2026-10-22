import type { CliKind } from '@gen-harness/contracts';
import { Button, Icon } from '@gen-harness/ui';
import { useCliProfiles } from '../lib/dataQueries';
import { emailInitials } from '../lib/format';
import { useNow } from '../lib/useNow';
import { CardError, PinHint, SkeletonLines, StateChip } from '../screens/common';
import { ClaudeRiskNotice, CliLoginPanel as CliLoginFlow } from '../screens/system/CliCard';
import { useCliLogin } from '../screens/system/useCliLogin';
import { CLI_ADD_PIN_TEXT, CLI_TEXT, cliAccountLabel, cliChip, cliMeta } from '../screens/system/systemModel';

const N8 = 'var(--color-neutral-800)';
const N4 = 'var(--color-neutral-400)';

/**
 * Đăng nhập tài khoản CLI cho AI: Antigravity (Google) và Claude Code (gói Claude, tuỳ chọn — QD-12 Owner tự quyết).
 * Hết hạn thật → nút "Đăng nhập lại"; token ngắn hạn quá giờ mà CLI tự gia hạn → "Đang hoạt động".
 *
 * v0.1.59 (g5-thiet-lap): TÁCH từ `Step4Brain` (trước là `CliAccountSection`) để dùng chung với màn "Nguồn AI" của Thiết lập một
 * màn (`one/StepAiSource`). HÀNH VI GIỮ NGUYÊN — `/guide/4` vẫn vẽ Step4Brain đầy đủ. `bare`: bỏ khung + tiêu đề phần (nơi dùng
 * đã có tiêu đề riêng cho lựa chọn). Ba bản sao ở BossChecksPage chưa dọn (Nợ trong docs/ROADMAP.md).
 */
export function CliLoginPanel({ kind, bare = false }: { kind: CliKind; bare?: boolean }) {
  const profiles = useCliProfiles(kind);
  const login = useCliLogin(kind);
  const now = useNow(60_000);
  const txt = CLI_TEXT[kind];
  const active = profiles.data?.find((p) => p.active);
  const chip = cliChip(active);
  const claude = kind === 'claude_code_cli';
  return (
    <div className={bare ? 'setup-cli setup-cli--bare' : 'setup-section'} data-testid={`setup-cli-${kind}`}>
      {bare ? null : (
        <div className="setup-section__title">{claude ? 'Claude Code CLI · gói Claude (tuỳ chọn)' : 'Tài khoản Google · Antigravity CLI'}</div>
      )}
      {claude && !active ? <ClaudeRiskNotice /> : null}
      {profiles.isPending ? (
        <SkeletonLines rows={1} padding="0" />
      ) : profiles.isError ? (
        <CardError error={profiles.error} onRetry={() => void profiles.refetch()} retrying={profiles.isFetching} />
      ) : (
        <div className="setup-row">
          <div className={active ? 'cli-avatar' : 'cli-avatar cli-avatar--empty'} aria-hidden>
            {active ? emailInitials(active.email) : '—'}
          </div>
          <div className="setup-row__main">
            <div className="setup-row__title" title={active ? cliAccountLabel(active) : undefined}>{active ? cliAccountLabel(active) : claude ? 'Chưa bật' : 'Chưa đăng nhập'}</div>
            <div className="cli-meta">
              {active ? cliMeta(active, now) : claude ? txt.empty : 'Đăng nhập Google để hệ thống dùng AI qua tài khoản của Sếp.'}
            </div>
          </div>
          {/* Chưa có tài khoản thì tiêu đề dòng đã nói trạng thái (Chưa đăng nhập / Chưa bật) — không vẽ thêm pill cùng chữ. */}
          {active ? (
            <StateChip color={chip.tone} border={chip.tone === N4 ? N8 : chip.tone} dot size="md">
              {chip.label}
            </StateChip>
          ) : null}
          {!login.active ? (
            <Button
              variant={active && active.state !== 'expired' ? 'secondary' : 'primary'}
              className="btn-28"
              icon="ph ph-user-switch"
              onClick={() => login.start.mutate()}
            >
              {active?.state === 'expired' ? 'Đăng nhập lại' : active ? txt.add : txt.login}
            </Button>
          ) : null}
          {/* v0.1.45 (F-20): POST /cli/login cần phiên PIN — báo trước, hộp PIN tự mở khi gặp 423. */}
          {!login.active ? <PinHint text={CLI_ADD_PIN_TEXT} title={CLI_ADD_PIN_TEXT} /> : null}
        </div>
      )}
      <CliLoginFlow login={login} />
      {txt.scope ? (
        <div className="risk-box" role="note" data-testid={`setup-cli-scope-${kind}`}>
          <Icon name="ph ph-shield-warning" size={16} color="var(--color-warn)" />
          <div className="risk-box__text">
            {txt.scope}
          </div>
        </div>
      ) : null}
    </div>
  );
}
