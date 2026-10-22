import { useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { InviteRole, ManagedUser, TempPasswordResult, UsersPage } from '@gen-harness/contracts';
import { Button, Chip, Dialog, EmptyState, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { fmtAgo } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { serverFieldErrors } from '../../account/accountModel';
import { validateEmail, validateRequired } from '../../setup/validation';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { TempPasswordDialog } from './TempPasswordDialog';
import { useAccess } from './queries';
import { roleLabel } from './systemModel';
import { INVITE_ROLES, USERS_KEY, userStatus } from './usersModel';

type Pending = { kind: 'deactivate' | 'reset'; user: ManagedUser };

/** Đội ngũ › Người dùng (v0.1.42; trước ở Điều khiển hệ thống, v0.1.22, Đợt B1): mời, đổi vai trò, khoá/mở khoá, đặt lại mật khẩu. */
export function UsersTab() {
  const canManage = useCan('roles.manage');
  if (!canManage) {
    return (
      <div className="gh-card">
        <EmptyState
          icon="ph ph-lock-simple"
          title="Chỉ Owner quản lý người dùng"
          description="Mời người mới, đổi vai trò và khoá tài khoản cần quyền quản lý vai trò (mặc định chỉ Owner)."
        />
      </div>
    );
  }
  return <UsersPanel />;
}

function UsersPanel() {
  const q = useQuery({ queryKey: USERS_KEY, queryFn: ({ signal }) => api.users.list(signal) });
  const [inviting, setInviting] = useState(false);
  const [pending, setPending] = useState<Pending | null>(null);
  const [secret, setSecret] = useState<{ title: string; result: TempPasswordResult } | null>(null);
  // v0.1.46 (F-21): đọc sẵn địa chỉ đăng nhập khi mở tab để hộp mời có ngay (không nháy "chưa đọc được").
  useAccess();

  return (
    <div className="sys-tabs-col">
      <Panel
        title="Người dùng"
        kicker="Mời, đổi vai trò, khoá tài khoản · thao tác cần mã PIN và ghi Nhật ký hành động"
        label="Người dùng"
        genTarget="system.users.list"
        bodyClass="retention-wrap"
        aside={
          <Button variant="primary" className="btn-27" icon="ph ph-user-plus" onClick={() => setInviting(true)} data-gen-target="system.users.invite">
            Mời người dùng
          </Button>
        }
      >
        {q.isPending ? (
          <SkeletonLines rows={4} padding="10px 16px" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : (
          <table className="retention-table users-table">
            <thead>
              <tr>
                <th>Người dùng</th>
                <th>Vai trò</th>
                <th>Trạng thái</th>
                <th>Đăng nhập gần nhất</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((u) => (
                <UserRow key={u.id} u={u} onAsk={(kind) => setPending({ kind, user: u })} />
              ))}
            </tbody>
          </table>
        )}
      </Panel>
      <p className="muted-note">
        Hệ thống chưa tự gửi email mời: mật khẩu tạm hiện một lần sau khi mời hoặc đặt lại — Sếp tự gửi qua kênh riêng; người đó phải đổi mật khẩu ở lần đăng nhập đầu. Owner chỉ tạo ở trình
        thiết lập; tổ chức luôn giữ ít nhất một Owner.
      </p>

      {inviting ? (
        <InviteDialog
          onClose={() => setInviting(false)}
          onInvited={(r) => {
            setInviting(false);
            setSecret({ title: `Đã mời ${r.user.display_name}`, result: r });
          }}
        />
      ) : null}
      {pending ? <ConfirmDialog pending={pending} onClose={() => setPending(null)} onSecret={setSecret} /> : null}
      {secret ? <TempPasswordDialog title={secret.title} result={secret.result} onClose={() => setSecret(null)} /> : null}
    </div>
  );
}

function useUpdateRow() {
  const qc = useQueryClient();
  return (u: ManagedUser) => qc.setQueryData<UsersPage>(USERS_KEY, (old) => (old ? { ...old, items: old.items.map((x) => (x.id === u.id ? u : x)) } : old));
}

function UserRow({ u, onAsk }: { u: ManagedUser; onAsk: (k: Pending['kind']) => void }) {
  const update = useUpdateRow();
  const role = useMutation({
    mutationFn: (r: InviteRole) => api.users.changeRole(u.id, r),
    onSuccess: (x) => {
      update(x);
      toast(`Đã đổi vai trò ${x.display_name} thành ${roleLabel(x.role.code, x.role.name)}.`);
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const reactivate = useMutation({
    mutationFn: () => api.users.reactivate(u.id),
    onSuccess: (x) => {
      update(x);
      toast(`Đã mở khoá ${x.display_name}.`);
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const st = userStatus(u);
  const isOwner = u.role.code === 'owner';
  return (
    <tr data-inactive={u.status === 'inactive' || undefined}>
      <td>
        <div className="retention-table__name">
          {u.display_name}
          {u.is_self ? <span className="users-table__self"> {isOwner ? '(Sếp)' : '(bạn)'}</span> : null}
        </div>
        <div className="retention-table__code">{u.email}</div>
      </td>
      <td data-label="Vai trò">
        {u.is_self || isOwner ? (
          <span className="users-table__role">{roleLabel(u.role.code, u.role.name)}</span>
        ) : (
          <select
            className="gh-input users-table__select"
            aria-label={`Vai trò của ${u.display_name}`}
            value={u.role.code}
            disabled={role.isPending}
            onChange={(e) => role.mutate(e.target.value as InviteRole)}
          >
            {INVITE_ROLES.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        )}
      </td>
      <td data-label="Trạng thái">
        <Chip tone={st.tone} dot>
          {st.label}
        </Chip>
      </td>
      <td data-label="Đăng nhập gần nhất">{u.last_login_at ? fmtAgo(u.last_login_at) : 'chưa đăng nhập'}</td>
      <td className="retention-table__actions">
        {u.is_self ? (
          <span className="muted-note">sửa ở Tài khoản của tôi</span>
        ) : (
          <>
            <Button variant="ghost" className="btn-27" icon="ph ph-key" onClick={() => onAsk('reset')} aria-label={`Đặt lại mật khẩu ${u.display_name}`}>
              Đặt lại mật khẩu
            </Button>
            {u.status === 'active' ? (
              <Button variant="ghost" className="btn-27" icon="ph ph-lock-simple" onClick={() => onAsk('deactivate')} aria-label={`Khoá ${u.display_name}`}>
                Khoá
              </Button>
            ) : (
              <Button variant="ghost" className="btn-27" icon="ph ph-lock-simple-open" loading={reactivate.isPending} onClick={() => reactivate.mutate()} aria-label={`Mở khoá ${u.display_name}`}>
                Mở khoá
              </Button>
            )}
          </>
        )}
      </td>
    </tr>
  );
}

function InviteDialog({ onClose, onInvited }: { onClose: () => void; onInvited: (r: TempPasswordResult) => void }) {
  const qc = useQueryClient();
  const [v, setV] = useState({ display_name: '', email: '', role: 'operator' as InviteRole });
  const [submitted, setSubmitted] = useState(false);
  const [server, setServer] = useState<Record<string, string>>({});
  const client = {
    display_name: validateRequired(v.display_name, 'Nhập tên hiển thị.'),
    email: validateEmail(v.email),
  };
  const err = (k: 'display_name' | 'email' | 'role') => server[k] ?? (submitted ? (client as Record<string, string | null>)[k] ?? null : null);
  const invite = useMutation({
    mutationFn: () => api.users.invite({ display_name: v.display_name.trim(), email: v.email.trim().toLowerCase(), role: v.role }),
    onSuccess: (r) => {
      qc.setQueryData<UsersPage>(USERS_KEY, (old) => (old ? { ...old, items: [...old.items, r.user] } : old));
      onInvited(r);
    },
    onError: (e) => {
      const f = serverFieldErrors(e);
      if (f) setServer(f);
    },
  });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (client.display_name || client.email) return;
    invite.mutate();
  };
  const set = (k: keyof typeof v, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    setServer((s) => {
      const { [k]: _drop, ...rest } = s;
      return rest;
    });
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={460}
      title="Mời người dùng"
      kicker="Tạo tài khoản + mật khẩu tạm · cần mã PIN"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" type="submit" form="invite-user-form" icon="ph ph-user-plus" loading={invite.isPending}>
            Mời
          </Button>
        </>
      }
    >
      <form id="invite-user-form" className="acct-form" onSubmit={submit} noValidate aria-label="Mời người dùng">
        <TextField label="Tên hiển thị" value={v.display_name} onChange={(e) => set('display_name', e.target.value)} error={err('display_name')} maxLength={100} autoFocus />
        <TextField label="Email đăng nhập" type="email" value={v.email} onChange={(e) => set('email', e.target.value)} error={err('email')} maxLength={320} />
        <SelectField label="Vai trò" value={v.role} onChange={(e) => set('role', e.target.value)} options={INVITE_ROLES} error={err('role')} />
        {invite.isError && !serverFieldErrors(invite.error) ? <InlineError>{errorText(invite.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}

function ConfirmDialog({ pending, onClose, onSecret }: { pending: Pending; onClose: () => void; onSecret: (s: { title: string; result: TempPasswordResult }) => void }) {
  const update = useUpdateRow();
  const { user, kind } = pending;
  const run = useMutation({
    mutationFn: async () => {
      if (kind === 'deactivate') {
        update(await api.users.deactivate(user.id));
        toast(`Đã khoá ${user.display_name} — mọi phiên đăng nhập của người này đã bị đăng xuất.`);
        onClose();
      } else {
        const r = await api.users.resetPassword(user.id);
        update(r.user);
        onClose();
        onSecret({ title: `Mật khẩu tạm mới cho ${user.display_name}`, result: r });
      }
    },
  });
  const isReset = kind === 'reset';
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title={isReset ? `Đặt lại mật khẩu cho ${user.display_name}?` : `Khoá tài khoản ${user.display_name}?`}
      kicker={`${user.email} · cần mã PIN`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon={isReset ? 'ph ph-key' : 'ph ph-lock-simple'} loading={run.isPending} onClick={() => run.mutate()}>
            {isReset ? 'Đặt lại mật khẩu' : 'Khoá tài khoản'}
          </Button>
        </>
      }
    >
      <ul className="upd-confirm">
        {isReset ? (
          <>
            <li>Tạo mật khẩu tạm mới — mật khẩu cũ hết dùng được ngay.</li>
            <li>Mọi thiết bị của người này bị đăng xuất; lần đăng nhập tới phải đặt mật khẩu mới.</li>
          </>
        ) : (
          <>
            <li>Người này không đăng nhập được nữa; mọi phiên đang mở bị đăng xuất ngay.</li>
            <li>Dữ liệu và lịch sử giữ nguyên — bấm Mở khoá để dùng lại.</li>
          </>
        )}
      </ul>
      {run.isError ? <p className="upd-error">{errorText(run.error)}</p> : null}
    </Dialog>
  );
}
