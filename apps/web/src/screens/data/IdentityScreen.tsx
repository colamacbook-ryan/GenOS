import { useEffect, useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import {
  SCREEN_BY_KEY,
  type IdentityCandidate,
  type IdentityHistoryItem,
  type IdentityHistoryParty,
  type IdentitySide,
} from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Skeleton } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';
import { fmtDMClock, fmtInt } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { errorText } from '../../lib/errorText';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import { OK, WARN, channelIcon, channelTone, confidencePct, idStatCards, levelTone } from './dataModel';

export function IdentityScreen() {
  const meta = SCREEN_BY_KEY.identity;
  const canManage = useCan('data.manage');
  const stats = useQuery({ queryKey: qk2.idStats, queryFn: ({ signal }) => api.identity.stats(signal) });
  const pairs = useQuery({ queryKey: qk2.idCandidates, queryFn: ({ signal }) => api.identity.candidates('pending', signal) });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [evidenceFor, setEvidenceFor] = useState<IdentityCandidate | null>(null);

  return (
    <div className="screen">
      <ScreenHead
        title={meta.title}
        description={meta.description}
        maxWidth={700}
        actions={
          <Button variant="secondary" icon="ph ph-clock-counter-clockwise" className="btn-30" onClick={() => setHistoryOpen(true)}>
            Lịch sử gộp / tách
          </Button>
        }
      />

      <div className="id-stats" aria-label="Thống kê hợp nhất danh tính">
        {stats.isPending ? (
          Array.from({ length: 4 }, (_, i) => (
            <div className="id-stat" key={i} aria-hidden>
              <Skeleton width={140} height={9} />
              <Skeleton width={60} height={22} style={{ marginTop: 8 }} />
              <Skeleton width={170} height={10} style={{ marginTop: 6 }} />
            </div>
          ))
        ) : stats.isError ? (
          <div className="gh-card" style={{ gridColumn: '1 / -1' }}>
            <CardError error={stats.error} onRetry={() => void stats.refetch()} retrying={stats.isFetching} />
          </div>
        ) : (
          idStatCards(stats.data).map((c) => (
            <div className="id-stat" key={c.label}>
              <span className="id-stat__label">{c.label}</span>
              <div className="id-stat__nums">
                <span className="id-stat__value" style={{ color: c.tone }}>
                  {fmtInt(c.value)}
                </span>
                <span className="id-stat__unit">{c.unit}</span>
              </div>
              <div className="id-stat__sub">{c.sub}</div>
            </div>
          ))
        )}
      </div>

      <div className="pairs" aria-label="Cặp chờ xác nhận">
        {pairs.isPending ? (
          Array.from({ length: 3 }, (_, i) => (
            <div className="pair" key={i} aria-hidden>
              <SkeletonLines rows={1} padding="4px 0" />
            </div>
          ))
        ) : pairs.isError ? (
          <div className="gh-card">
            <CardError error={pairs.error} onRetry={() => void pairs.refetch()} retrying={pairs.isFetching} />
          </div>
        ) : pairs.data.length === 0 ? (
          <div className="gh-card">
            <EmptyState
              icon="ph ph-git-merge"
              title="Không còn cặp nào chờ xác nhận"
              description="Khi một người xuất hiện trên kênh khác, hệ thống sẽ gợi ý gộp ở đây — không bao giờ tự gộp."
            />
          </div>
        ) : (
          pairs.data.map((c) => <PairCard key={c.id} c={c} canManage={canManage} onEvidence={() => setEvidenceFor(c)} />)
        )}
      </div>

      <HistoryDialog open={historyOpen} onClose={() => setHistoryOpen(false)} canManage={canManage} />
      <EvidenceDialog candidate={evidenceFor} onClose={() => setEvidenceFor(null)} />
    </div>
  );
}

function Side({ s }: { s: IdentitySide }) {
  return (
    <div className="pair__side">
      <span className="pair__avatar" style={{ color: channelTone(s.channel) }}>
        <Icon name={channelIcon(s.channel)} size={15} />
      </span>
      <div style={{ minWidth: 0 }}>
        <div className="pair__name" title={s.person.name}>
          {s.person.name}
        </div>
        <div className="pair__meta" title={s.meta}>
          {s.meta}
        </div>
      </div>
    </div>
  );
}

function dropCandidate(id: string) {
  queryClient.setQueryData<IdentityCandidate[]>(qk2.idCandidates, (old) => old?.filter((x) => x.id !== id));
  void queryClient.invalidateQueries({ queryKey: qk2.idStats });
  void queryClient.invalidateQueries({ queryKey: qk2.idHistory });
}

