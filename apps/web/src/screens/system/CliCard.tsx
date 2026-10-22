import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, type CliKind, type CliProfile } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { cliProfilesKey, qk2, useCliProfiles } from '../../lib/dataQueries';
import { emailInitials } from '../../lib/format';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { useNow } from '../../lib/useNow';
import { errorText } from '../../lib/errorText';
import { CardError, InlineError, PinHint, SkeletonLines, StateChip } from '../common';
import { ConnectionStatusPill } from '../connections/ConnectionStatusPill';
import { cliConnStatus } from '../connections/connectionsModel';
import {
  CLAUDE_CONSUMER_TERMS_URL,
  CLAUDE_TERMS_URL,
  CLI_ADD_PIN_TEXT,
  CLI_LOGIN_TEXT,
  CLI_TEXT,
  cliAccountLabel,
  cliChip,
  cliMeta,
  cliSwitchError,
  credTone,
} from './systemModel';
import { useCliLogin, type CliLogin } from './useCliLogin';

export function CliLoginPanel({ login }: { login: CliLogin }) {
  const [code, setCode] = useState('');
  const { event, status } = login;
  const txt = CLI_TEXT[login.kind];
  useEffect(() => {
    if (status === 'waiting_code') setCode('');
  }, [status]);
  if (!login.active && !login.start.isError) return null;
  const submitErr = login.submit.error;
  const notWaiting = submitErr instanceof ApiError && submitErr.code === 'CLI_LOGIN_NOT_WAITING';
  return (
    <div className="cli-login" aria-live="polite" data-testid="cli-login">
      {login.start.isError ? (
        <InlineError>{errorText(login.start.error)}</InlineError>
      ) : (
        <div className="cli-login__status">
          {status === 'starting' || status === 'verifying' ? (
            <Icon name="ph ph-circle-notch" size={14} className="spin" />
          ) : status === 'failed' ? (
            <Icon name="ph ph-x-circle" size={14} color="var(--color-bad)" />
          ) : (
            <Icon name="ph ph-key" size={14} color="var(--color-accent-300)" />
          )}
          <span>{status === 'failed' && event?.message ? event.message : status ? CLI_LOGIN_TEXT[status] : ''}</span>
        </div>
      )}
      {status === 'waiting_code' && event?.url ? (
        <>
          <div className="cli-login__links">
            <a className="cli-login__url" href={event.url} target="_blank" rel="noopener noreferrer">
              <Icon name="ph ph-arrow-square-out" size={13} />
              {txt.openLink}
            </a>
            <Button
              variant="ghost"
              className="btn-27"
              icon="ph ph-copy"
              onClick={() => {
                const url = event.url ?? '';
                void navigator.clipboard?.writeText(url).then(
                  () => toast(`Đã chép link — dán vào trình duyệt đang đăng nhập ${txt.account}.`, 'neutral'),
                  () => toast(`Không chép được — bấm giữ link “${txt.openLink}” để chép.`, 'warn'),
                );
              }}
            >
              Chép link
            </Button>
          </div>
          <p className="cli-login__hint">{txt.hint}</p>
          {/* Not a <form>: this panel also sits inside the setup wizard's form (no nested forms). */}
          <div
            className="cli-login__row"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') {
                e.preventDefault();
                e.stopPropagation();
                if (code.trim()) login.submit.mutate(code.trim());
              }
            }}
          >
            <TextField
              label="Mã xác thực"
              value={code}
              autoComplete="one-time-code"
              spellCheck={false}
              onChange={(e) => setCode(e.target.value)}
              placeholder="Dán mã từ trang đăng nhập"
            />
            <Button variant="primary" disabled={!code.trim()} loading={login.submit.isPending} onClick={() => login.submit.mutate(code.trim())}>
              Xác nhận
            </Button>
          </div>
        </>
      ) : null}
      {submitErr ? (
        <InlineError>{notWaiting ? 'Phiên đăng nhập không còn chờ mã — bắt đầu lại.' : errorText(submitErr)}</InlineError>
      ) : null}
      <div className="dlg-row">
        {status === 'failed' || login.start.isError ? (
          <Button variant="secondary" className="btn-27" icon="ph ph-arrow-clockwise" onClick={() => login.start.mutate()}>
            Thử lại
          </Button>
        ) : null}
        {!login.finished ? (
          <Button variant="ghost" className="btn-27" loading={login.cancel.isPending} onClick={() => login.cancel.mutate()}>
            Huỷ đăng nhập
          </Button>
        ) : (
          <Button variant="ghost" className="btn-27" onClick={login.clear}>
            Đóng
          </Button>
        )}
      </div>
    </div>
  );
}

