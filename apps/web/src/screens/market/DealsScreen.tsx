import { useMemo, useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import type { CaseItem, CasePriority, CaseStatus, Deal, DealStatus, PersonRef } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Tabs, type FilterOption, type TabItem } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { fmtDM, fmtInt } from '../../lib/format';
import { TRUNCATED_USERS_TEXT, useAssignees, useEmptyUsersText } from '../../lib/pickers';
import { useCan } from '../../lib/permissions';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, ScreenHead, SkeletonLines } from '../common';
import {
  CASE_PRIORITY_LABEL,
  CASE_STATUS_LABEL,
  DEAL_STATUS_LABEL,
  caseStatusTone,
  dealStatusTone,
  fmtVnd,
  priorityTone,
} from './marketModel';
import { useCases, useDeals, useOpportunities, usePatchCase, usePatchDeal } from './queries';

type DealsTab = 'deals' | 'cases';

export function DealsScreen() {
  const [tab, setTab] = useUrlState<DealsTab>('dtab', 'deals');
  const items: TabItem<DealsTab>[] = [
    { key: 'deals', label: 'Deal' },
    { key: 'cases', label: 'Vụ việc' },
  ];
  return (
    <div className="screen">
      <ScreenHead
        title="Deal & Vụ việc"
        description="Deal đã chốt theo giai đoạn thắng/thua, và vụ việc khiếu nại đang cần người xử lý — cùng dữ liệu cơ hội & thị trường, cùng ngôn ngữ thiết kế với phần còn lại của cụm."
        maxWidth={760}
        actions={<Tabs items={items} value={tab} onChange={setTab} label="Tab Deal & Vụ việc" idPrefix="deals-tab" />}
      />
      {tab === 'deals' ? <DealsPane /> : <CasesPane />}
    </div>
  );
}

const DEAL_STATUS_OPTIONS: FilterOption<string>[] = [{ value: '', label: 'Tất cả' }, ...(['open', 'won', 'lost'] as DealStatus[]).map((v) => ({ value: v, label: DEAL_STATUS_LABEL[v] }))];