function PairCard({ c, canManage, onEvidence }: { c: IdentityCandidate; canManage: boolean; onEvidence: () => void }) {
  const tone = levelTone(c.level);
  const merge = useMutation({
    mutationFn: () => api.identity.merge(c.id),
    onSuccess: (r) => {
      dropCandidate(c.id);
      toast(`Đã gộp vào ${r.person.code} · ${r.person.name}`);
    },
  });
  const reject = useMutation({
    mutationFn: () => api.identity.reject(c.id),
    onSuccess: () => {
      dropCandidate(c.id);
      toast('Đã đánh dấu không phải cùng một người', 'neutral');
    },
  });
  const err = merge.error ?? reject.error;
  return (
    <article className="pair" aria-label={`${c.a.person.name} và ${c.b.person.name}, khớp ${confidencePct(c.confidence)}`}>
      <div className="pair__grid">
        <Side s={c.a} />
        <div className="pair__mid">
          <span className="pair__conf" style={{ color: tone, borderColor: tone }}>
            khớp {confidencePct(c.confidence)}
          </span>
          <span className="pair__rule" />
          <span className="pair__basis">{c.basis}</span>
        </div>
        <Side s={c.b} />
        <div className="pair__actions">
          {canManage ? (
            <>
              <Button
                variant="primary"
                icon="ph ph-git-merge"
                loading={merge.isPending}
                disabled={reject.isPending}
                onClick={() => merge.mutate()}
              >
                Gộp
              </Button>
              <Button variant="secondary" loading={reject.isPending} disabled={merge.isPending} onClick={() => reject.mutate()}>
                Không phải
              </Button>
            </>
          ) : null}
          <Button variant="ghost" onClick={onEvidence}>
            Chứng cứ
          </Button>
        </div>
      </div>
      {err && !merge.isPending && !reject.isPending ? (
        <div className="pair__error">
          <InlineError>{errorText(err)}</InlineError>
        </div>
      ) : null}
    </article>
  );
}