/**
 * v0.1.31 — QD-12 (Owner tự quyết): dùng gói Claude cá nhân qua một ứng dụng tự động có thể bị điều khoản của Anthropic
 * hạn chế. Hiện rõ trước khi đăng nhập, kèm link điều khoản chính thức.
 */
export function ClaudeRiskNotice() {
  return (
    <div className="risk-box" role="note" data-testid="claude-risk">
      <Icon name="ph ph-warning" size={16} color="var(--color-warn)" />
      <div className="risk-box__text">
        <strong>Sếp tự quyết rủi ro:</strong> Anthropic quy định đăng nhập gói Claude (Free/Pro/Max) dành cho cá nhân dùng
        Claude Code thông thường; dùng qua một ứng dụng tự động như Gen-Harness có thể bị hạn chế hoặc khoá tài khoản. Muốn an
        toàn, dùng khoá API (Claude Console) thay thế. Xem{' '}
        <a href={CLAUDE_TERMS_URL} target="_blank" rel="noopener noreferrer">
          điều khoản Claude Code
        </a>{' '}
        và{' '}
        <a href={CLAUDE_CONSUMER_TERMS_URL} target="_blank" rel="noopener noreferrer">
          điều khoản người dùng
        </a>
        . Mặc định TẮT — chỉ chạy sau khi Sếp đăng nhập.
      </div>
    </div>
  );
}

/**
 * Tài khoản CLI (Antigravity hoặc Claude Code) + "Khoá & phiên" rows (design CLI card + `creds`). Chỉ render ở Kết nối
 * (v0.1.42, F-61): viên trạng thái chung (ConnectionStatusPill) + đúng một nút chính (`data-main-action`); trạng thái chi
 * tiết (Đang hoạt động / Sắp hết hạn / Hết hạn) nằm ở dòng meta.
 */
