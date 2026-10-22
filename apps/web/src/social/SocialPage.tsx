import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import type { SocialAccount, SocialPlatform, SocialWriteGate } from '@gen-harness/contracts';
import { Button, Card, Chip, Dialog, EmptyState, Icon, SelectField, Switch, TextField } from '@gen-harness/ui';
import { api } from '../lib/api';
import { errorText } from '../lib/errorText';
import { fmtAgo, fmtDMClock } from '../lib/format';
import { useOrgTimezone } from '../lib/permissions';
import { useMe } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { onRealtimeEvent } from '../lib/realtime';
import { toast } from '../lib/toast';
import { useNow } from '../lib/useNow';
import { useGenStore } from '../gen/genStore';
import { CardError, SkeletonLines } from '../screens/common';
import { ScreenTitle } from '../screens/ScreenPage';
import { LoginViewer } from './LoginViewer';
import {
  SOCIAL_KEY,
  WRITE_LIMIT_MAX,
  WRITE_LIMIT_MIN,
  WRITE_RISK_PATH,
  accountStatus,
  loginLabel,
  parseTimes,
  qkSocial,
  shortTarget,
  writeActionLabel,
  writeStatusView,
} from './socialModel';
import { ErrorWithDetail, WriteProofDialog } from './WriteProofDialog';

// Máy chủ báo tài khoản/việc đổi (đăng nhập xong, đọc xong, tự dừng…) → tải lại.
onRealtimeEvent('social.update', (qc) => void qc.invalidateQueries({ queryKey: SOCIAL_KEY }));

const refresh = () => void queryClient.invalidateQueries({ queryKey: SOCIAL_KEY });

/**
 * `/social` — Tài khoản mạng xã hội (v0.1.29, Đợt D3 lát đầu). CHỈ Owner. Gen chỉ ĐỌC thông báo + danh sách hội thoại.
 * Có công cụ, dùng hay không do Owner quyết (Boss 30/09) — mỗi tài khoản phải tích chấp nhận rủi ro; luật cứng (không
 * tài khoản giả, không lách chống bot) không phải lựa chọn.
 */
export function SocialPage() {
  const me = useMe();
  useEffect(() => {
    document.title = 'Tài khoản mạng xã hội · Gen-Harness';
  }, []);
  if (me.isPending) return <div className="screen"><SkeletonLines rows={4} /></div>;
  if (me.data?.role?.code !== 'owner') {
    return (
      <div className="screen">
        <div className="gh-card">
          <EmptyState icon="ph ph-lock-simple" title="Chỉ Owner dùng được" description="Tài khoản mạng xã hội là tài khoản cá nhân của Sếp — chỉ Owner thêm, đăng nhập và cho Gen đọc." />
        </div>
      </div>
    );
  }
  return <SocialBody ownerId={me.data.id} />;
}

