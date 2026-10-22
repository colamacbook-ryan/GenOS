import { useMemo, useState } from 'react';
import type { AgentIdentity, AgentTemplate } from '@gen-harness/contracts';
import { AUTONOMY_DEFAULT_LEVEL, SCREEN_BY_KEY, autonomyChoice } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, SelectField, Switch, TextField } from '@gen-harness/ui';
import { useChannels } from '../../lib/dataQueries';
import { fmtDMClock } from '../../lib/format';
import { useCan } from '../../lib/permissions';
import { errorText } from '../../lib/errorText';
import { AutonomySelect } from '../AutonomySelect';
import { CardError, InlineError, Panel, PinHint, ScreenHead, SkeletonLines, StateChip } from '../common';
import {
  AUTONOMY_LEVELS,
  WARN,
  agentCreateBody,
  agentIcon,
  agentPatchBody,
  decisionTone,
  decisionWhat,
  lastSpoke,
  modelLine,
  scopeSummary,
} from './agentsModel';
import {
  useAgentDecisions,
  useAgentTemplates,
  useAgents,
  useArchiveAgent,
  useCloneAgent,
  useCreateAgent,
  useDiplomat,
  useSetDiplomat,
  useSetAgentEnabled,
  useUpdateAgent,
} from './queries';

export function AgentsScreen() {
  const meta = SCREEN_BY_KEY.agents;
  const canManage = useCan('system.manage');
  const agents = useAgents();
  const [editing, setEditing] = useState<{ agent: AgentIdentity | null; template: AgentTemplate | null } | null>(null);
  const [cloning, setCloning] = useState(false);
  const [archiving, setArchiving] = useState<AgentIdentity | null>(null);

  const actions = canManage ? (
    <>
      <Button variant="secondary" icon="ph ph-copy" className="btn-30" data-gen-target="agents.clone" onClick={() => setCloning(true)} disabled={!agents.data?.length}>
        Nhân bản
      </Button>
      <Button variant="primary" icon="ph ph-plus" className="btn-30" data-gen-target="agents.add" onClick={() => setEditing({ agent: null, template: null })}>
        Tạo agent mới
      </Button>
    </>
  ) : null;

  return (
    <div className="screen">
      <ScreenHead title={meta.title} description={meta.description} maxWidth={700} actions={actions} />

      <div className="ag-grid" role="list" aria-label="Danh sách agent" aria-busy={agents.isFetching || undefined} data-gen-target="agents.list">
        {agents.isPending ? (
          Array.from({ length: 3 }, (_, i) => <AgentCardSkeleton key={i} />)
        ) : agents.isError ? (
          <div className="gh-card">
            <CardError error={agents.error} onRetry={() => void agents.refetch()} retrying={agents.isFetching} />
          </div>
        ) : agents.data.length === 0 ? (
          <div className="gh-card">
            <EmptyState
              icon="ph ph-user-focus"
              title="Chưa có danh tính agent nào"
              description={canManage ? 'Tạo agent đầu tiên, hoặc dùng một mẫu có sẵn ở dưới.' : 'Owner chưa tạo danh tính agent nào.'}
            />
          </div>
        ) : (
          agents.data.map((a) => (
            <AgentCard
              key={a.id}
              agent={a}
              canManage={canManage}
              onEdit={() => setEditing({ agent: a, template: null })}
              onArchive={() => setArchiving(a)}
            />
          ))
        )}
      </div>

      <DiplomatPanel agents={agents.data ?? []} canManage={canManage} />

      <div className="ag-panels">
        <DecisionsPanel />
        <TemplatesPanel canManage={canManage} onUseTemplate={(t) => setEditing({ agent: null, template: t })} />
      </div>

      {editing ? <AgentFormDialog agent={editing.agent} template={editing.template} onClose={() => setEditing(null)} /> : null}
      {cloning ? <CloneAgentDialog agents={agents.data ?? []} onClose={() => setCloning(false)} /> : null}
      {archiving ? <ArchiveAgentDialog agent={archiving} onClose={() => setArchiving(null)} /> : null}
    </div>
  );
}

