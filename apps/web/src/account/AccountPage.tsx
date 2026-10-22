import { useEffect, useState, type FormEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { Account, AccountSession } from '@gen-harness/contracts';
import { Button, Card, Chip, Icon, PinInput, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { fmtAgo, fmtDM, fmtHM } from '../lib/format';
import { qk } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { toast } from '../lib/toast';
import { CardError, SkeletonLines } from '../screens/common';
import { PinHistoryDialog } from '../screens/system/PinCard';
import { useCan } from '../lib/permissions';
import { ScreenTitle } from '../screens/ScreenPage';
import { useFieldErrors } from '../setup/useFieldErrors';
import { roleLine } from '../shell/people';
import {
  accountKey,
  describeDevice,
  emailChanged,
  hasErrors,
  isMobile,
  pinErrors,
  profileErrors,
  serverFieldErrors,
  type PinValues,
  type ProfileValues,
} from './accountModel';
import { PasswordForm } from './PasswordForm';

const refreshMe = () => queryClient.invalidateQueries({ queryKey: qk.me });
const refreshAccount = () => queryClient.invalidateQueries({ queryKey: accountKey });
const fmtWhen = (iso: string | null | undefined) => (iso ? `${fmtHM(iso)} ${fmtDM(iso)}` : '—');

/** "Tài khoản của tôi" (`/account`, v0.1.19): hồ sơ, mật khẩu, PIN (Owner), phiên đăng nhập. */
export function AccountPage() {
  const q = useQuery({ queryKey: accountKey, queryFn: ({ signal }) => api.account.get(signal) });

  useEffect(() => {
    document.title = 'Tài khoản của tôi · Gen-Harness';
  }, []);

  return (
    <div className="screen acct">
      <ScreenTitle
        title="Tài khoản của tôi"
        description="Tên hiển thị, email đăng nhập, mật khẩu, mã PIN và các thiết bị đang đăng nhập. Mọi thay đổi đều được ghi vào Nhật ký hành động."
        maxWidth={640}
      />
      {q.isPending ? (
        <div className="gh-card">
          <SkeletonLines rows={6} />
        </div>
      ) : q.isError ? (
        <div className="gh-card">
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        </div>
      ) : (
        <div className="acct-grid">
          <ProfileCard account={q.data} />
          <Card data-gen-target="account.password" title="Đổi mật khẩu" kicker="Đổi xong, các thiết bị khác tự đăng xuất">
            <PasswordForm
              onDone={async (r) => {
                toast(
                  r.sessions_revoked
                    ? `Đã đổi mật khẩu — đã đăng xuất ${r.sessions_revoked} thiết bị khác.`
                    : 'Đã đổi mật khẩu.',
                );
                await Promise.all([refreshAccount(), refreshMe()]);
              }}
            />
          </Card>
          <PinCard hasPin={q.data.has_pin} />
          <SessionsCard sessions={q.data.sessions} />
        </div>
      )}
    </div>
  );
}

function ProfileCard({ account }: { account: Account }) {
  const [v, setV] = useState<ProfileValues>({ display_name: account.display_name, email: account.email, current_password: '' });
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const changingEmail = emailChanged(v.email, account.email);
  const dirty = v.display_name.trim() !== account.display_name || changingEmail;
  const clientErrors = profileErrors(v, account.email);
  type K = keyof ProfileValues;
  const f = useFieldErrors<K>(clientErrors);

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    f.setSubmitted(true);
    if (hasErrors(clientErrors) || !dirty) return;
    setBusy(true);
    setFormError(null);
    try {
      const next = await api.account.update({
        display_name: v.display_name.trim(),
        ...(changingEmail ? { email: v.email.trim(), current_password: v.current_password } : {}),
      });
      queryClient.setQueryData(accountKey, next);
      await refreshMe();
      setV({ display_name: next.display_name, email: next.email, current_password: '' });
      f.setSubmitted(false);
      toast(changingEmail ? 'Đã lưu hồ sơ — lần sau đăng nhập bằng email mới.' : 'Đã lưu hồ sơ.');
    } catch (err) {
      const fields = serverFieldErrors(err);
      if (fields) f.setServer(fields as Partial<Record<K, string>>);
      else setFormError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-gen-target="account.profile" title="Hồ sơ" kicker={`${roleLine(account)} · tạo ngày ${fmtDM(account.created_at)}`}>
      <form className="acct-form" onSubmit={submit} noValidate aria-label="Hồ sơ">
        <TextField
          label="Tên hiển thị"
          autoComplete="name"
          value={v.display_name}
          onChange={(e) => set('display_name', e.target.value)}
          onBlur={() => f.blur('display_name')}
          error={f.errorOf('display_name')}
          hint="Hiện ở thanh bên và trong Nhật ký hành động."
        />
        <TextField
          label="Email đăng nhập"
          type="email"
          autoComplete="email"
          value={v.email}
          onChange={(e) => set('email', e.target.value)}
          onBlur={() => f.blur('email')}
          error={f.errorOf('email')}
        />
        {changingEmail ? (
          <TextField
            label="Mật khẩu hiện tại (để xác nhận đổi email)"
            revealable
            autoComplete="current-password"
            value={v.current_password}
            onChange={(e) => set('current_password', e.target.value)}
            onBlur={() => f.blur('current_password')}
            error={f.errorOf('current_password')}
          />
        ) : null}
        <div className="acct-form__error" role="alert">
          {formError}
        </div>
        <div className="acct-form__actions">
          <Button variant="primary" type="submit" loading={busy} disabled={!dirty && !busy} icon="ph ph-floppy-disk">
            Lưu hồ sơ
          </Button>
        </div>
      </form>
    </Card>
  );
}

const EMPTY_PIN: PinValues = { current_password: '', new_pin: '', new_pin_confirm: '' };

/**
 * Mã PIN riêng của từng người. v0.1.61 (F071/F097): người chưa có PIN (Quản lý được mời…) ĐẶT PIN LẦN ĐẦU ở đây bằng mật
 * khẩu đăng nhập — trước đây thẻ bị ẩn và máy chủ trả 409 nên không ai ngoài Owner duyệt được.
 */
function PinCard({ hasPin }: { hasPin: boolean }) {
  // v0.1.42 (F-61): "Lịch sử nhập PIN" chuyển về đây (trước ở thẻ PIN của Điều khiển hệ thống).
  const canAudit = useCan('audit.read');
  // PIN chỉ cần cho thao tác ghi có PIN (duyệt bản nháp, đổi quyền/hệ thống). Vai chỉ xem (Kiểm soát) chưa cần — nói đúng thực tế.
  const canApprove = useCan('action.approve');
  const canManageSystem = useCan('system.manage');
  const canManageRoles = useCan('roles.manage');
  const needsPin = canApprove || canManageSystem || canManageRoles;
  const [historyOpen, setHistoryOpen] = useState(false);
  const [v, setV] = useState<PinValues>(EMPTY_PIN);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const clientErrors = pinErrors(v);
  type K = keyof PinValues;
  const f = useFieldErrors<K>(clientErrors);

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    f.setSubmitted(true);
    if (hasErrors(clientErrors)) return;
    setBusy(true);
    setFormError(null);
    try {
      await api.account.changePin(v);
      setV(EMPTY_PIN);
      f.setSubmitted(false);
      toast(hasPin ? 'Đã đổi mã PIN.' : 'Đã đặt mã PIN — dùng được ngay cho thao tác cần PIN.');
      if (!hasPin) await refreshAccount();
    } catch (err) {
      const fields = serverFieldErrors(err);
      if (fields) f.setServer(fields as Partial<Record<K, string>>);
      else setFormError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      data-gen-target="account.pin"
      title={hasPin ? 'Đổi mã PIN' : 'Đặt mã PIN'}
      kicker={
        hasPin
          ? 'PIN 6 số dùng cho thao tác nhạy cảm'
          : needsPin
            ? 'Bạn chưa có mã PIN — cần để duyệt bản nháp và thao tác nhạy cảm'
            : 'Bạn chưa có mã PIN — vai trò hiện tại chưa có thao tác nào cần PIN, đặt sẵn để dùng khi được cấp thêm quyền'
      }
    >
      <form className="acct-form" onSubmit={submit} noValidate aria-label={hasPin ? 'Đổi mã PIN' : 'Đặt mã PIN'}>
        <TextField
          label="Mật khẩu hiện tại"
          revealable
          autoComplete="current-password"
          value={v.current_password}
          onChange={(e) => set('current_password', e.target.value)}
          onBlur={() => f.blur('current_password')}
          error={f.errorOf('current_password')}
          hint={hasPin ? 'Quên PIN cũ vẫn đổi được — chỉ cần mật khẩu đăng nhập.' : 'Đặt lần đầu chỉ cần mật khẩu đăng nhập của bạn.'}
        />
        <PinField id="acct-pin" label="Mã PIN mới" value={v.new_pin} onChange={(x) => set('new_pin', x)}
          onBlur={() => f.blur('new_pin')} error={f.errorOf('new_pin')} />
        <PinField id="acct-pin-confirm" label="Nhập lại mã PIN mới" value={v.new_pin_confirm}
          onChange={(x) => set('new_pin_confirm', x)} onBlur={() => f.blur('new_pin_confirm')}
          error={f.errorOf('new_pin_confirm')} />
        <div className="acct-form__error" role="alert">
          {formError}
        </div>
        <div className="acct-form__actions">
          <Button variant="primary" type="submit" loading={busy} icon="ph ph-password">
            {hasPin ? 'Đổi mã PIN' : 'Đặt mã PIN'}
          </Button>
          {canAudit ? (
            <Button variant="secondary" icon="ph ph-clock-counter-clockwise" onClick={() => setHistoryOpen(true)}>
              Lịch sử nhập mã PIN (cả tổ chức)
            </Button>
          ) : null}
        </div>
      </form>
      {canAudit ? <PinHistoryDialog open={historyOpen} onClose={() => setHistoryOpen(false)} /> : null}
    </Card>
  );
}

function PinField({ id, label, value, onChange, onBlur, error }: {
  id: string; label: string; value: string; onChange: (v: string) => void; onBlur: () => void; error: string | null;
}) {
  return (
    <div className="gh-field">
      <label className="gh-field__label" htmlFor={`${id}-0`}>
        {label}
      </label>
      <PinInput idPrefix={id} label={label} value={value} onChange={onChange} onBlur={onBlur} invalid={!!error}
        describedBy={error ? `${id}-error` : undefined} />
      {error ? (
        <div className="gh-field__error" id={`${id}-error`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}

function SessionsCard({ sessions }: { sessions: AccountSession[] }) {
  const others = sessions.filter((s) => !s.current).length;
  const revokeOthers = useMutation({
    mutationFn: () => api.account.revokeOtherSessions(),
    onSuccess: async (r) => {
      toast(r.sessions_revoked ? `Đã đăng xuất ${r.sessions_revoked} thiết bị khác.` : 'Không còn thiết bị nào khác.');
      await refreshAccount();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });

  return (
    <Card
      className="acct-sessions"
      data-gen-target="account.sessions"
      title="Phiên đăng nhập"
      kicker={`${sessions.length} thiết bị đang đăng nhập`}
      actions={
        <Button
          size="sm"
          icon="ph ph-sign-out"
          loading={revokeOthers.isPending}
          disabled={others === 0 && !revokeOthers.isPending}
          onClick={() => revokeOthers.mutate()}
        >
          Đăng xuất các thiết bị khác
        </Button>
      }
      padded={false}
    >
      <ul className="acct-session-list">
        {sessions.map((s) => (
          <SessionRow key={s.id} s={s} />
        ))}
      </ul>
    </Card>
  );
}

function SessionRow({ s }: { s: AccountSession }) {
  const revoke = useMutation({
    mutationFn: () => api.account.revokeSession(s.id),
    onSuccess: async () => {
      toast(`Đã đăng xuất ${describeDevice(s.user_agent)}.`);
      await refreshAccount();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  return (
    <li className="acct-session" data-current={s.current || undefined}>
      <span className="acct-session__icon" aria-hidden>
        <Icon name={isMobile(s.user_agent) ? 'ph ph-device-mobile' : 'ph ph-browser'} size={16} />
      </span>
      <div className="acct-session__main">
        <div className="acct-session__title">
          {describeDevice(s.user_agent)}
          {s.current ? (
            <Chip tone="ok" dot>
              Thiết bị này
            </Chip>
          ) : null}
        </div>
        <div className="acct-session__meta">
          {s.ip ?? 'IP không rõ'} · đăng nhập {fmtWhen(s.created_at)} · hoạt động {fmtAgo(s.last_seen_at ?? s.created_at)}
        </div>
      </div>
      {s.current ? null : (
        <Button size="sm" variant="ghost" loading={revoke.isPending} onClick={() => revoke.mutate()}
          aria-label={`Đăng xuất ${describeDevice(s.user_agent)} (${s.ip ?? 'IP không rõ'})`}>
          Đăng xuất
        </Button>
      )}
    </li>
  );
}