export function CliCard({ canManage, showCredentials = true, kind = 'antigravity_cli' }: { canManage: boolean; showCredentials?: boolean; kind?: CliKind }) {
  const profiles = useCliProfiles(kind);
  const txt = CLI_TEXT[kind];
  const creds = useQuery({
    queryKey: qk2.credentials,
    queryFn: ({ signal }) => api.providers.credentials(signal),
    enabled: showCredentials,
  });
  const now = useNow(60_000);
  const login = useCliLogin(kind);
  const [switchOpen, setSwitchOpen] = useState(false);
  const list = profiles.data ?? [];
  const active = list.find((p) => p.active);
  const others = list.filter((p) => !p.active).length;
  // Nút chính gọi login.start: "Đăng nhập lại" (phiên hết hạn) hoặc chưa có tài khoản nào — chỉ khi đó nhắc cần PIN
  // ("Đổi tài khoản" mở hộp thoại tự nói cần PIN).
  const loginIsMain = canManage && active?.state === 'expired' && !login.active;
  const chip = cliChip(active);
  const startLogin = () => {
    setSwitchOpen(false);
    login.start.mutate();
  };

  return (
    <section className="gh-card" aria-label={txt.title} data-testid={`cli-card-${kind}`}>
      <div className="gh-card__header">
        <div style={{ minWidth: 0 }}>
          <div className="gh-card__title">{txt.title}</div>
          <div className="gh-card__kicker">{txt.kicker}</div>
          {txt.scope ? (
            // v0.1.62 → gói G: phạm vi dùng theo chỗ chạy (core trong hệ thống, ngoại giao / người ngoài qua hộp riêng) + rủi ro còn lại của Antigravity CLI.
            <div className="gh-card__kicker" role="note" data-testid="cli-scope" style={{ color: 'var(--color-warn)', display: 'flex', gap: 6, alignItems: 'flex-start' }}>
              <Icon name="ph ph-shield-warning" size={13} color="var(--color-warn)" />
              <span>{txt.scope}</span>
            </div>
          ) : null}
        </div>
        {profiles.data ? <ConnectionStatusPill status={cliConnStatus(active)} /> : null}
      </div>
      <div className="cli-body">
        {profiles.isPending ? (
          <SkeletonLines rows={2} padding="0" />
        ) : profiles.isError ? (
          <CardError error={profiles.error} onRetry={() => void profiles.refetch()} retrying={profiles.isFetching} />
        ) : (
          <>
          {kind === 'claude_code_cli' ? <ClaudeRiskNotice /> : null}
          <div className="cli-acct" data-testid="cli-current">
            <div className={active ? 'cli-avatar' : 'cli-avatar cli-avatar--empty'} aria-hidden>
              {active ? emailInitials(active.email) : <Icon name="ph ph-user" size={15} />}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="cli-email" title={active ? cliAccountLabel(active) : `Chưa chọn tài khoản ${txt.account}`}>
                {active ? cliAccountLabel(active) : `Chưa chọn tài khoản ${txt.account}`}
              </div>
              <div className="cli-meta">
                {active
                  ? `${chip.label} · ${cliMeta(active, now)}${others ? ` · ${others} tài khoản khác đã lưu` : ''}`
                  : list.length
                    ? `${list.length} tài khoản đã lưu — chọn một tài khoản để AI dùng`
                    : txt.empty}
              </div>
            </div>
            {loginIsMain ? (
              <Button variant="primary" className="btn-28" icon="ph ph-sign-in" onClick={() => login.start.mutate()} loading={login.start.isPending} data-main-action>
                Đăng nhập lại
              </Button>
            ) : canManage && list.length ? (
              <Button variant={active ? 'secondary' : 'primary'} className="btn-28" icon="ph ph-user-switch" onClick={() => setSwitchOpen(true)} data-main-action>
                {active ? 'Đổi tài khoản' : 'Chọn tài khoản'}
              </Button>
            ) : canManage && !login.active ? (
              <Button variant="primary" className="btn-28" icon="ph ph-sign-in" onClick={() => login.start.mutate()} loading={login.start.isPending} data-main-action>
                {txt.login}
              </Button>
            ) : null}
          </div>
          {loginIsMain || (canManage && list.length === 0 && !login.active) ? (
            // v0.1.45 (F-20): bắt đầu đăng nhập (thêm tài khoản) cần phiên PIN `cli.switch_account` — hộp PIN tự mở khi gặp 423.
            // Chỉ nhắc khi nút chính đang là nút đăng nhập; 'Đổi tài khoản' mở hộp thoại tự nói cần PIN.
            <PinHint text={CLI_ADD_PIN_TEXT} title={CLI_ADD_PIN_TEXT} />
          ) : null}
          </>
        )}
        <CliLoginPanel login={login} />
        {showCredentials ? (
          <>
            <div className="cli-rule" />
            {creds.isPending ? (
              <SkeletonLines rows={3} padding="0" />
            ) : creds.isError ? (
              <CardError error={creds.error} onRetry={() => void creds.refetch()} retrying={creds.isFetching} />
            ) : creds.data.length === 0 ? (
              <p className="muted-note">Chưa có khoá API hay phiên kênh nào.</p>
            ) : (
              creds.data.map((cr, i) => {
                const tone = credTone(cr.state);
                return (
                  <div className="cred" key={`${cr.name}-${i}`}>
                    <Icon name={cr.icon} size={14} color={tone} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="cred__name">{cr.name}</div>
                      <div className="cred__meta">{cr.meta}</div>
                    </div>
                    <StateChip color={tone}>{cr.state_label}</StateChip>
                  </div>
                );
              })
            )}
          </>
        ) : null}
      </div>
      <ProfilesDialog kind={kind} open={switchOpen} onClose={() => setSwitchOpen(false)} profiles={list} loginBusy={login.active && !login.finished} onAdd={startLogin} />
    </section>
  );
}

/**
 * Danh sách tài khoản Google của CLI: tài khoản đang dùng, "Dùng tài khoản này", xoá, "Thêm tài khoản Google".
 * Đổi/xoá cần PIN: API trả 423 → PinDialogHost (toàn cục) hiện hộp PIN, nhập đúng thì yêu cầu tự gửi lại.
 */