function AgentCard({
  agent,
  canManage,
  onEdit,
  onArchive,
}: {
  agent: AgentIdentity;
  canManage: boolean;
  onEdit: () => void;
  onArchive: () => void;
}) {
  const { icon, tone } = agentIcon(agent);
  const setEnabled = useSetAgentEnabled();
  const model = modelLine(agent);
  const spoke = lastSpoke(agent);
  const fields: Array<[string, string]> = [
    ['VAI TRÒ', agent.role_desc],
    ['GIỌNG', agent.voice],
    ['NÓI KHI', agent.speak_when],
    ['CẤM', agent.forbidden.length ? agent.forbidden.join(' · ') : 'Không khai báo'],
    ['KÊNH', scopeSummary(agent.channel_scopes)],
    ['MODEL', model.text],
  ];
  return (
    <article className="ag-card" role="listitem" data-off={agent.is_enabled ? undefined : ''} aria-label={agent.name}>
      <div className="ag-card__head">
        <div className="ag-card__icon" style={{ color: tone }}>
          <Icon name={icon} size={16} />
        </div>
        <div className="ag-card__title">
          <div className="ag-card__name">{agent.name}</div>
          <div className="ag-card__role" title={agent.role_desc}>{agent.role_desc}</div>
        </div>
        {canManage ? (
          <Switch
            checked={agent.is_enabled}
            label={`${agent.is_enabled ? 'Tắt' : 'Bật'} agent ${agent.name}`}
            disabled={setEnabled.isPending}
            onChange={(v) => setEnabled.mutate({ id: agent.id, enabled: v })}
          />
        ) : (
          <StateChip color={agent.is_enabled ? 'var(--color-ok)' : 'var(--color-neutral-500)'}>{agent.is_enabled ? 'Bật' : 'Tắt'}</StateChip>
        )}
      </div>
      <div className="ag-card__fields">
        {fields.map(([k, v]) => (
          <div className="ag-field-row" key={k}>
            <span className="ag-field-row__key">{k}</span>
            <span
              className="ag-field-row__val"
              style={k === 'MODEL' && !model.ok ? { color: WARN } : undefined}
              data-warn={k === 'MODEL' && !model.ok ? '' : undefined}
            >
              {v}
            </span>
          </div>
        ))}
      </div>
      {/* F089: công tắc lỗi (PIN khoá, mạng) phải nói được vì sao không đổi — không để công tắc lặng lẽ nhảy về cũ. */}
      {setEnabled.isError ? <InlineError>{errorText(setEnabled.error)}</InlineError> : null}
      <div className="ag-card__rule" aria-hidden />
      <div className="ag-card__foot">
        <span className="ag-autonomy" title={`Mức ${agent.autonomy_level} — ${AUTONOMY_LEVELS[agent.autonomy_level] ?? ''}`}>
          {autonomyChoice(agent.autonomy_level)?.label ?? `mức ${agent.autonomy_level}`}
        </span>
        <span className="ag-card__spoke" title={spoke}>{spoke}</span>
        <span className="ag-card__acts">
          {canManage ? (
            <Button variant="ghost" className="btn-22" onClick={onArchive} aria-label={`Lưu trữ agent ${agent.name}`}>
              Lưu trữ
            </Button>
          ) : null}
          <Button variant="ghost" className="btn-22" onClick={onEdit} aria-label={`${canManage ? 'Sửa' : 'Xem'} agent ${agent.name}`}>
            {canManage ? 'Sửa' : 'Xem'}
          </Button>
        </span>
      </div>
    </article>
  );
}

function AgentCardSkeleton() {
  return (
    <div className="ag-card" aria-hidden>
      <SkeletonLines rows={2} padding="0" />
    </div>
  );
}

function DecisionsPanel() {
  const [full, setFull] = useState(false);
  const decisions = useAgentDecisions(undefined, undefined, full ? 50 : 8);
  return (
    <Panel
      genTarget="agents.decisions"
      title="Agent đã nói gì, nhân danh gì"
      kicker="Mọi phát ngôn đều ghi lại danh tính đứng tên"
      aside={
        <Button variant="secondary" className="btn-27" onClick={() => setFull((v) => !v)}>
          {full ? 'Thu gọn' : 'Xem toàn bộ'}
        </Button>
      }
      bodyClass="ag-log"
      label="Agent đã nói gì, nhân danh gì"
    >
      {decisions.isPending ? (
        <SkeletonLines rows={4} padding="10px 16px" />
      ) : decisions.isError ? (
        <CardError error={decisions.error} onRetry={() => void decisions.refetch()} retrying={decisions.isFetching} />
      ) : decisions.data.items.length === 0 ? (
        <EmptyState icon="ph ph-chat-circle-dots" title="Chưa có quyết định nào được ghi lại" />
      ) : (
        decisions.data.items.map((d) => (
          <div className="ag-log-row" key={d.id}>
            <span className="ag-log-row__time">{fmtDMClock(d.at)}</span>
            <span className="ag-log-row__agent">{d.agent.name}</span>
            <span className="ag-log-row__what" style={{ color: decisionTone(d) }} title={decisionWhat(d)}>
              {decisionWhat(d)}
            </span>
            {/* F075: câu agent đã gợi ý / soạn — trước đây lưu nhưng không màn nào đọc được. */}
            {d.proposal_text ? (
              <span className="ag-log-row__proposal" data-testid="agent-proposal">
                {d.decision === 'suggest' ? 'Gợi ý: ' : d.decision === 'note' ? 'Ghi chú: ' : 'Nội dung soạn: '}
                {d.proposal_text}
              </span>
            ) : null}
          </div>
        ))
      )}
    </Panel>
  );
}

