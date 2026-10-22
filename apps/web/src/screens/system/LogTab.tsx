import { useEffect, useMemo, useState } from 'react';
import { EmptyState, Icon } from '@gen-harness/ui';
import { downloadText } from '../../lib/download';
import { fmtDMClock } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { errorText } from '../../lib/errorText';
import { toast } from '../../lib/toast';
import { CardError, SkeletonLines } from '../common';
import { N4 } from '../data/dataModel';
import { actionLabel, actorLabel, localDayStartIso, matchingActionCodes, matchingActorIds, targetText } from './auditLabels';
import { auditActorTone, auditResultView } from './systemModel';
import { useExportAuditLog, useSystemAuditLog } from './queries';

const ACTOR_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: 'Mọi tác nhân' },
  { value: 'user', label: 'Người' },
  { value: 'agent', label: 'Agent' },
  { value: 'system', label: 'Hệ thống' },
  { value: 'plugin', label: 'Plugin' },
];

/**
 * Nhật ký hệ thống — tìm, lọc ngày, xem hết (PLAN 4.5, spec `auditLog`).
 * A8 (F099/F105): ô tìm + lọc ngày chạy ở MÁY CHỦ trên toàn bộ nhật ký, "Xem thêm" nạp trang tiếp theo (cursor); dòng
 * chung `http.*` và lượt kiểm chuỗi bình thường ẩn mặc định; mã kỹ thuật dịch ra câu tiếng Việt (`auditLabels.ts`).
 */