function DealsPane() {
  const [status, setStatus] = useUrlState<string>('dstatus', '');
  const deals = useDeals({ status: (status || undefined) as DealStatus | undefined, limit: 100 });
  const patch = usePatchDeal();
  const [creating, setCreating] = useState(false);

  return (
    <div className="deals-pane">
      <div className="dir-filters">
        <FilterRow label="Trạng thái" value={status} onChange={setStatus} options={DEAL_STATUS_OPTIONS} />
        <span className="dir-filters__spacer" />
        <div className="dir-filters__actions">
          {deals.data ? <span className="raw-count">{fmtInt(deals.data.total)} deal</span> : null}
          <Button variant="primary" icon="ph ph-plus" onClick={() => setCreating(true)}>
            Tạo deal
          </Button>
        </div>
      </div>

      <div className="table-card">
        {deals.isPending ? (
          <SkeletonLines rows={5} />
        ) : deals.isError ? (
          <CardError error={deals.error} onRetry={() => void deals.refetch()} retrying={deals.isFetching} />
        ) : deals.data.items.length === 0 ? (
          <EmptyState icon="ph ph-handshake" title="Chưa có deal nào" />
        ) : (
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Deal">
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Mã</th>
                  <th>Người</th>
                  <th style={{ width: 150 }}>Số tiền</th>
                  <th style={{ width: 210 }}>Trạng thái</th>
                  <th style={{ width: 130 }}>Ngày tạo</th>
                </tr>
              </thead>
              <tbody>
                {deals.data.items.map((d) => (
                  <DealRow key={d.id} d={d} busy={patch.isPending} onChange={(s) => patch.mutate({ id: d.id, body: { status: s } })} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {patch.isError ? <InlineError>{errorText(patch.error)}</InlineError> : null}
      </div>

      {creating ? <CreateDealDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function DealRow({ d, busy, onChange }: { d: Deal; busy: boolean; onChange: (s: DealStatus) => void }) {
  return (
    <tr>
      <td className="td-id">{d.code}</td>
      <td>
        <div className="dir-person-cell__name">{d.person?.name ?? '—'}</div>
        {d.person?.org_name ? <div className="dir-person-cell__org">{d.person.org_name}</div> : null}
      </td>
      <td className="td-id">{fmtVnd(d.amount_vnd)}</td>
      <td>
        <div className="dir-filter-row__opts" role="group" aria-label={`Trạng thái deal ${d.code}`}>
          {(['open', 'won', 'lost'] as DealStatus[]).map((s) => (
            <button key={s} type="button" className="dir-filter-row__opt" aria-pressed={d.status === s} disabled={busy} onClick={() => onChange(s)} style={d.status === s ? { color: dealStatusTone(s), borderColor: dealStatusTone(s) } : undefined}>
              {DEAL_STATUS_LABEL[s]}
            </button>
          ))}
        </div>
      </td>
      <td className="td-id">{fmtDM(d.created_at)}</td>
    </tr>
  );
}

const CASE_STATUS_OPTIONS: FilterOption<string>[] = [{ value: '', label: 'Tất cả' }, ...(['open', 'in_progress', 'resolved', 'closed'] as CaseStatus[]).map((v) => ({ value: v, label: CASE_STATUS_LABEL[v] }))];

function CasesPane() {
  const [status, setStatus] = useUrlState<string>('cstatus', '');
  const cases = useCases({ status: (status || undefined) as CaseStatus | undefined, limit: 100 });
  const patch = usePatchCase();
  const [assigneeFor, setAssigneeFor] = useState<CaseItem | null>(null);
  const [creating, setCreating] = useState(false);
  // Gán người xử lý cần opportunity.write — không có quyền thì không hiện nút "Đổi" (mở ra chỉ gặp 403).
  const canWrite = useCan('opportunity.write');

  return (
    <div className="deals-pane">
      <div className="dir-filters">
        <FilterRow label="Trạng thái" value={status} onChange={setStatus} options={CASE_STATUS_OPTIONS} />
        <span className="dir-filters__spacer" />
        <div className="dir-filters__actions">
          {cases.data ? <span className="raw-count">{fmtInt(cases.data.total)} vụ việc</span> : null}
          <Button variant="primary" icon="ph ph-plus" onClick={() => setCreating(true)}>
            Tạo vụ việc
          </Button>
        </div>
      </div>

      <div className="table-card">
        {cases.isPending ? (
          <SkeletonLines rows={5} />
        ) : cases.isError ? (
          <CardError error={cases.error} onRetry={() => void cases.refetch()} retrying={cases.isFetching} />
        ) : cases.data.items.length === 0 ? (
          <EmptyState icon="ph ph-warning" title="Chưa có vụ việc nào" />
        ) : (
          <div className="gh-table-scroll">
            <table className="gh-table w920" aria-label="Vụ việc">
              <thead>
                <tr>
                  <th style={{ width: 110 }}>Mã</th>
                  <th style={{ width: 70 }}>Ưu tiên</th>
                  <th>Tiêu đề</th>
                  <th style={{ width: 170 }}>Trạng thái</th>
                  <th style={{ width: 170 }}>Người xử lý</th>
                </tr>
              </thead>
              <tbody>
                {cases.data.items.map((c) => (
                  <tr key={c.id}>
                    <td className="td-id">{c.code}</td>
                    <td>
                      <span style={{ color: priorityTone(c.priority), fontFamily: 'var(--font-mono)', fontSize: 11 }}>{CASE_PRIORITY_LABEL[c.priority]}</span>
                    </td>
                    <td>
                      <div style={{ fontSize: 12.5 }}>{c.title}</div>
                      {c.subject ? <div className="dir-person-cell__org">{c.subject.name}</div> : null}
                    </td>
                    <td>
                      <div className="dir-filter-row__opts" role="group" aria-label={`Trạng thái vụ việc ${c.code}`}>
                        {(['open', 'in_progress', 'resolved', 'closed'] as CaseStatus[]).map((s) => (
                          <button
                            key={s}
                            type="button"
                            className="dir-filter-row__opt"
                            aria-pressed={c.status === s}
                            disabled={patch.isPending}
                            onClick={() => patch.mutate({ id: c.id, body: { status: s } })}
                            style={c.status === s ? { color: caseStatusTone(s), borderColor: caseStatusTone(s) } : undefined}
                          >
                            {CASE_STATUS_LABEL[s]}
                          </button>
                        ))}
                      </div>
                    </td>
                    <td>
                      <div className="dir-bot-cell">
                        <span className="dir-bot-cell__name">{c.assignee ? c.assignee.name : 'Chưa gán'}</span>
                        {canWrite ? (
                          <Button variant="ghost" size="sm" onClick={() => setAssigneeFor(c)}>
                            Đổi
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {patch.isError ? <InlineError>{errorText(patch.error)}</InlineError> : null}
      </div>

      {assigneeFor ? <AssigneeDialog c={assigneeFor} onClose={() => setAssigneeFor(null)} /> : null}
      {creating ? <CreateCaseDialog onClose={() => setCreating(false)} /> : null}
    </div>
  );
}

function AssigneeDialog({ c, onClose }: { c: CaseItem; onClose: () => void }) {
  const patch = usePatchCase();
  const { options, hasOthers, truncated, query: people } = useAssignees();
  const emptyText = useEmptyUsersText();
  // Người đang được gán nhưng không còn trong /pickers/users (đã khoá/xoá) — vẫn hiện, đánh dấu chọn (như AgentChoices).
  const missing = c.assignee && people.isSuccess && !options.some((u) => u.id === c.assignee?.id) ? c.assignee : null;
  return (
    <Dialog
      open
      onClose={onClose}
      width={360}
      title="Gán người xử lý"
      kicker={c.title}
      actions={
        <Button variant="secondary" onClick={onClose}>
          Đóng
        </Button>
      }
    >
      <div className="dlg-list" role="list">
        <button type="button" className="sv-open" aria-pressed={!c.assignee} disabled={patch.isPending} onClick={() => patch.mutate({ id: c.id, body: { assignee_user_id: null } }, { onSuccess: onClose })}>
          <span className="sv-open__name">Chưa gán</span>
          {!c.assignee ? <Icon name="ph ph-check" size={14} /> : null}
        </button>
        {people.isPending ? (
          <SkeletonLines rows={3} />
        ) : people.isError ? (
          <InlineError>{errorText(people.error)}</InlineError>
        ) : (
          <>
            {missing ? (
              <button
                type="button"
                className="sv-open"
                aria-pressed
                disabled
                title={
                  truncated
                    ? 'Người này nằm ngoài 500 người đầu của danh sách (hoặc đã bị khoá/xoá)'
                    : 'Người này đã bị khoá hoặc xoá — chọn người khác để gán lại'
                }
              >
                {/* Danh sách bị cắt ở trần → không khẳng định là đã khoá/xoá. */}
                <span className="sv-open__name">{`${missing.name || 'Người dùng'} (${truncated ? 'ngoài danh sách' : 'đã khoá/xoá'})`}</span>
                <Icon name="ph ph-check" size={14} />
              </button>
            ) : null}
            {options.map((u) => (
              <button key={u.id} type="button" className="sv-open" aria-pressed={c.assignee?.id === u.id} disabled={patch.isPending} onClick={() => patch.mutate({ id: c.id, body: { assignee_user_id: u.id } }, { onSuccess: onClose })}>
                <span className="sv-open__name">{u.label}</span>
                {c.assignee?.id === u.id ? <Icon name="ph ph-check" size={14} /> : null}
              </button>
            ))}
            {!hasOthers ? <p className="muted-note">{emptyText}</p> : null}
            {truncated ? <p className="muted-note">{TRUNCATED_USERS_TEXT}</p> : null}
          </>
        )}
      </div>
      {patch.isError ? <InlineError>{errorText(patch.error)}</InlineError> : null}
    </Dialog>
  );
}

function useDistinctPersons(): PersonRef[] {
  const opps = useOpportunities();
  return useMemo(() => {
    const map = new Map<string, PersonRef>();
    for (const o of opps.data?.items ?? []) if (o.person) map.set(o.person.id, o.person);
    return Array.from(map.values());
  }, [opps.data]);
}

function CreateDealDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const persons = useDistinctPersons();
  const [personId, setPersonId] = useState('');
  const [amount, setAmount] = useState('');
  const [erp, setErp] = useState('');
  const create = useMutation({
    mutationFn: () => api.market.deals.create({ person_id: personId, amount_vnd: Number(amount), erp_ref: erp || undefined }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['market', 'deals'] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      width={400}
      title="Tạo deal"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-handshake" loading={create.isPending} disabled={!personId || !amount} onClick={() => create.mutate()}>
            Tạo
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <div className="dlg-list" role="list">
          {persons.length === 0 ? <EmptyState icon="ph ph-user" title="Chưa có người nào trong cơ hội để chọn" /> : null}
          {persons.map((p) => (
            <button key={p.id} type="button" className="sv-open" aria-pressed={personId === p.id} onClick={() => setPersonId(p.id)}>
              <span className="sv-open__name">{p.name}</span>
              {personId === p.id ? <Icon name="ph ph-check" size={14} /> : null}
            </button>
          ))}
        </div>
        <label className="gh-field">
          <span className="gh-field__label">Số tiền (₫)</span>
          <input className="gh-input" type="number" min={0} value={amount} onChange={(e) => setAmount(e.target.value)} />
        </label>
        <label className="gh-field">
          <span className="gh-field__label">Mã ERP (không bắt buộc)</span>
          <input className="gh-input" value={erp} onChange={(e) => setErp(e.target.value)} />
        </label>
        {create.isError ? <InlineError>{errorText(create.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}

function CreateCaseDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const persons = useDistinctPersons();
  const [title, setTitle] = useState('');
  const [priority, setPriority] = useState<CasePriority>('P2');
  const [subjectId, setSubjectId] = useState('');
  const create = useMutation({
    mutationFn: () =>
      api.market.cases.create({ title, priority, subject: subjectId ? { type: 'person', id: subjectId } : undefined }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['market', 'cases'] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title="Tạo vụ việc"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-warning" loading={create.isPending} disabled={!title.trim()} onClick={() => create.mutate()}>
            Tạo
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <label className="gh-field">
          <span className="gh-field__label">Tiêu đề</span>
          <input className="gh-input" value={title} onChange={(e) => setTitle(e.target.value)} />
        </label>
        <div className="dir-filter-row">
          <span className="dir-filter-row__label">Ưu tiên</span>
          <div className="dir-filter-row__opts" role="group" aria-label="Ưu tiên">
            {(['P1', 'P2', 'P3'] as CasePriority[]).map((p) => (
              <button key={p} type="button" className="dir-filter-row__opt" aria-pressed={priority === p} onClick={() => setPriority(p)}>
                {p}
              </button>
            ))}
          </div>
        </div>
        <div className="dlg-list" role="list">
          <button type="button" className="sv-open" aria-pressed={subjectId === ''} onClick={() => setSubjectId('')}>
            <span className="sv-open__name">Không gắn với ai</span>
          </button>
          {persons.map((p) => (
            <button key={p.id} type="button" className="sv-open" aria-pressed={subjectId === p.id} onClick={() => setSubjectId(p.id)}>
              <span className="sv-open__name">{p.name}</span>
              {subjectId === p.id ? <Icon name="ph ph-check" size={14} /> : null}
            </button>
          ))}
        </div>
        {create.isError ? <InlineError>{errorText(create.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}

function FilterRow({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: FilterOption<string>[] }) {
  return (
    <div className="dir-filter-row">
      <span className="dir-filter-row__label">{label}</span>
      <div className="dir-filter-row__opts" role="group" aria-label={label}>
        {options.map((o) => (
          <button key={o.value} type="button" className="dir-filter-row__opt" aria-pressed={o.value === value} onClick={() => onChange(o.value)}>
            {o.label}
          </button>
        ))}
      </div>
    </div>
  );
}