function TemplatesPanel({ canManage, onUseTemplate }: { canManage: boolean; onUseTemplate: (t: AgentTemplate) => void }) {
  const templates = useAgentTemplates();
  return (
    <Panel genTarget="agents.templates" title="Mẫu có sẵn" kicker="Mẫu tuỳ chọn · không phải bản sắc hệ thống" bodyClass="ag-templates" label="Mẫu có sẵn">
      {templates.isPending ? (
        <SkeletonLines rows={4} padding="8px 16px" />
      ) : templates.isError ? (
        <CardError error={templates.error} onRetry={() => void templates.refetch()} retrying={templates.isFetching} />
      ) : (
        templates.data.map((t) => (
          <div className="ag-template-row" key={t.code}>
            <Icon name="ph ph-stack" size={15} />
            <div className="ag-template-row__body">
              <div className="ag-template-row__name" style={{ color: t.default_enabled ? undefined : 'var(--color-neutral-500)' }}>
                {t.name}
              </div>
              <div className="ag-template-row__note" title={t.role_desc}>{t.role_desc}</div>
            </div>
            {canManage ? (
              <Button variant="ghost" className="btn-22" onClick={() => onUseTemplate(t)}>
                Dùng mẫu
              </Button>
            ) : null}
          </div>
        ))
      )}
    </Panel>
  );
}

/** v0.1.45 (F-20): máy chủ đòi phiên PIN `policy.change` khi rào chắn của agent đổi — hộp PIN tự mở khi gặp 423.
 *  Giới hạn (limits) không sửa ở form này nên không nhắc. Tạo / nhân bản agent luôn cần PIN `agent.manage`. */
const AGENT_PIN_TEXT = 'Đổi mức tự trị, điều cấm hay phạm vi kênh cần mã PIN';
const AGENT_CREATE_PIN_TEXT = 'Tạo agent cần mã PIN';
const AGENT_CLONE_PIN_TEXT = 'Nhân bản agent cần mã PIN';
const AGENT_ARCHIVE_PIN_TEXT = 'Lưu trữ agent cần mã PIN';