export function LogTab() {
  const canRead = useCan('audit.read');
  const canExport = useCan('data.manage');
  const tz = useOrgTimezone();
  const [actorType, setActorType] = useState('');
  const [search, setSearch] = useState('');
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [showSystem, setShowSystem] = useState(false);
  // Gõ xong ~0,35 giây mới hỏi máy chủ — không gửi một yêu cầu cho mỗi phím.
  const [needle, setNeedle] = useState('');
  useEffect(() => {
    const h = window.setTimeout(() => setNeedle(search.trim()), 350);
    return () => window.clearTimeout(h);
  }, [search]);

  const since = from ? localDayStartIso(from, tz) : undefined;
  const until = to ? localDayStartIso(to, tz, 1) : undefined; // "đến hết ngày" ⇒ trước 0 giờ ngày kế tiếp
  const q = useSystemAuditLog({
    actor_type: actorType || undefined,
    since,
    until,
    q: needle || undefined,
    // Máy chủ chỉ lưu mã: gõ "đăng nhập" ⇒ gửi kèm mã hành động có nhãn tiếng Việt khớp.
    q_actions: needle ? matchingActionCodes(needle).join(',') || undefined : undefined,
    q_actors: needle ? matchingActorIds(needle).join(',') || undefined : undefined,
    hide_noise: showSystem ? undefined : true,
  });
  const exportCsv = useExportAuditLog();
  const rows = useMemo(() => q.data?.pages.flatMap((p) => p.items) ?? [], [q.data]);
  const filtering = !!(needle || actorType || from || to);

  const onExport = () => {
    exportCsv.mutate(
      { actor_type: actorType || undefined, since, until },
      {
        onSuccess: (csv) => {
          const d = new Date().toISOString().slice(0, 10).replace(/-/g, '');
          downloadText(csv, `nhat-ky-${d}.csv`);
          toast('Đã xuất nhật ký hành động');
        },
        onError: (e) => toast(errorText(e), 'bad'),
      },
    );
  };

  if (!canRead) {
    return (
      <div className="gh-card">
        <EmptyState icon="ph ph-lock-simple" title="Vai trò của bạn không xem được Nhật ký" description="Nhật ký hành động chỉ hiện với vai trò có quyền xem — Quản lý chỉ thấy nhật ký các thao tác của chính mình." />
      </div>
    );
  }

  return (
    <div className="gh-card sys-log">
      <div className="sys-log__head">
        <div>
          <div className="gh-card__title">Nhật ký hành động</div>
          <div className="gh-card__kicker">Ai đã làm gì, lúc nào — giữ lâu dài, không sửa được. Vai trò Kiểm soát xem được nhưng không thao tác.</div>
        </div>
        <div className="sys-log__tools">
          <div className="sys-log__search">
            <Icon name="ph ph-magnifying-glass" size={14} color="var(--color-neutral-700)" />
            <input
              className="sys-log__search-input"
              placeholder="Tìm người, hành động…"
              aria-label="Tìm trong nhật ký"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <select className="sys-log__actor" aria-label="Lọc theo loại tác nhân" value={actorType} onChange={(e) => setActorType(e.target.value)}>
            {ACTOR_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          {canExport ? (
            <button
              type="button"
              className="gh-btn gh-btn--secondary btn-30"
              onClick={onExport}
              disabled={exportCsv.isPending}
              title="Xuất theo loại tác nhân và khoảng ngày đang chọn (đủ cả dòng hệ thống, không theo ô tìm)"
            >
              <Icon name="ph ph-download-simple" size={14} />
              {exportCsv.isPending ? 'Đang xuất…' : 'Xuất CSV'}
            </button>
          ) : null}
        </div>
      </div>

      <div className="sys-log__filters">
        <label className="sys-log__field">
          Từ ngày
          <input type="date" className="sys-log__actor" aria-label="Từ ngày" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
        </label>
        <label className="sys-log__field">
          Đến hết ngày
          <input type="date" className="sys-log__actor" aria-label="Đến hết ngày" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </label>
        {from || to ? (
          <button
            type="button"
            className="gh-btn gh-btn--ghost btn-30"
            onClick={() => {
              setFrom('');
              setTo('');
            }}
          >
            Bỏ lọc ngày
          </button>
        ) : null}
        <label className="sys-log__field sys-log__toggle">
          <input type="checkbox" checked={showSystem} onChange={(e) => setShowSystem(e.target.checked)} />
          Hiện cả dòng hệ thống (thao tác chung, kiểm tra nhật ký hằng đêm)
        </label>
      </div>

      {q.isPending ? (
        <SkeletonLines rows={6} padding="10px 16px" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon="ph ph-list-checks"
          title={filtering ? 'Không có hành động nào khớp bộ lọc' : 'Chưa có hành động nào'}
          description={!showSystem ? 'Các dòng hệ thống đang ẩn — bật "Hiện cả dòng hệ thống" nếu Sếp muốn xem cả chúng.' : undefined}
        />
      ) : (
        <div className="gh-table-scroll">
          <table className="sys-log__table">
            <thead>
              <tr>
                <th>Thời điểm</th>
                <th>Ai / agent nào</th>
                <th>Hành động</th>
                <th>Đối tượng</th>
                <th>Tự trị</th>
                <th>Kết quả</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((a) => {
                const r = auditResultView(a.result);
                return (
                  <tr key={a.id}>
                    <td className="mono sys-log__time">{fmtDMClock(a.at, tz)}</td>
                    <td style={{ color: auditActorTone(a.actor_type) }}>{actorLabel(a)}</td>
                    <td className="sys-log__action" title={a.action}>
                      {actionLabel(a.action, a.detail)}
                    </td>
                    <td className="sys-log__target" title={targetText(a)}>
                      {targetText(a)}
                    </td>
                    <td className="mono" style={{ color: N4 }}>
                      {a.autonomy_level != null ? `mức ${a.autonomy_level}` : '—'}
                    </td>
                    <td>
                      <span className="sys-log__result" style={{ color: r.tone, borderColor: r.tone }}>
                        {r.label}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {q.isSuccess && rows.length > 0 ? (
        <div className="sys-log__more">
          <span>
            {q.hasNextPage ? `Đang hiện ${rows.length} dòng mới nhất` : `Đã hiện hết — ${rows.length} dòng`}
          </span>
          {q.hasNextPage ? (
            <button type="button" className="gh-btn gh-btn--secondary btn-30" onClick={() => void q.fetchNextPage()} disabled={q.isFetchingNextPage}>
              {q.isFetchingNextPage ? 'Đang tải…' : 'Xem thêm'}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