function SocialBody({ ownerId }: { ownerId: string }) {
  const status = useQuery({ queryKey: qkSocial.status, queryFn: ({ signal }) => api.social.status(signal), refetchInterval: 30_000 });
  const platforms = useQuery({ queryKey: qkSocial.platforms, queryFn: ({ signal }) => api.social.platforms(signal) });
  const accounts = useQuery({
    queryKey: qkSocial.accounts,
    queryFn: ({ signal }) => api.social.accounts.list(signal),
    // Việc đang chạy → tải lại nhanh (dự phòng khi WS rớt).
    refetchInterval: (q) => (q.state.data?.items.some((a) => a.active_job) ? 4000 : 30_000),
  });
  const [adding, setAdding] = useState(false);
  const setGenOpen = useGenStore((s) => s.setOpen);
  const halted = !!status.data?.halted;

  return (
    <div className="screen social">
      <ScreenTitle
        title="Tài khoản mạng xã hội"
        description="Gen đọc thông báo và tin nhắn trên tài khoản của chính Sếp rồi tóm tắt khi Sếp hỏi. Trả lời bình luận và nhắn tin chỉ gửi khi Sếp bấm Xác nhận và nhập mã PIN; chưa hỗ trợ đăng bài."
        maxWidth={720}
      />
      <div className="social-grid">
        <div className="social-main">
          {halted ? (
            <div className="risk-box no-model" role="alert" data-testid="social-halted">
              <Icon name="ph ph-hand-palm" size={16} color="var(--color-bad)" />
              <div className="no-model__body">
                <div className="risk-box__title">Đã dừng tất cả việc trình duyệt</div>
                <p className="risk-box__text">Không việc nào chạy (kể cả lịch và Gen) tới khi Sếp bấm Bật lại.</p>
              </div>
            </div>
          ) : null}
          <Card
            title="Tài khoản"
            kicker="Mỗi tài khoản chỉ chạy một việc một lúc"
            actions={
              <Button variant="primary" className="btn-28" icon="ph ph-plus" onClick={() => setAdding(true)} disabled={!platforms.data}>
                Thêm tài khoản
              </Button>
            }
          >
            {accounts.isPending ? (
              <SkeletonLines rows={3} />
            ) : accounts.isError ? (
              <CardError error={accounts.error} onRetry={() => void accounts.refetch()} retrying={accounts.isFetching} />
            ) : accounts.data.items.length === 0 ? (
              <EmptyState
                icon="ph ph-facebook-logo"
                title="Chưa có tài khoản nào"
                description="Bấm Thêm tài khoản, đọc kỹ cảnh báo rủi ro, rồi tự đăng nhập trong cửa sổ trình duyệt."
              />
            ) : (
              <ul className="social-list" aria-label="Tài khoản mạng xã hội">
                {accounts.data.items.map((a) => (
                  <AccountRow key={a.id} account={a} halted={halted} />
                ))}
              </ul>
            )}
          </Card>
        </div>
        <div className="side-col">
          <KillSwitchCard halted={halted} worker={status.data?.worker ?? null} loading={status.isPending} />
          <WriteGateCard accounts={accounts.data?.items ?? []} />
          <HardRulesCard rules={status.data?.hard_rules ?? platforms.data?.hard_rules ?? []} />
          <Card title="Hỏi Gen" kicker="Tóm tắt khi cần">
            <p className="muted-note">Hỏi: "Facebook có gì mới?" — Gen đọc (tốn 1 lượt) rồi tóm tắt; mục đáng ngờ được đánh dấu.</p>
            <Button variant="secondary" icon="ph ph-sparkle" onClick={() => setGenOpen(ownerId, true)}>
              Mở Gen
            </Button>
            <p className="muted-note">
              Mọi lần kết nối, đăng nhập, đọc, gỡ đều ghi ở <Link to="/system?tab=log">Nhật ký hành động</Link>.
            </p>
          </Card>
        </div>
      </div>
      {platforms.data ? (
        <AddAccountDialog open={adding} platforms={platforms.data.items} riskVersion={platforms.data.risk_version} onClose={() => setAdding(false)} />
      ) : null}
    </div>
  );
}

