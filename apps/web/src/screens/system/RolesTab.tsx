import { useState } from 'react';
import type { PermScope } from '@gen-harness/contracts';
import { STATIC_HARD_BOUNDARIES } from '@gen-harness/contracts';
import { EmptyState, Icon, Switch, TextField } from '@gen-harness/ui';
import { fmtInt } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { errorText } from '../../lib/errorText';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { BAD, N5, OK, WARN } from '../data/dataModel';
import { BOUNDARY_ICON, GROUP_KIND_LABEL, LISTEN_MODES, ROLE_LABEL, SCOPE_CELL, VIEW_SCOPES, boundaryTone, cellLocked, listenTone, scopeOptionsFor } from './systemModel';
import { useBoundaries, useListeningGroups, usePatchBoundary, usePatchPermission, usePermissions } from './queries';

/** Quyền hạn — ma trận vai trò, nhóm đang lắng nghe, ranh giới có trách nhiệm (PLAN 4.5). */
export function RolesTab() {
  const canRead = useCan('system.read');
  if (!canRead) {
    return (
      <div className="gh-card">
        <EmptyState
          icon="ph ph-lock-simple"
          title="Vai trò của bạn không xem được Quyền hạn"
          description="Ma trận quyền, nhóm lắng nghe và ranh giới chỉ hiện với vai trò có quyền xem hệ thống."
        />
      </div>
    );
  }
  return (
    <div className="sys-tabs-col">
      <PermissionMatrix />
      <div className="sys-grid2">
        <ListeningGroupsPanel />
        <BoundariesPanel />
      </div>
    </div>
  );
}

/** Từ ngắn nói quyền con trong một cột ("xem", "sửa", "duyệt"…) — để cảnh báo cột đang lệch mức nói rõ quyền nào khác. */
const CODE_WORD: Record<string, string> = {
  'overview.read': 'xem',
  'queue.read': 'xem',
  'queue.act': 'xử lý',
  'profile.read': 'xem',
  'profile.write': 'sửa',
  'people_review.read': 'xem',
  'people_review.write': 'sửa',
  'care.read': 'xem chăm sóc',
  'opportunity.read': 'xem',
  'opportunity.write': 'sửa',
  'action.draft': 'soạn',
  'action.approve': 'duyệt',
  'audit.read': 'xem',
};
/** Quyền "duyệt & gửi ra ngoài": ô riêng, không đổi theo ô "soạn nháp" (cho soạn ≠ cho duyệt). */
const APPROVE_CODE = 'action.approve';
const SPLIT_LABEL: Record<string, string> = { 'action.draft': 'Soạn nháp', [APPROVE_CODE]: 'Duyệt & gửi ra ngoài' };
const SCOPE_WORD: Record<PermScope, string> = { all: 'tất cả', team: 'việc được giao', assigned: 'khách được phân', none: 'không' };
const MIXED = 'mixed';
const MIXED_HINT = 'Các quyền trong cột đang khác mức — chọn một mức để đặt đồng loạt.';

/** Trạng thái một ô (vai trò × cột): các mã quyền SỬA ĐƯỢC của cột (bỏ mã khoá cứng Owner/Auditor) và mức chung của chúng.
 *  Ô là của CẢ CỘT: chọn mức nào thì mọi mã sửa được trong cột đổi theo, để dấu "Không được" nghĩa là không còn quyền nào. */
function columnCell(roleCode: string, perms: Record<string, PermScope>, codes: string[]) {
  const editable = codes.filter((c) => !cellLocked(roleCode, c));
  const scopes = editable.map((c) => perms[c] ?? 'none');
  const mixed = new Set(scopes).size > 1;
  return {
    editable,
    mixed,
    // Ô khoá cứng (mọi mã đều khoá) vẫn hiện mức của mã đầu như trước.
    scope: (editable.length ? scopes[0] : (perms[codes[0]] ?? 'none')) as PermScope,
    detail: editable.map((c) => `${CODE_WORD[c] ?? c} ${SCOPE_WORD[perms[c] ?? 'none']}`).join(' · '),
  };
}

