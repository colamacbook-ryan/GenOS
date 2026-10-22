import { useEffect, useMemo } from 'react';
import { Link } from 'react-router-dom';
import type { ChannelType, DirRelation, GraphHeatBand, GraphState, GraphValueBand, PersonType } from '@gen-harness/contracts';
import { EmptyState, FilterSelect, Icon } from '@gen-harness/ui';
import { Bar, CardError, SkeletonLines } from '../common';
import { fmtAgo, fmtInt } from '../../lib/format';
import { useAssignees } from '../../lib/pickers';
import { useUrlState } from '../../lib/uiStore';
import {
  CHANNEL_OPTIONS,
  HEAT_OPTIONS,
  N4,
  PERSON_TYPE_LABEL,
  RELATION_LABEL,
  RELATION_OPTIONS,
  STATE_OPTIONS,
  TYPE_OPTIONS,
  VALUE_OPTIONS,
  channelIcon,
  channelTone,
  heatTone,
  initialsOf,
  potentialTone,
  riskTone,
} from './graphModel';
import { useGraphList } from './queries';

/** owner_user_id phải là UUID — link/bookmark cũ `?owner=u-ha` (danh sách cứng trước v0.1.35) bị bỏ qua thay vì 422. */
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function ListPane() {
  const [type, setType] = useUrlState<string>('type', '');
  const [channel, setChannel] = useUrlState<string>('channel', '');
  const [heat, setHeat] = useUrlState<string>('heat', '');
  const [potential, setPotential] = useUrlState<string>('pot', '');
  const [risk, setRisk] = useUrlState<string>('risk', '');
  const [owner, setOwner] = useUrlState<string>('owner', '');
  const [state, setState] = useUrlState<string>('state', '');
  const [relation, setRelation] = useUrlState<string>('rel', '');
  const ownerId = UUID_RE.test(owner) ? owner : '';
  useEffect(() => {
    if (owner && !ownerId) setOwner('');
  }, [owner, ownerId, setOwner]);

  const query = useMemo(
    () => ({
      type: (type || undefined) as PersonType | undefined,
      channel: (channel || undefined) as ChannelType | undefined,
      heat: (heat || undefined) as GraphHeatBand | undefined,
      potential: (potential || undefined) as GraphValueBand | undefined,
      risk: (risk || undefined) as GraphValueBand | undefined,
      owner_user_id: ownerId || undefined,
      state: (state || undefined) as GraphState | undefined,
      relation: (relation || undefined) as DirRelation | undefined,
      limit: 100,
    }),
    [type, channel, heat, potential, risk, ownerId, state, relation],
  );
  const list = useGraphList(query);
  // Người phụ trách THẬT (v0.1.35) — 403/lỗi/đang tải → chỉ còn 'Tất cả', không chặn màn.
  const { options: assignees } = useAssignees();
  const ownerOptions = useMemo(
    () => [{ value: '', label: 'Tất cả' }, ...assignees.map((u) => ({ value: u.id, label: u.label }))],
    [assignees],
  );

  return (
    <div className="gp-list">
      <div className="gp-filters">
        <FilterSelect label="Loại" value={type} onChange={setType} options={TYPE_OPTIONS} />
        <FilterSelect label="Kênh" value={channel} onChange={setChannel} options={CHANNEL_OPTIONS} />
        <FilterSelect label="Độ nóng" value={heat} onChange={setHeat} options={HEAT_OPTIONS} />
        <FilterSelect label="Tiềm năng" value={potential} onChange={setPotential} options={VALUE_OPTIONS} />
        <FilterSelect label="Rủi ro" value={risk} onChange={setRisk} options={VALUE_OPTIONS} />
        <FilterSelect label="Phụ trách" value={owner} onChange={setOwner} options={ownerOptions} />
        <FilterSelect label="Chạm gần nhất" value={state} onChange={setState} options={STATE_OPTIONS} />
        <FilterSelect label="Giai đoạn" value={relation} onChange={setRelation} options={RELATION_OPTIONS} />
        <span className="gp-filters__spacer" />
        {list.data ? <span className="raw-count">{fmtInt(list.data.total)} hồ sơ khớp</span> : null}
      </div>

      <div className="table-card">
        {list.isPending ? (
          <SkeletonLines rows={7} />
        ) : list.isError ? (
          <CardError error={list.error} onRetry={() => void list.refetch()} retrying={list.isFetching} />
        ) : list.data.items.length === 0 ? (
          <EmptyState icon="ph ph-graph" title="Không có hồ sơ nào khớp bộ lọc" description="Nới bộ lọc, hoặc bấm Dựng lại đồ thị nếu dữ liệu vừa mới nạp." />
        ) : (
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Danh sách quan hệ">
              <thead>
                <tr>
                  <th>Đối tượng</th>
                  <th style={{ width: 100 }}>Loại</th>
                  <th style={{ width: 150 }}>Giai đoạn</th>
                  <th style={{ width: 70 }}>Kênh</th>
                  <th style={{ width: 110 }}>Độ nóng</th>
                  <th style={{ width: 100 }}>Tiềm năng</th>
                  <th style={{ width: 90 }}>Rủi ro</th>
                  <th style={{ width: 130 }}>Chạm gần nhất</th>
                  <th style={{ width: 130 }}>Tải quan hệ</th>
                  <th style={{ width: 90 }}>Cầu nối</th>
                </tr>
              </thead>
              <tbody>
                {list.data.items.map((p) => (
                  <tr key={p.id}>
                    <td>
                      <div className="gp-person-cell">
                        <span className="gp-person-cell__av">{initialsOf(p.name)}</span>
                        <div style={{ minWidth: 0 }}>
                          <Link to={`/profile?id=${encodeURIComponent(p.id)}`} className="gp-person-cell__name">
                            {p.name}
                          </Link>
                          {p.org_name ? <div className="gp-person-cell__org">{p.org_name}</div> : null}
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="mono-tag">{p.type ? (PERSON_TYPE_LABEL[p.type] ?? p.type) : '—'}</span>
                    </td>
                    {/* Nhãn giai đoạn một dòng: bảng min 920px mà tổng cột cố định đã ~970px ⇒ cột này bị ép còn ~26px, chữ xếp
                        dọc từng tiếng ("Trực/tiếp/với/Sếp") ở 1024/390px. nowrap ⇒ bảng tự rộng ra, vùng .gh-table-scroll cuộn. */}
                    <td style={{ fontSize: 12, color: 'var(--color-neutral-300)', whiteSpace: 'nowrap' }}>{RELATION_LABEL[p.relation] ?? p.relation}</td>
                    <td>
                      <span className="gp-ch-icons">
                        {p.channels.map((c) => (
                          <Icon key={c} name={channelIcon(c)} size={13} color={channelTone(c)} label={c} />
                        ))}
                      </span>
                    </td>
                    <td>
                      {p.heat !== null ? (
                        <>
                          <Bar pct={p.heat} tone={heatTone(p.heat)} width={56} />
                          <span style={{ marginLeft: 8, color: heatTone(p.heat), fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{fmtInt(p.heat)}</span>
                        </>
                      ) : (
                        <span className="td-id">—</span>
                      )}
                    </td>
                    <td className="td-id" style={{ color: potentialTone(p.potential) }}>
                      {p.potential !== null ? fmtInt(p.potential) : '—'}
                    </td>
                    <td className="td-id" style={{ color: riskTone(p.risk) }}>
                      {p.risk !== null ? fmtInt(p.risk) : '—'}
                    </td>
                    <td className="td-id" style={{ color: p.state === 'cold' ? N4 : undefined }}>
                      {fmtAgo(p.last_interaction_at)}
                    </td>
                    <td className="td-id">
                      {fmtInt(p.degree)} cạnh · {p.total_weight.toFixed(1)}
                    </td>
                    <td className="td-id" style={{ color: p.bridge_score > 0 ? 'var(--color-accent-300)' : undefined }}>
                      {p.bridge_score > 0 ? p.bridge_score.toFixed(1) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