function KillSwitchCard({ halted, worker, loading }: { halted: boolean; worker: { version: string; at: string; running: number } | null; loading: boolean }) {
  const [confirm, setConfirm] = useState(false);
  const halt = useMutation({
    mutationFn: () => api.social.halt(),
    onSuccess: () => {
      toast('Đã dừng tất cả việc trình duyệt', 'warn');
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const release = useMutation({
    mutationFn: () => api.social.release(),
    onSuccess: () => {
      toast('Đã bật lại', 'ok');
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  return (
    <Card title="Công tắc dừng khẩn" kicker={halted ? 'Đang dừng' : 'Đang cho phép chạy'} data-testid="social-kill-switch">
      <p className="muted-note">Đóng ngay mọi trình duyệt nền, chặn cả ĐỌC và GỬI. Bật lại cần mã PIN.</p>
      <p className="muted-note">
        {worker ? (
          <>
            Trình duyệt: <strong>sẵn sàng</strong>
            {worker.running ? ` · đang chạy ${worker.running} việc` : ''}
          </>
        ) : loading ? (
          '…'
        ) : (
          'Trình duyệt chưa chạy (dịch vụ browser) — việc sẽ chờ tới khi dịch vụ lên.'
        )}
      </p>
      {halted ? (
        <Button variant="secondary" icon="ph ph-play" loading={release.isPending} onClick={() => release.mutate()}>
          Bật lại (cần PIN)
        </Button>
      ) : (
        <Button variant="secondary" className="social-danger" icon="ph ph-hand-palm" loading={halt.isPending} onClick={() => setConfirm(true)}>
          Dừng tất cả
        </Button>
      )}
      <Dialog
        open={confirm}
        onClose={() => setConfirm(false)}
        title="Dừng tất cả việc trình duyệt?"
        kicker="Đóng mọi trình duyệt ngay, không nhận việc mới"
        actions={
          <>
            <Button variant="secondary" onClick={() => setConfirm(false)}>
              Không
            </Button>
            <Button
              variant="primary"
              className="social-danger"
              icon="ph ph-hand-palm"
              onClick={() => {
                setConfirm(false);
                halt.mutate();
              }}
            >
              Dừng ngay
            </Button>
          </>
        }
      >
        <p className="risk-box__text">Việc đang chạy bị huỷ, lịch đọc và Gen không chạy nữa cho tới khi Sếp bấm Bật lại (cần PIN). Phiên đăng nhập đã lưu vẫn giữ nguyên.</p>
      </Dialog>
    </Card>
  );
}

/** Mô tả cổng ghi: Mở/Khoá + lý do (một nguồn cho thẻ và test). */
function gateView(g: SocialWriteGate): { open: boolean; text: string } {
  if (g.open) return { open: true, text: g.sandbox.enabled === true ? 'Trình duyệt chạy trong sandbox' : 'Sếp đã đồng ý rủi ro' };
  if (!g.worker_online) return { open: false, text: 'Trình duyệt nền chưa chạy, Sếp chưa đồng ý rủi ro' };
  return { open: false, text: g.sandbox.reason ?? 'Chưa bật được sandbox và Sếp chưa đồng ý rủi ro' };
}

/** v0.1.47 (F-79/F-85): cổng ghi Facebook, giới hạn gửi/ngày từng tài khoản, 10 lần gửi gần đây (+ ảnh chụp). */
function WriteGateCard({ accounts }: { accounts: SocialAccount[] }) {
  const tz = useOrgTimezone();
  const gate = useQuery({ queryKey: qkSocial.writeGate, queryFn: ({ signal }) => api.social.writeGate(signal), refetchInterval: 30_000 });
  const writes = useQuery({
    queryKey: [...qkSocial.writes, 10],
    queryFn: ({ signal }) => api.social.writes({ limit: 10 }, signal),
    refetchInterval: (q) => (q.state.data?.items.some((w) => w.status === 'queued' || w.status === 'running') ? 4000 : 30_000),
  });
  const [proofFor, setProofFor] = useState<string | null>(null);
  const view = gate.data ? gateView(gate.data) : null;
  return (
    <Card title="Gửi trả lời & tin nhắn" kicker={view ? (view.open ? 'Cổng gửi: Mở' : 'Cổng gửi: Khoá') : 'Cổng gửi'} data-testid="social-write-gate">
      {gate.isPending ? (
        <SkeletonLines rows={2} />
      ) : gate.isError ? (
        <CardError error={gate.error} onRetry={() => void gate.refetch()} retrying={gate.isFetching} />
      ) : view ? (
        <p className="muted-note" data-testid="social-write-gate-state" data-open={view.open}>
          <Chip tone={view.open ? 'ok' : 'warn'} dot>
            {view.open ? 'Mở' : 'Khoá'}
          </Chip>{' '}
          {view.text}
        </p>
      ) : null}
      <p className="muted-note">
        <Link to={WRITE_RISK_PATH}>Đọc cảnh báo rủi ro{gate.data?.consent || gate.data?.sandbox.enabled ? '' : ' & đồng ý'}</Link>
      </p>
      {accounts.length ? (
        <ul className="write-accounts" aria-label="Giới hạn gửi theo tài khoản">
          {accounts.map((a) => (
            <WriteLimitRow key={a.id} account={a} />
          ))}
        </ul>
      ) : null}
      <div className="setup-section__title">Lần gửi gần đây</div>
      {writes.isPending ? (
        <SkeletonLines rows={2} />
      ) : writes.isError ? (
        <CardError error={writes.error} onRetry={() => void writes.refetch()} retrying={writes.isFetching} />
      ) : writes.data.items.length === 0 ? (
        <p className="muted-note">Chưa gửi lần nào.</p>
      ) : (
        <ul className="write-recent" aria-label="Lần gửi gần đây" data-testid="social-writes">
          {writes.data.items.slice(0, 10).map((w) => {
            const st = writeStatusView(w);
            return (
              <li key={w.job_id} className="write-recent__item">
                <div className="write-recent__top">
                  <span className="muted-note">{fmtDMClock(w.created_at, tz)}</span>
                  <strong>{writeActionLabel(w.action)}</strong>
                  <Chip tone={st.tone}>{st.chip}</Chip>
                </div>
                <div className="muted-note write-recent__target" title={w.target_url ?? undefined}>
                  {w.account_label} · {shortTarget(w.target_url)}
                </div>
                {w.status === 'failed' && w.error_text ? <div className="muted-note">{w.error_text}</div> : null}
                {st.notes.map((n) => (
                  <div key={n} className="muted-note write-recent__note">
                    {n}
                  </div>
                ))}
                {w.has_proof ? (
                  <Button variant="ghost" size="sm" icon="ph ph-image" onClick={() => setProofFor(w.job_id)}>
                    Xem ảnh chụp
                  </Button>
                ) : null}
              </li>
            );
          })}
        </ul>
      )}
      {proofFor ? <WriteProofDialog jobId={proofFor} open onClose={() => setProofFor(null)} /> : null}
    </Card>
  );
}

function WriteLimitRow({ account: a }: { account: SocialAccount }) {
  const limit = a.daily_write_limit ?? 10;
  const save = useMutation({
    mutationFn: (n: number) => api.social.accounts.update(a.id, { daily_write_limit: n }),
    onSuccess: (acc) => {
      toast(`${acc.label}: giới hạn gửi ${acc.daily_write_limit}/ngày`, 'neutral');
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const options = Array.from({ length: WRITE_LIMIT_MAX - WRITE_LIMIT_MIN + 1 }, (_, i) => String(WRITE_LIMIT_MIN + i)).map((v) => ({ value: v, label: v }));
  return (
    <li className="write-accounts__row" data-testid={`social-write-limit-${a.id}`}>
      <div className="write-accounts__name">{a.label}</div>
      <div className="muted-note">
        Đã dùng {a.writes_today ?? 0}/{limit} lượt gửi (24 giờ qua)
      </div>
      <SelectField label="Giới hạn gửi/ngày" value={String(limit)} options={options} disabled={save.isPending} onChange={(e) => save.mutate(Number(e.target.value))} />
      {save.isError ? <ErrorWithDetail error={save.error} /> : null}
    </li>
  );
}

function HardRulesCard({ rules }: { rules: string[] }) {
  return (
    <Card title="Luật cứng — luôn tắt" kicker="Không phải lựa chọn rủi ro" data-testid="social-hard-rules">
      <ul className="risk-list">
        {rules.map((r) => (
          <li key={r}>{r}</li>
        ))}
      </ul>
    </Card>
  );
}

function AccountRow({ account: a, halted }: { account: SocialAccount; halted: boolean }) {
  const now = useNow(60_000);
  const st = accountStatus(a);
  const [ticket, setTicket] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  const [showItems, setShowItems] = useState(false);
  const busy = !!a.active_job || halted;

  const login = useMutation({
    mutationFn: () => api.social.accounts.login(a.id),
    onSuccess: (t) => {
      setTicket(t.ticket);
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const act = useMutation({
    mutationFn: (what: 'read' | 'check' | 'pause' | 'resume'): Promise<unknown> =>
      what === 'read'
        ? api.social.accounts.read(a.id)
        : what === 'check'
          ? api.social.accounts.check(a.id)
          : what === 'pause'
            ? api.social.accounts.pause(a.id)
            : api.social.accounts.resume(a.id),
    onSuccess: (_r, what) => {
      toast(what === 'read' ? 'Đã xếp lượt đọc' : what === 'check' ? 'Đang kiểm phiên' : what === 'pause' ? 'Đã tạm dừng' : 'Đã tiếp tục', 'neutral');
      if (what === 'read') setShowItems(true);
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  const onLoggedIn = useCallback(() => {
    toast(`${a.label}: đã đăng nhập`, 'ok');
    refresh();
    setTimeout(() => setTicket(null), 800);
  }, [a.label]);

  const canLogin = a.status !== 'active' || !a.has_session;
  return (
    <li className="social-row" data-testid={`social-account-${a.id}`}>
      <div className="social-row__head">
        <Icon name="ph ph-facebook-logo" size={18} />
        <div className="social-row__main">
          <div className="social-row__title">
            {a.label} <span className="muted-note">· {a.platform_name}</span>
          </div>
          <div className="muted-note">
            {a.external_handle ? `${a.external_handle} · ` : ''}
            {a.last_read_at ? `đọc ${fmtAgo(a.last_read_at, now)}` : 'chưa đọc lần nào'}
            {a.risk_accepted_at ? ` · đã chấp nhận rủi ro ${fmtAgo(a.risk_accepted_at, now)}` : ''}
          </div>
        </div>
        <Chip tone={st.tone} dot>
          {st.label}
        </Chip>
      </div>
      {st.hint ? <p className="muted-note social-row__hint">{st.hint}</p> : null}
      {a.active_job?.error_text ? <p className="muted-note">{a.active_job.error_text}</p> : null}
      <div className="social-row__actions">
        <Button variant={canLogin ? 'primary' : 'secondary'} size="sm" icon="ph ph-sign-in" disabled={busy} loading={login.isPending} onClick={() => login.mutate()}>
          {loginLabel(a)}
        </Button>
        <Button variant="secondary" size="sm" icon="ph ph-tray" disabled={busy || a.status !== 'active'} onClick={() => act.mutate('read')}>
          Đọc ngay
        </Button>
        <Button variant="secondary" size="sm" icon="ph ph-heartbeat" disabled={busy || !a.has_session} onClick={() => act.mutate('check')}>
          Kiểm tra phiên
        </Button>
        {a.status === 'paused' ? (
          <Button variant="secondary" size="sm" icon="ph ph-play" onClick={() => act.mutate('resume')}>
            Tiếp tục
          </Button>
        ) : (
          <Button variant="ghost" size="sm" icon="ph ph-pause" onClick={() => act.mutate('pause')}>
            Tạm dừng
          </Button>
        )}
        <Button variant="ghost" size="sm" icon="ph ph-trash" onClick={() => setRemoving(true)}>
          Gỡ tài khoản
        </Button>
        <Button variant="ghost" size="sm" icon={showItems ? 'ph ph-caret-up' : 'ph ph-caret-down'} onClick={() => setShowItems((v) => !v)}>
          Lần đọc gần nhất
        </Button>
      </div>
      <ScheduleEditor account={a} />
      {showItems ? <LatestItems accountId={a.id} /> : null}
      <LoginViewer open={!!ticket} ticket={ticket} label={a.label} onClose={() => { setTicket(null); refresh(); }} onLoggedIn={onLoggedIn} />
      <RemoveDialog open={removing} account={a} onClose={() => setRemoving(false)} />
    </li>
  );
}

function ScheduleEditor({ account: a }: { account: SocialAccount }) {
  const [text, setText] = useState(a.schedule.times.join(', ') || '08:00, 17:00');
  const parsed = parseTimes(text);
  const save = useMutation({
    mutationFn: (enabled: boolean) => api.social.accounts.update(a.id, { schedule: { enabled, times: enabled ? parsed.times : a.schedule.times } }),
    onSuccess: (acc) => {
      toast(acc.schedule.enabled ? `Lịch đọc: ${acc.schedule.times.join(', ')}` : 'Đã tắt lịch đọc tự động', 'neutral');
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  return (
    <div className="social-sched">
      <Switch checked={a.schedule.enabled} label="Đọc tự động theo lịch" onChange={(v) => { if (!v || !parsed.error) save.mutate(v); }} disabled={save.isPending} />
      <span className="social-sched__label">Đọc tự động (mặc định tắt)</span>
      <TextField
        label="Giờ đọc"
        value={text}
        onChange={(e) => setText(e.target.value)}
        error={parsed.error}
        hint={`Tối đa ${a.daily_read_limit} lượt/ngày, không chạy 23:00–06:00.`}
      />
      {a.schedule.enabled ? (
        <Button variant="secondary" size="sm" disabled={!!parsed.error} loading={save.isPending} onClick={() => save.mutate(true)}>
          Lưu giờ
        </Button>
      ) : null}
    </div>
  );
}

function LatestItems({ accountId }: { accountId: string }) {
  const q = useQuery({ queryKey: qkSocial.latest(accountId), queryFn: ({ signal }) => api.social.accounts.latest(accountId, signal) });
  if (q.isPending) return <SkeletonLines rows={3} />;
  if (q.isError) return <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />;
  const job = q.data.job;
  const items = job?.result?.items ?? [];
  if (!job) return <p className="muted-note">Chưa có lần đọc nào xong.</p>;
  return (
    <div className="social-items" data-testid="social-items">
      <p className="muted-note">
        {job.result?.counts ? `${job.result.counts.notifications} thông báo · ${job.result.counts.inbox} hội thoại · ${job.result.counts.unread} chưa đọc` : ''}
        {' — nội dung do người ngoài viết, chỉ để đọc.'}
      </p>
      <ul>
        {items.map((it, i) => (
          <li key={i} className="social-item" data-unread={it.unread || undefined}>
            <Icon name={it.kind === 'inbox' ? 'ph ph-chats' : 'ph ph-bell'} size={13} />
            <span className="social-item__text">
              {it.who ? <strong>{it.who}: </strong> : null}
              {it.text}
            </span>
            {it.suspicious ? <Chip tone="bad">đáng ngờ</Chip> : null}
            {it.time ? <span className="muted-note">{it.time}</span> : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function RemoveDialog({ open, account: a, onClose }: { open: boolean; account: SocialAccount; onClose: () => void }) {
  const remove = useMutation({
    mutationFn: () => api.social.accounts.remove(a.id),
    onSuccess: () => {
      toast(`Đã gỡ ${a.label} — đã xoá phiên đăng nhập và nội dung đã đọc`, 'ok');
      onClose();
      refresh();
    },
    onError: (e) => toast(errorText(e), 'bad'),
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={`Gỡ ${a.label}?`}
      kicker="Xoá phiên đăng nhập đã lưu + nội dung đã đọc"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Không
          </Button>
          <Button variant="primary" className="social-danger" icon="ph ph-trash" loading={remove.isPending} onClick={() => remove.mutate()}>
            Gỡ tài khoản
          </Button>
        </>
      }
    >
      <p className="risk-box__text">
        Hệ thống xoá phiên (cookie) đã mã hoá và mọi nội dung đã đọc của tài khoản này, huỷ việc đang chờ. Muốn chắc chắn hơn: vào
        Facebook → Cài đặt → Mật khẩu và bảo mật → "Nơi bạn đã đăng nhập" để đăng xuất thiết bị này. Cần PIN.
      </p>
    </Dialog>
  );
}

/** Thêm tài khoản: chọn nền tảng + đặt tên → hộp chấp nhận rủi ro (bắt buộc tích) → PIN. */
export function AddAccountDialog({
  open,
  platforms,
  riskVersion,
  onClose,
}: {
  open: boolean;
  platforms: SocialPlatform[];
  riskVersion: string;
  onClose: () => void;
}) {
  const [platform, setPlatform] = useState(platforms[0]?.key ?? '');
  const [label, setLabel] = useState('');
  const [step, setStep] = useState<'pick' | 'risk'>('pick');
  const [acceptRisk, setAcceptRisk] = useState(false);
  const [acceptRules, setAcceptRules] = useState(false);
  useEffect(() => {
    if (open) {
      setStep('pick');
      setAcceptRisk(false);
      setAcceptRules(false);
      setLabel('');
    }
  }, [open]);
  const p = platforms.find((x) => x.key === platform);
  const create = useMutation({
    mutationFn: () => api.social.accounts.create({ platform, label: label.trim(), risk_version: riskVersion, accept_risk: acceptRisk, accept_rules: acceptRules }),
    onSuccess: (acc) => {
      toast(`Đã thêm ${acc.label} — bấm Đăng nhập để tự đăng nhập`, 'ok');
      onClose();
      refresh();
    },
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={560}
      title={step === 'pick' ? 'Thêm tài khoản mạng xã hội' : `Trước khi kết nối ${p?.name ?? ''}`}
      kicker={step === 'pick' ? 'Chỉ tài khoản thật của chính Sếp' : 'Đọc kỹ — Sếp tự quyết có dùng hay không'}
      actions={
        step === 'pick' ? (
          <>
            <Button variant="secondary" onClick={onClose}>
              Huỷ
            </Button>
            <Button variant="primary" iconRight="ph ph-arrow-right" disabled={!p || !label.trim()} onClick={() => setStep('risk')}>
              Tiếp
            </Button>
          </>
        ) : (
          <>
            <Button variant="secondary" onClick={() => setStep('pick')}>
              Quay lại
            </Button>
            <Button variant="primary" icon="ph ph-shield-check" disabled={!acceptRisk || !acceptRules} loading={create.isPending} onClick={() => create.mutate()}>
              Tôi chấp nhận, thêm tài khoản
            </Button>
          </>
        )
      }
    >
      {step === 'pick' ? (
        <div className="dlg-fields">
          <SelectField label="Nền tảng" value={platform} onChange={(e) => setPlatform(e.target.value)} options={platforms.map((x) => ({ value: x.key, label: x.name }))} />
          <TextField label="Tên để nhận ra" placeholder="Ví dụ: Facebook của Sếp" value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} />
          <p className="muted-note">Trang Facebook của công ty, Instagram chuyên nghiệp… sẽ đi cổng chính thức (API) ở bản sau — an toàn hơn.</p>
        </div>
      ) : p ? (
        <div className="dlg-fields" data-testid="social-risk-dialog">
          <div className="risk-box" role="note">
            <Icon name="ph ph-warning" size={16} color="var(--color-warn)" />
            <div>
              <div className="risk-box__title">Rủi ro</div>
              <ul className="risk-list">
                {p.risk.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          </div>
          <div className="social-dos">
            <div>
              <div className="setup-section__title">Gen-Harness SẼ</div>
              <ul className="risk-list">
                {p.will_do.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
            <div>
              <div className="setup-section__title">Gen-Harness KHÔNG</div>
              <ul className="risk-list">
                {p.wont_do.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          </div>
          <label className="gh-check">
            <input type="checkbox" checked={acceptRisk} onChange={(e) => setAcceptRisk(e.target.checked)} data-autofocus />
            <span>Tôi hiểu và chấp nhận rủi ro tài khoản có thể bị xác minh, hạn chế hoặc khoá.</span>
          </label>
          <label className="gh-check">
            <input type="checkbox" checked={acceptRules} onChange={(e) => setAcceptRules(e.target.checked)} />
            <span>Đây là tài khoản thật của chính tôi; tôi tự đăng nhập, không dùng tài khoản giả hay của người khác.</span>
          </label>
          {create.isError ? <p className="setup-error" role="alert">{errorText(create.error)}</p> : null}
        </div>
      ) : null}
    </Dialog>
  );
}