function PermissionMatrix() {
  const canManage = useCan('roles.manage');
  const q = usePermissions();
  const patch = usePatchPermission();
  const [busyCell, setBusyCell] = useState<string | null>(null);

  // Đổi cả cột: gửi PATCH cho TỪNG mã quyền sửa được của cột (máy chủ chỉ đổi đúng một mã mỗi lần gọi). Lỗi giữa chừng thì
  // dừng, tải lại bảng để ô nói đúng cái đang có (ô lệch mức sẽ hiện "Khác nhau" kèm chi tiết).
  const onChange = async (role: string, key: string, codes: string[], current: Record<string, PermScope>, scope: PermScope) => {
    setBusyCell(key);
    try {
      for (const permission of codes) {
        if (cellLocked(role, permission) || (current[permission] ?? 'none') === scope) continue;
        await patch.mutateAsync({ role: role as 'owner' | 'manager' | 'operator' | 'agent_staff' | 'auditor', permission, scope });
      }
    } catch {
      void q.refetch();
    } finally {
      setBusyCell((k) => (k === key ? null : k));
    }
  };

  return (
    <Panel
      title="Ma trận quyền theo vai trò"
      kicker="Dữ liệu đánh giá nhân sự khoá chặt hơn dữ liệu cơ hội · đổi quyền cần mã PIN"
      label="Ma trận quyền theo vai trò"
      bodyClass="roles-matrix-wrap"
    >
      {q.isPending ? (
        <SkeletonLines rows={5} padding="10px 16px" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <>
          <div className="roles-legend">
            {(['all', 'team', 'none'] as PermScope[]).map((s) => (
              <span className="roles-legend__item" key={s}>
                <Icon name={SCOPE_CELL[s].icon} size={13} color={SCOPE_CELL[s].tone} />
                {s === 'all' ? 'tất cả dữ liệu' : s === 'team' ? 'có giới hạn (việc / khách được giao)' : 'không được'}
              </span>
            ))}
          </div>
          <table className="roles-matrix">
            <thead>
              <tr>
                <th>Vai trò</th>
                {q.data.columns.map((c) => (
                  <th key={c.key} className="roles-matrix__col">
                    {c.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {q.data.roles.map((role) => (
                <tr key={role.code}>
                  <td>
                    <div className="roles-matrix__role">{ROLE_LABEL[role.code]?.name ?? role.name}</div>
                    <div className="roles-matrix__meta">{ROLE_LABEL[role.code]?.meta ?? role.meta}</div>
                  </td>
                  {q.data.columns.map((c) => {
                    // Mỗi cột có thể gồm nhiều quyền (vd Nhân sự = xem + sửa + chăm sóc) — ô là của CẢ CỘT: hiện mức chung,
                    // lệch mức thì hiện "Khác nhau"; chọn mức mới là đổi mọi mã sửa được trong cột. NGOẠI LỆ: cột có quyền
                    // "duyệt & gửi ra ngoài" tách thành ô riêng cho từng quyền, để cho phép SOẠN nháp không kéo theo cho phép DUYỆT.
                    const groups: string[][] = c.permissions.includes(APPROVE_CODE) ? c.permissions.map((p) => [p]) : [c.permissions];
                    const split = groups.length > 1;
                    const roleName = ROLE_LABEL[role.code]?.name ?? role.name;
                    return (
                      <td key={c.key} className="roles-matrix__cell" data-label={c.label}>
                        {groups.map((codes) => {
                          const cell0 = columnCell(role.code, role.permissions, codes);
                          const locked = cell0.editable.length === 0;
                          const cell = SCOPE_CELL[cell0.scope];
                          const key = `${role.code}:${c.key}:${codes.join('+')}`;
                          const sub = split ? SPLIT_LABEL[codes[0]] ?? codes[0] : null;
                          const subTitle = split && codes[0] === APPROVE_CODE ? 'Người có quyền này duyệt được và gửi tin ra ngoài (Zalo, WhatsApp…)' : undefined;
                          const mixedNote = cell0.mixed ? (
                            <div className="roles-matrix__meta" style={{ color: WARN }} data-testid="roles-mixed" title={MIXED_HINT}>
                              {cell0.detail}
                            </div>
                          ) : null;
                          if (!canManage || locked) {
                            return (
                              <div key={key} data-locked={locked || undefined} style={locked ? { opacity: 0.75 } : undefined}>
                                {sub ? <div className="roles-matrix__meta" title={subTitle}>{sub}</div> : null}
                                <span className="roles-matrix__mark" title={locked ? `${cell.title} — cố định, không sửa được` : cell.title}>
                                  <Icon name={cell.icon} size={15} color={cell.tone} label={sub ? `${sub}: ${cell.title}` : cell.title} />
                                </span>
                                {mixedNote}
                              </div>
                            );
                          }
                          return (
                            <div key={key}>
                              {sub ? <div className="roles-matrix__meta" title={subTitle}>{sub}</div> : null}
                              <select
                                className="roles-matrix__select"
                                aria-label={sub ? `${roleName} · ${c.label} · ${sub}` : `${roleName} · ${c.label}`}
                                value={cell0.mixed ? MIXED : cell0.scope}
                                disabled={busyCell === key}
                                onChange={(e) => void onChange(role.code, key, codes, role.permissions, e.target.value as PermScope)}
                              >
                                {cell0.mixed ? (
                                  <option value={MIXED} disabled>
                                    Khác nhau
                                  </option>
                                ) : null}
                                {scopeOptionsFor(cell0.editable[0], cell0.mixed ? 'all' : cell0.scope).map((o) => (
                                  <option key={o.value} value={o.value} disabled={o.disabled}>
                                    {o.label}
                                  </option>
                                ))}
                              </select>
                              {mixedNote}
                            </div>
                          );
                        })}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      {patch.isError ? <InlineError>{errorText(patch.error)}</InlineError> : null}
    </Panel>
  );
}

function ListeningGroupsPanel() {
  const groups = useListeningGroups();
  return (
    <Panel title="Nhóm đang lắng nghe" kicker="Chỉ nhóm tổ chức có quyền hợp lệ" label="Nhóm đang lắng nghe" bodyClass="roles-groups-wrap">
      {groups.isPending ? (
        <SkeletonLines rows={4} padding="10px 16px" />
      ) : groups.isError ? (
        <CardError error={groups.error} onRetry={() => void groups.refetch()} retrying={groups.isFetching} />
      ) : groups.data.length === 0 ? (
        <EmptyState icon="ph ph-users-three" title="Chưa có nhóm nào đang lắng nghe" />
      ) : (
        <table className="roles-groups">
          <thead>
            <tr>
              <th>Nhóm</th>
              <th>Chế độ nghe</th>
              <th>Quyền xem</th>
            </tr>
          </thead>
          <tbody>
            {groups.data.map((g) => (
              <tr key={g.id}>
                <td>
                  <div className="roles-groups__name">{g.name}</div>
                  <div className="roles-groups__meta">
                    {fmtInt(g.members)} thành viên · {GROUP_KIND_LABEL[g.kind as keyof typeof GROUP_KIND_LABEL] ?? g.kind}
                  </div>
                </td>
                <td>
                  <span className="roles-groups__mode" style={{ color: listenTone(g.listen_mode) }}>
                    {LISTEN_MODES.find((m) => m.value === g.listen_mode)?.label ?? g.listen_mode}
                  </span>
                </td>
                <td>
                  <span className="roles-groups__scope">{VIEW_SCOPES.find((v) => v.value === g.view_scope)?.label ?? g.view_scope}</span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  );
}

function BoundariesPanel() {
  const canManage = useCan('system.manage');
  const q = useBoundaries();
  const patch = usePatchBoundary();
  const [threshold, setThreshold] = useState<string | null>(null);

  const gate = q.data?.find((b) => b.code === 'approval_gate');
  const thresholdValue = threshold ?? (gate?.params.approval_threshold_vnd != null ? String(gate.params.approval_threshold_vnd) : '');

  const saveThreshold = () => {
    const n = Number(threshold);
    if (threshold === null || !Number.isInteger(n) || n < 0) return;
    patch.mutate({ code: 'approval_gate', body: { params: { approval_threshold_vnd: n } } }, { onSuccess: () => setThreshold(null) });
  };

  return (
    <Panel
      title="Ranh giới có trách nhiệm"
      kicker="Hệ thống quan sát, không phải máy kết án — 8 giới hạn không tắt được"
      label="Ranh giới có trách nhiệm"
      bodyClass="roles-boundaries"
    >
      {q.isPending ? (
        <SkeletonLines rows={6} padding="10px 16px" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : (
        <>
          {q.data.map((b) => (
            <div className="boundary-row" key={b.code}>
              <Icon name={BOUNDARY_ICON[b.code] ?? 'ph ph-shield'} size={15} color={boundaryTone(b)} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="boundary-row__label">{b.label}</div>
                {b.locked ? <div className="boundary-row__hint">Cố định — không ai bật/tắt được, kể cả Owner.</div> : null}
                {b.code === 'approval_gate' ? (
                  <div className="boundary-row__threshold">
                    <TextField
                      label="Ngưỡng chờ duyệt (₫)"
                      type="number"
                      min={0}
                      step={1000000}
                      value={thresholdValue}
                      disabled={!canManage}
                      hint={thresholdValue && Number.isFinite(Number(thresholdValue)) ? `= ${Number(thresholdValue).toLocaleString('vi-VN')} ₫` : undefined}
                      onChange={(e) => setThreshold(e.target.value)}
                      onBlur={saveThreshold}
                    />
                  </div>
                ) : null}
              </div>
              {canManage ? (
                <Switch
                  checked={b.enabled}
                  label={`${b.enabled ? 'Tắt' : 'Bật'} ${b.label}`}
                  disabled={b.locked || patch.isPending}
                  onChange={(v) => patch.mutate({ code: b.code, body: { enabled: v } })}
                />
              ) : (
                <span className="boundary-row__state" style={{ color: b.enabled ? OK : N5 }}>
                  {b.enabled ? 'Đang bật' : 'Đang tắt'}
                </span>
              )}
            </div>
          ))}
          {STATIC_HARD_BOUNDARIES.map((b) => (
            <div className="boundary-row" key={b.label} data-static="">
              <Icon name="ph ph-lock-simple" size={15} color={BAD} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="boundary-row__label">{b.label}</div>
                <div className="boundary-row__hint">{b.hint}</div>
              </div>
              <span className="boundary-row__state" style={{ color: WARN }}>
                Không có công tắc
              </span>
            </div>
          ))}
        </>
      )}
      {patch.isError ? <InlineError>{errorText(patch.error)}</InlineError> : null}
    </Panel>
  );
}