function AgentFormDialog({ agent, template, onClose }: { agent: AgentIdentity | null; template: AgentTemplate | null; onClose: () => void }) {
  const create = useCreateAgent();
  const update = useUpdateAgent();
  const channels = useChannels();
  // F077: vai không có system.manage (vd. Kiểm soát) chỉ XEM danh tính agent — không có nút Lưu, các ô bị khoá.
  const canManage = useCan('system.manage');
  const readOnly = !!agent && !canManage;
  const [name, setName] = useState(agent?.name ?? template?.name ?? '');
  const [roleDesc, setRoleDesc] = useState(agent?.role_desc ?? template?.role_desc ?? '');
  const [voice, setVoice] = useState(agent?.voice ?? template?.voice ?? '');
  const [speakWhen, setSpeakWhen] = useState(agent?.speak_when ?? template?.speak_when ?? '');
  const [forbidden, setForbidden] = useState((agent?.forbidden ?? template?.forbidden ?? []).join('\n'));
  // F-30: `null` = giữ nguyên mức đang lưu — sửa agent không gửi lại autonomy_level nếu Sếp không chọn mức khác.
  const [picked, setPicked] = useState<number | null>(null);
  const [scopeIds, setScopeIds] = useState<Set<string>>(new Set(agent?.channel_scopes.map((s) => s.channel_id) ?? []));
  const installed = (channels.data ?? []).filter((c) => c.installed && c.id);

  const mutation = agent ? update : create;
  const submit = () => {
    if (readOnly || !name.trim() || !roleDesc.trim() || !voice.trim() || !speakWhen.trim()) return;
    if (agent) {
      // v0.1.45 (F-20): chỉ gửi rào chắn (mức tự trị / điều cấm / phạm vi kênh) khi đổi — sửa tên không hỏi PIN.
      const patch = agentPatchBody(agent, { name, roleDesc, voice, speakWhen, forbidden, picked, scopeIds });
      update.mutate({ id: agent.id, body: patch }, { onSuccess: onClose });
      return;
    }
    // v0.1.67: chưa chọn mức ⇒ mức mặc định chung (4) đang hiện sáng ở ô chọn (F078); mẫu tắt sẵn ⇒ agent tắt (F082).
    create.mutate(agentCreateBody({ name, roleDesc, voice, speakWhen, forbidden, picked, scopeIds }, template), { onSuccess: onClose });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={480}
      title={readOnly ? `Xem ${agent.name}` : agent ? `Sửa ${agent.name}` : 'Tạo agent mới'}
      kicker={
        readOnly
          ? 'Chỉ xem — chỉ Owner sửa được danh tính agent'
          : template && !agent
            ? `Điền sẵn từ mẫu ${template.name} — không bắt buộc, chỉnh tự do${template.default_enabled ? '' : '. Mẫu này tạo ra agent đang TẮT — bật ở thẻ khi sẵn sàng'}`
            : 'Danh tính do Sếp tự định nghĩa'
      }
      actions={
        readOnly ? (
          <Button variant="secondary" onClick={onClose}>
            Đóng
          </Button>
        ) : (
          <>
            <Button variant="secondary" onClick={onClose}>
              Huỷ
            </Button>
            <Button variant="primary" loading={mutation.isPending} disabled={!name.trim() || !roleDesc.trim() || !voice.trim() || !speakWhen.trim()} onClick={submit}>
              {agent ? 'Lưu thay đổi' : 'Tạo agent'}
            </Button>
          </>
        )
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <TextField label="Tên hiển thị" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} disabled={readOnly} />
        <TextField label="Vai trò" value={roleDesc} onChange={(e) => setRoleDesc(e.target.value)} maxLength={500} disabled={readOnly} />
        <TextField label="Giọng điệu" value={voice} onChange={(e) => setVoice(e.target.value)} maxLength={200} disabled={readOnly} />
        <TextField label="Khi nào được nói" value={speakWhen} onChange={(e) => setSpeakWhen(e.target.value)} maxLength={500} disabled={readOnly} />
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="ag-forbidden">
            Cấm làm gì (mỗi dòng một điều)
          </label>
          <textarea id="ag-forbidden" className="gh-input" rows={2} value={forbidden} onChange={(e) => setForbidden(e.target.value)} disabled={readOnly} />
        </div>
        {readOnly ? null : agent ? <PinHint text={AGENT_PIN_TEXT} title={AGENT_PIN_TEXT} /> : <PinHint text={AGENT_CREATE_PIN_TEXT} title={AGENT_CREATE_PIN_TEXT} />}
        {/* Tạo mới: current = mức mặc định thật (4) để ô chọn sáng đúng mức sẽ áp dụng; bấm "Chỉ ghi nhận" ghi mức 0. */}
        {readOnly ? (
          <p className="muted-note">Mức tự trị: {autonomyChoice(agent.autonomy_level)?.label ?? `mức ${agent.autonomy_level}`}</p>
        ) : (
          <AutonomySelect current={agent ? agent.autonomy_level : AUTONOMY_DEFAULT_LEVEL} value={picked} onChange={setPicked} />
        )}
        <fieldset className="ag-scope-fields">
          <legend className="gh-field__label">Phạm vi kênh được xuất hiện</legend>
          {installed.length === 0 ? (
            <p className="muted-note">Chưa có kênh nào cài đặt.</p>
          ) : (
            installed.map((c) => (
              <label className="ag-scope-check" key={c.id}>
                <input
                  type="checkbox"
                  checked={scopeIds.has(c.id as string)}
                  disabled={readOnly}
                  onChange={(e) => {
                    const next = new Set(scopeIds);
                    if (e.target.checked) next.add(c.id as string);
                    else next.delete(c.id as string);
                    setScopeIds(next);
                  }}
                />
                {c.name}
              </label>
            ))
          )}
        </fieldset>
        {mutation.isError ? <InlineError>{errorText(mutation.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}

function CloneAgentDialog({ agents, onClose }: { agents: AgentIdentity[]; onClose: () => void }) {
  const clone = useCloneAgent();
  const [sourceId, setSourceId] = useState(agents[0]?.id ?? '');
  const [name, setName] = useState('');
  const [copyScopes, setCopyScopes] = useState(true);
  const source = useMemo(() => agents.find((a) => a.id === sourceId) ?? null, [agents, sourceId]);
  const submit = () => {
    if (!sourceId || !name.trim()) return;
    clone.mutate({ id: sourceId, body: { name: name.trim(), copy_channel_scopes: copyScopes } }, { onSuccess: onClose });
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title="Nhân bản agent"
      kicker="Bản sao tạo ở trạng thái tắt — Owner rà lại trước khi bật"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={clone.isPending} disabled={!sourceId || !name.trim()} onClick={submit}>
            Nhân bản
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <SelectField
          label="Nhân bản từ"
          value={sourceId}
          onChange={(e) => setSourceId(e.target.value)}
          options={agents.map((a) => ({ value: a.id, label: a.name }))}
        />
        <TextField label="Tên agent mới" value={name} onChange={(e) => setName(e.target.value)} placeholder={source ? `${source.name} (bản sao)` : ''} maxLength={120} />
        <label className="ag-scope-check">
          <input type="checkbox" checked={copyScopes} onChange={(e) => setCopyScopes(e.target.checked)} />
          Sao chép luôn phạm vi kênh
        </label>
        <PinHint text={AGENT_CLONE_PIN_TEXT} title={AGENT_CLONE_PIN_TEXT} />
        {clone.isError ? <InlineError>{errorText(clone.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}

function ArchiveAgentDialog({ agent, onClose }: { agent: AgentIdentity; onClose: () => void }) {
  const archive = useArchiveAgent();
  return (
    <Dialog
      open
      onClose={onClose}
      width={420}
      title={`Lưu trữ ${agent.name}?`}
      kicker="Agent biến khỏi danh sách và ngừng làm việc; lịch sử quyết định của agent vẫn còn"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={archive.isPending} onClick={() => archive.mutate(agent.id, { onSuccess: onClose })}>
            Lưu trữ agent
          </Button>
        </>
      }
    >
      <div className="dlg-fields">
        <p className="muted-note">
          Model riêng của agent (nếu có) được gỡ. Tên “{agent.name}” dùng lại được cho agent mới. Muốn chỉ dừng tạm thì tắt công tắc ở thẻ.
        </p>
        <PinHint text={AGENT_ARCHIVE_PIN_TEXT} title={AGENT_ARCHIVE_PIN_TEXT} />
        {archive.isError ? <InlineError>{errorText(archive.error)}</InlineError> : null}
      </div>
    </Dialog>
  );
}

const DIPLOMAT_PIN_TEXT = 'Đổi Agent ngoại giao cần mã PIN';
const DIPLOMAT_NONE = '';

/** v0.1.73: Gen chỉ nói trong Console; mọi kênh ngoài (Zalo/WhatsApp quản trị, Telegram, nhóm, Chế độ hỗ trợ) nói bằng agent này. */
export function DiplomatPanel({ agents, canManage }: { agents: AgentIdentity[]; canManage: boolean }) {
  const cur = useDiplomat();
  const save = useSetDiplomat();
  const [pick, setPick] = useState<string | null>(null);
  const value = pick ?? cur.data?.agent_id ?? DIPLOMAT_NONE;
  const options = [
    { value: DIPLOMAT_NONE, label: 'Chưa chọn — bot xưng “trợ lý”' },
    ...agents.filter((a) => a.is_enabled).map((a) => ({ value: a.id, label: a.name })),
  ];
  const dirty = value !== (cur.data?.agent_id ?? DIPLOMAT_NONE);
  return (
    <div style={{ marginTop: 14 }}>
      <Panel genTarget="agents.diplomat" title="Agent ngoại giao" kicker="Gen chỉ trả lời trong Console. Zalo/WhatsApp quản trị, Telegram, trả lời trong nhóm và Chế độ hỗ trợ nói bằng agent này (tên, giọng, vai); luật an toàn và bộ lọc không đổi.">
        {cur.isError ? (
          <CardError error={cur.error} onRetry={() => void cur.refetch()} retrying={cur.isFetching} />
        ) : (
          <div className="dlg-fields">
            <SelectField
              label="Agent ngoại giao"
              value={value}
              options={options}
              disabled={!canManage || cur.isPending}
              onChange={(e) => setPick(e.target.value)}
            />
            {canManage ? (
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Button variant="primary" className="btn-30" disabled={!dirty} loading={save.isPending} onClick={() => save.mutate(value || null, { onSuccess: () => setPick(null) })}>
                  Lưu
                </Button>
                <PinHint text={DIPLOMAT_PIN_TEXT} title={DIPLOMAT_PIN_TEXT} />
              </div>
            ) : null}
            {save.isError ? <InlineError>{errorText(save.error)}</InlineError> : null}
          </div>
        )}
      </Panel>
    </div>
  );
}