function ProfilesDialog({
  kind,
  open,
  onClose,
  profiles,
  loginBusy,
  onAdd,
}: {
  kind: CliKind;
  open: boolean;
  onClose: () => void;
  profiles: CliProfile[];
  loginBusy: boolean;
  onAdd: () => void;
}) {
  const now = useNow(60_000, open);
  const txt = CLI_TEXT[kind];
  const key = cliProfilesKey(kind);
  const [confirmDelete, setConfirmDelete] = useState<CliProfile | null>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: qk2.cliProfiles });
    void queryClient.invalidateQueries({ queryKey: qk2.providers });
    void queryClient.invalidateQueries({ queryKey: qk2.credentials });
  };
  const activate = useMutation({
    mutationFn: (id: string) => api.cli.activate(id),
    onSuccess: (p) => {
      queryClient.setQueryData<CliProfile[]>(key, (old) => old?.map((x) => ({ ...x, active: x.id === p.id })));
      refresh();
      toast(`Đã chuyển sang ${cliAccountLabel(p)} — từ lượt tiếp theo AI dùng tài khoản này.`);
      onClose();
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 404) refresh();
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.cli.remove(id),
    onSuccess: (_v, id) => {
      const gone = profiles.find((x) => x.id === id);
      queryClient.setQueryData<CliProfile[]>(key, (old) => old?.filter((x) => x.id !== id));
      refresh();
      toast(`Đã xoá ${cliAccountLabel(gone)} khỏi danh sách`, 'neutral');
    },
    onError: (e) => {
      if (e instanceof ApiError && e.status === 404) refresh();
    },
  });
  useEffect(() => {
    if (!open) {
      activate.reset();
      remove.reset();
      setConfirmDelete(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset only when the dialog closes
  }, [open]);
  const err = activate.error ?? remove.error;
  const busy = activate.isPending || remove.isPending;
  const active = profiles.find((p) => p.active);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={480}
      title={`Đổi tài khoản ${txt.account} cho AI`}
      kicker="Thêm, đổi hoặc xoá tài khoản cần mã PIN"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
          <Button variant="primary" icon="ph ph-plus" disabled={loginBusy} onClick={onAdd}>
            {txt.add}
          </Button>
        </>
      }
    >
      <p className="gh-dialog__text" data-testid="cli-dialog-current">
        {active ? (
          <>
            AI đang dùng <strong>{cliAccountLabel(active)}</strong>. Chọn tài khoản khác để chuyển; lượt gọi AI kế tiếp dùng tài khoản mới.
          </>
        ) : (
          `Chưa chọn tài khoản nào — chọn một tài khoản bên dưới hoặc thêm tài khoản ${txt.account} mới.`
        )}
      </p>
      {loginBusy ? <p className="gh-dialog__status gh-dialog__status--warn">Đang đăng nhập thêm một tài khoản — hoàn tất hoặc huỷ bước đó trước khi đổi.</p> : null}
      {profiles.length === 0 ? (
        <EmptyState icon="ph ph-user" title={`Chưa có tài khoản ${txt.account} nào`} />
      ) : (
        <div role="list" aria-label={`Tài khoản ${txt.account} đã lưu`}>
          {profiles.map((p) => {
            const label = cliAccountLabel(p);
            return (
              <div className="profile-row" role="listitem" key={p.id} aria-current={p.active ? 'true' : undefined}>
                <div className="cli-avatar" aria-hidden>
                  {emailInitials(p.email)}
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="cli-email" title={label}>{label}</div>
                  <div className="cli-meta">{cliMeta(p, now)}</div>
                </div>
                {p.active ? (
                  <StateChip color="var(--color-ok)" dot>
                    Đang dùng
                  </StateChip>
                ) : confirmDelete?.id === p.id ? (
                  <>
                    <span className="cli-meta" style={{ marginTop: 0 }}>
                      Xoá tài khoản này?
                    </span>
                    <Button variant="secondary" className="btn-27" onClick={() => setConfirmDelete(null)}>
                      Huỷ
                    </Button>
                    <Button
                      variant="primary"
                      className="btn-27"
                      icon="ph ph-trash"
                      loading={remove.isPending}
                      onClick={() => {
                        setConfirmDelete(null);
                        remove.mutate(p.id);
                      }}
                    >
                      Xoá
                    </Button>
                  </>
                ) : (
                  <>
                    <Button
                      variant="secondary"
                      className="btn-27"
                      aria-label={`Dùng tài khoản ${label}`}
                      disabled={loginBusy || (busy && activate.variables !== p.id)}
                      loading={activate.isPending && activate.variables === p.id}
                      onClick={() => {
                        activate.reset();
                        remove.reset();
                        activate.mutate(p.id);
                      }}
                    >
                      Dùng tài khoản này
                    </Button>
                    <Button
                      variant="ghost"
                      className="btn-27"
                      icon="ph ph-trash"
                      aria-label={`Xoá tài khoản ${label}`}
                      disabled={loginBusy || busy}
                      onClick={() => setConfirmDelete(p)}
                    />
                  </>
                )}
              </div>
            );
          })}
        </div>
      )}
      {err ? <InlineError>{cliSwitchError(err, errorText)}</InlineError> : null}
    </Dialog>
  );
}