function EvidenceDialog({ candidate, onClose }: { candidate: IdentityCandidate | null; onClose: () => void }) {
  const tz = useOrgTimezone();
  const q = useQuery({
    queryKey: qk2.idEvidence(candidate?.id ?? '-'),
    queryFn: ({ signal }) => api.identity.evidence(candidate!.id, signal),
    enabled: !!candidate,
  });
  const detail = candidate ? Object.entries(candidate.basis_detail ?? {}) : [];
  return (
    <Dialog
      open={!!candidate}
      onClose={onClose}
      width={540}
      title="Chứng cứ gợi ý gộp"
      kicker={candidate ? `${candidate.a.person.name} ↔ ${candidate.b.person.name} · khớp ${confidencePct(candidate.confidence)}` : undefined}
    >
      <div className="dlg-fields">
        {candidate ? (
          <div className="dlg-kv">
            <span className="dlg-kv__k">căn cứ</span>
            <span className="dlg-kv__v">{candidate.basis}</span>
            {detail.map(([k, v]) => (
              <FragmentKV key={k} k={k} v={v} />
            ))}
          </div>
        ) : null}
        {q.isPending ? (
          <SkeletonLines rows={3} padding="0" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : q.data.length === 0 ? (
          <p className="muted-note">Không có tin nhắn nào làm chứng cứ.</p>
        ) : (
          <div className="dlg-list">
            {q.data.map((ev) => (
              <div className="dlg-item" key={ev.raw.id}>
                <div className="dlg-item__meta">
                  <Icon name={channelIcon(ev.raw.channel.type)} size={13} color={channelTone(ev.raw.channel.type)} label={ev.raw.channel.name} />
                  <span>{ev.raw.code}</span>
                  <span>·</span>
                  <span>{fmtDMClock(ev.raw.received_at, tz)}</span>
                  {ev.raw.person ? (
                    <>
                      <span>·</span>
                      <span>{ev.raw.person.name}</span>
                    </>
                  ) : null}
                </div>
                <div className="dlg-item__text">{ev.raw.text}</div>
                {ev.note ? <div className="dlg-item__quote">{ev.note}</div> : null}
              </div>
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}

function FragmentKV({ k, v }: { k: string; v: unknown }) {
  const text = typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean' ? String(v) : JSON.stringify(v);
  return (
    <>
      <span className="dlg-kv__k">{k}</span>
      <span className="dlg-kv__v">{text}</span>
    </>
  );
}

// ── Lịch sử gộp / tách ────────────────────────────────────────────────────
const partyName = (p: IdentityHistoryParty) => [p.code, p.name].filter(Boolean).join(' · ') || '—';

function actorLabel(a: IdentityHistoryItem['actor']): string {
  if (!a) return 'hệ thống';
  if (typeof a === 'string') return a;
  return a.label ?? a.display_name ?? a.name ?? '—';
}

function HistoryDialog({ open, onClose, canManage }: { open: boolean; onClose: () => void; canManage: boolean }) {
  const tz = useOrgTimezone();
  const q = useQuery({ queryKey: qk2.idHistory, queryFn: ({ signal }) => api.identity.history(signal), enabled: open });
  const [splitting, setSplitting] = useState<IdentityHistoryItem | null>(null);
  return (
    <Dialog open={open} onClose={onClose} width={560} title="Lịch sử gộp / tách" kicker="Mọi thao tác đều hoàn tác được và được ghi nhật ký">
      {q.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : q.data.length === 0 ? (
        <EmptyState icon="ph ph-clock-counter-clockwise" title="Chưa có thao tác gộp hay tách nào" />
      ) : (
        <div className="dlg-list">
          {q.data.map((h) => (
            <HistoryRow key={h.id} h={h} tz={tz} canManage={canManage} onSplit={() => setSplitting(h)} />
          ))}
        </div>
      )}
      <SplitDialog item={splitting} onClose={() => setSplitting(null)} />
    </Dialog>
  );
}

function HistoryRow({ h, tz, canManage, onSplit }: { h: IdentityHistoryItem; tz: string; canManage: boolean; onSplit: () => void }) {
  const revert = useMutation({
    mutationFn: () => api.identity.revert(h.id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: qk2.idHistory });
      void queryClient.invalidateQueries({ queryKey: qk2.idStats });
      void queryClient.invalidateQueries({ queryKey: qk2.idCandidates });
      toast('Đã hoàn tác thao tác');
    },
  });
  const canSplit = h.op === 'merge' && !h.reverted && !!h.to.id && (h.to.identities?.length ?? 0) > 1;
  return (
    <div className="dlg-item">
      <div className="dlg-item__meta">
        <Icon name={h.op === 'merge' ? 'ph ph-git-merge' : 'ph ph-scissors'} size={13} color={h.op === 'merge' ? OK : WARN} />
        <span>{h.op === 'merge' ? 'Gộp' : 'Tách'}</span>
        <span>·</span>
        <span>{fmtDMClock(h.at, tz)}</span>
        <span>·</span>
        <span>{actorLabel(h.actor)}</span>
        {h.reverted ? <span style={{ color: WARN }}>· đã hoàn tác</span> : null}
      </div>
      <div className="dlg-item__text">
        {partyName(h.from)} → {partyName(h.to)} · {fmtInt(h.identities)} tài khoản
      </div>
      {canManage && !h.reverted ? (
        <div className="dlg-row" style={{ marginTop: 8 }}>
          <Button variant="secondary" className="btn-27" icon="ph ph-arrow-counter-clockwise" loading={revert.isPending} onClick={() => revert.mutate()}>
            Hoàn tác
          </Button>
          {canSplit ? (
            <Button variant="ghost" className="btn-27" icon="ph ph-scissors" onClick={onSplit}>
              Tách tài khoản
            </Button>
          ) : null}
        </div>
      ) : null}
      {revert.isError ? <InlineError>{errorText(revert.error)}</InlineError> : null}
    </div>
  );
}

function SplitDialog({ item, onClose }: { item: IdentityHistoryItem | null; onClose: () => void }) {
  const [picked, setPicked] = useState<string[]>([]);
  useEffect(() => setPicked([]), [item]);
  const ids = item?.to.identities ?? [];
  const split = useMutation({
    mutationFn: () => api.identity.split(item!.to.id!, picked),
    onSuccess: (p) => {
      void queryClient.invalidateQueries({ queryKey: qk2.idHistory });
      void queryClient.invalidateQueries({ queryKey: qk2.idStats });
      toast(`Đã tách thành ${p.code} · ${p.name}`);
      onClose();
    },
  });
  const valid = picked.length > 0 && picked.length < ids.length;
  return (
    <Dialog
      open={!!item}
      onClose={onClose}
      width={420}
      title="Tách tài khoản"
      kicker={item ? `Khỏi ${partyName(item.to)} · tạo một người mới` : undefined}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-scissors" disabled={!valid} loading={split.isPending} onClick={() => split.mutate()}>
            Tách
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <p className="muted-note">Chọn các tài khoản kênh thuộc về người khác. Phải giữ lại ít nhất một tài khoản.</p>
        {ids.map((i) => (
          <label className="gh-check" key={i.identity_id}>
            <input
              type="checkbox"
              checked={picked.includes(i.identity_id)}
              onChange={(e) => setPicked((p) => (e.target.checked ? [...p, i.identity_id] : p.filter((x) => x !== i.identity_id)))}
            />
            <span>{i.meta ?? i.channel ?? i.identity_id}</span>
          </label>
        ))}
        <InlineError>{split.isError ? errorText(split.error) : null}</InlineError>
      </div>
    </Dialog>
  );
}
