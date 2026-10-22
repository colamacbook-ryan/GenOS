import { useMemo, useState, type ChangeEvent } from 'react';
import type { PluginItem, PluginLocalInstallBody } from '@gen-harness/contracts';
import { SCREEN_BY_KEY } from '@gen-harness/contracts';
import { Button, Dialog, EmptyState, Icon, Switch, Tabs, type TabItem } from '@gen-harness/ui';
import { errorText } from '../../lib/errorText';
import { useCan } from '../../lib/permissions';
import { toast } from '../../lib/toast';
import { useUrlState } from '../../lib/uiStore';
import { CardError, InlineError, Panel, ScreenHead, SkeletonLines, StateChip } from '../common';
import {
  BREAKER_LABEL,
  HEALTH_LABEL,
  N5,
  OK,
  ORIGIN_LABEL,
  breakerTone,
  disableBlockReason,
  healthTone,
  removeBlockReason,
  sha256Hex,
} from './pluginsModel';
import { usePluginLogs, usePlugins, useInstallLocalPlugin, useRemovePlugin, useResetBreaker, useTogglePlugin } from './queries';

type Tab = 'core' | 'addon';

const MANIFEST_TEMPLATE = `{
  "package": "@ext/vi-du",
  "name": "Plugin ví dụ",
  "version": "1.0.0",
  "layer": "extension",
  "description": "Mô tả ngắn về việc plugin làm",
  "permissions": ["read:clean", "write:notes"]
}`;

export function PluginsScreen() {
  const meta = SCREEN_BY_KEY.plugins;
  const canManage = useCan('system.manage');
  const q = usePlugins();
  const [tab, setTab] = useUrlState<Tab>('tab', 'core');
  const [installing, setInstalling] = useState(false);
  const [logsFor, setLogsFor] = useState<PluginItem | null>(null);

  const list = q.data ?? [];
  const coreList = list.filter((p) => p.origin === 'core');
  const addonList = list.filter((p) => p.origin !== 'core');
  const shown = tab === 'core' ? coreList : addonList;

  const degradedCount = list.filter((p) => p.health === 'degraded').length;
  const isolatedCount = list.filter((p) => p.health === 'isolated' || p.breaker.state === 'open').length;
  const addonEnabled = addonList.filter((p) => p.enabled).length;
  const kpis = [
    { label: 'Plugin nền DSH', value: String(coreList.length), sub: 'Khung gầm phụ thuộc, luôn bật' },
    { label: 'Plugin cài thêm', value: String(addonList.length), sub: `${addonEnabled} đang bật · ${addonList.length - addonEnabled} tắt` },
    { label: 'Đang suy giảm', value: String(degradedCount), sub: degradedCount ? 'Xem cột Sức khoẻ' : 'Không có plugin nào suy giảm' },
    { label: 'Bị cách ly', value: String(isolatedCount), sub: isolatedCount ? 'Reset breaker để thử lại' : 'Không có plugin nào bị ngắt hẳn' },
  ];

  const tabs: TabItem<Tab>[] = [
    { key: 'core', label: 'Plugin nền DSH', count: coreList.length },
    { key: 'addon', label: 'Plugin cài thêm', count: addonList.length },
  ];

  return (
    <div className="screen">
      <ScreenHead
        title={meta.title}
        description={meta.description}
        maxWidth={meta.descMaxWidth}
        actions={
          canManage ? (
            <Button variant="primary" icon="ph ph-upload-simple" className="btn-30" onClick={() => setInstalling(true)}>
              Nạp plugin từ tệp
            </Button>
          ) : undefined
        }
      />

      <div className="plg-kpis" aria-label="Chỉ số plugin">
        {q.isPending
          ? Array.from({ length: 4 }, (_, i) => <div className="plg-kpi" key={i}><SkeletonLines rows={1} padding="14px 16px" /></div>)
          : kpis.map((k) => (
              <div className="plg-kpi" key={k.label}>
                <div className="plg-kpi__label">{k.label}</div>
                <div className="plg-kpi__value">{k.value}</div>
                <div className="plg-kpi__sub">{k.sub}</div>
              </div>
            ))}
      </div>

      <Tabs items={tabs} value={tab} onChange={setTab} label="Tab plugin" idPrefix="plg-tab" />

      <Panel title={tab === 'core' ? 'Plugin nền DSH — nạp theo thứ tự phụ thuộc' : 'Plugin cài thêm'} bodyClass="plg-table-wrap" label="Danh sách plugin">
        {q.isPending ? (
          <SkeletonLines rows={5} padding="10px 16px" />
        ) : q.isError ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : shown.length === 0 ? (
          <EmptyState icon="ph ph-puzzle-piece" title="Chưa có plugin nào ở nhóm này" />
        ) : (
          <table className="plg-table">
            <thead>
              <tr>
                <th>Plugin</th>
                <th>Layer</th>
                <th>Nguồn</th>
                <th>Phiên bản</th>
                <th>Nạp thứ</th>
                <th>Sức khoẻ</th>
                <th>Breaker</th>
                <th>Bật/tắt</th>
                <th>Nhật ký</th>
                <th>Gỡ</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <PluginRow key={p.package} p={p} all={list} canManage={canManage} onLogs={() => setLogsFor(p)} />
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      {installing ? <InstallLocalDialog onClose={() => setInstalling(false)} /> : null}
      {logsFor ? <LogsDialog plugin={logsFor} onClose={() => setLogsFor(null)} /> : null}
    </div>
  );
}

function PluginRow({ p, all, canManage, onLogs }: { p: PluginItem; all: PluginItem[]; canManage: boolean; onLogs: () => void }) {
  const toggle = useTogglePlugin();
  const remove = useRemovePlugin();
  const resetBreaker = useResetBreaker();
  const [confirmRemove, setConfirmRemove] = useState(false);

  const blockDisable = disableBlockReason(p, all);
  const blockRemove = removeBlockReason(p);
  const busy = (toggle.isPending && toggle.variables?.pkg === p.package) || (remove.isPending && remove.variables === p.package);

  return (
    <tr data-package={p.package} data-health={p.health}>
      <td>
        <div className="plg-table__name">{p.name}</div>
        <div className="plg-table__pkg mono">{p.package} · v{p.version}</div>
      </td>
      <td className="mono">{p.layer}</td>
      <td>{ORIGIN_LABEL[p.origin]}</td>
      <td className="mono">{p.version}</td>
      <td className="mono">{p.load_order ?? '—'}</td>
      <td>
        <StateChip color={healthTone(p.health)} dot>
          {HEALTH_LABEL[p.health]}
        </StateChip>
      </td>
      <td>
        <div className="plg-breaker">
          <StateChip color={breakerTone(p.breaker.state)}>{BREAKER_LABEL[p.breaker.state]}</StateChip>
          {p.breaker.state !== 'closed' && canManage ? (
            <Button variant="ghost" className="btn-22" icon="ph ph-arrow-counter-clockwise" loading={resetBreaker.isPending && resetBreaker.variables === p.package} onClick={() => resetBreaker.mutate(p.package)}>
              Reset
            </Button>
          ) : null}
        </div>
        {p.breaker.last_error ? <div className="plg-breaker__err">{p.breaker.last_error}</div> : null}
      </td>
      <td>
        {canManage ? (
          <div className="plg-toggle-cell">
            <Switch
              checked={p.enabled}
              locked={!!blockDisable}
              disabled={busy}
              label={`${p.enabled ? 'Tắt' : 'Bật'} ${p.name}`}
              onChange={(v) => toggle.mutate({ pkg: p.package, enabled: v }, { onError: (e) => toast(errorText(e), 'bad') })}
            />
            {blockDisable ? (
              <span className="plg-lock-note">
                <Icon name="ph ph-lock-simple" size={11} /> {blockDisable}
              </span>
            ) : null}
          </div>
        ) : (
          <StateChip color={p.enabled ? OK : N5}>{p.enabled ? 'Đang bật' : 'Đang tắt'}</StateChip>
        )}
      </td>
      <td>
        <Button variant="ghost" className="btn-22" icon="ph ph-list-magnifying-glass" onClick={onLogs}>
          Xem
        </Button>
      </td>
      <td>
        {blockRemove ? (
          <span className="plg-lock-note" title={blockRemove}>
            <Icon name="ph ph-lock-simple" size={11} /> Không gỡ được
          </span>
        ) : canManage ? (
          <Button variant="ghost" className="btn-22" icon="ph ph-trash" loading={remove.isPending && remove.variables === p.package} onClick={() => setConfirmRemove(true)}>
            Gỡ
          </Button>
        ) : null}
      </td>
      {confirmRemove ? (
        <RemoveConfirmDialog
          plugin={p}
          onClose={() => setConfirmRemove(false)}
          onConfirm={() =>
            remove.mutate(p.package, {
              onSuccess: () => setConfirmRemove(false),
              onError: (e) => toast(errorText(e), 'bad'),
            })
          }
          pending={remove.isPending}
          error={remove.isError ? remove.error : null}
        />
      ) : null}
    </tr>
  );
}

function RemoveConfirmDialog({
  plugin,
  onClose,
  onConfirm,
  pending,
  error,
}: {
  plugin: PluginItem;
  onClose: () => void;
  onConfirm: () => void;
  pending: boolean;
  error: unknown;
}) {
  return (
    <Dialog
      open
      onClose={onClose}
      width={380}
      title={`Gỡ ${plugin.name}?`}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={pending} onClick={onConfirm}>
            Gỡ plugin
          </Button>
        </>
      }
    >
      <p className="muted-note">Gỡ khỏi hệ thống, dừng mọi consumer đang chạy. Có thể nạp lại sau nếu cần.</p>
      {error ? <InlineError>{errorText(error)}</InlineError> : null}
    </Dialog>
  );
}

function LogsDialog({ plugin, onClose }: { plugin: PluginItem; onClose: () => void }) {
  const logs = usePluginLogs(plugin.package);
  return (
    <Dialog open onClose={onClose} width={520} title={`Nhật ký · ${plugin.name}`} kicker="Cập nhật LIVE khi có sự kiện mới">
      {logs.isPending ? (
        <SkeletonLines rows={4} padding="0" />
      ) : logs.isError ? (
        <CardError error={logs.error} onRetry={() => void logs.refetch()} retrying={logs.isFetching} />
      ) : logs.data.items.length === 0 ? (
        <EmptyState icon="ph ph-notebook" title="Chưa có nhật ký nào" />
      ) : (
        <ul className="plg-log-list">
          {logs.data.items.map((l) => (
            <li key={l.id} className="plg-log-row">
              <span className="plg-log-row__level" data-level={l.level.toLowerCase()}>
                {l.level}
              </span>
              <span className="plg-log-row__msg">{l.message}</span>
              <span className="plg-log-row__time mono">{new Date(l.at).toLocaleTimeString('vi-VN')}</span>
            </li>
          ))}
        </ul>
      )}
    </Dialog>
  );
}

function InstallLocalDialog({ onClose }: { onClose: () => void }) {
  const install = useInstallLocalPlugin();
  const [fileName, setFileName] = useState<string | null>(null);
  const [codeSha, setCodeSha] = useState('');
  const [manifestText, setManifestText] = useState(MANIFEST_TEMPLATE);
  const [signature, setSignature] = useState('');

  const manifest = useMemo(() => {
    try {
      const v = JSON.parse(manifestText) as Record<string, unknown>;
      return v && typeof v === 'object' ? v : null;
    } catch {
      return null;
    }
  }, [manifestText]);
  const permissions = Array.isArray(manifest?.permissions) ? (manifest?.permissions as unknown[]).filter((x): x is string => typeof x === 'string') : [];
  const shaValid = /^[0-9a-f]{64}$/i.test(codeSha);
  const canSubmit = !!manifest && shaValid && signature.trim().length > 0;

  async function onPickFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setFileName(file.name);
    try {
      const buf = await file.arrayBuffer();
      setCodeSha(await sha256Hex(buf));
    } catch {
      toast('Không tính được sha256 từ tệp này — nhập tay ở ô bên dưới', 'warn');
    }
  }

  function submit() {
    if (!manifest || !canSubmit) return;
    const body: PluginLocalInstallBody = { manifest, code_sha256: codeSha.toLowerCase(), signature: signature.trim() };
    install.mutate(body, { onSuccess: () => { toast(`Đã nạp ${String(manifest.name ?? manifest.package)} — chờ Sếp bật`, 'ok'); onClose(); } });
  }

  return (
    <Dialog
      open
      onClose={onClose}
      width={480}
      title="Nạp plugin từ tệp"
      kicker="Kiểm chữ ký ed25519 trước khi lưu — chưa chạy mã, chưa tự bật"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" loading={install.isPending} disabled={!canSubmit} onClick={submit}>
            Nạp plugin
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
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="plg-file">
            Tệp mã plugin (tính sha256 tự động)
          </label>
          <input id="plg-file" type="file" onChange={(e) => void onPickFile(e)} />
          {fileName ? <div className="plg-file-hint mono">{fileName} → {codeSha}</div> : null}
        </div>
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="plg-sha">
            sha256 mã nguồn (hex 64 ký tự — tự điền khi chọn tệp, sửa được)
          </label>
          <input id="plg-sha" className="gh-input mono" value={codeSha} onChange={(e) => setCodeSha(e.target.value.trim())} spellCheck={false} />
        </div>
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="plg-manifest">
            Manifest (JSON)
          </label>
          <textarea id="plg-manifest" className="gh-input mono" rows={7} value={manifestText} onChange={(e) => setManifestText(e.target.value)} spellCheck={false} />
          {!manifest ? <InlineError>JSON chưa hợp lệ</InlineError> : null}
        </div>
        {manifest ? (
          <div className="plg-perms">
            <span className="plg-perms__label">Quyền xin:</span>
            {permissions.length === 0 ? <span className="muted-note">Không xin quyền nào</span> : permissions.map((perm) => <span className="plg-perm-chip" key={perm}>{perm}</span>)}
          </div>
        ) : null}
        <div className="gh-field">
          <label className="gh-field__label" htmlFor="plg-sig">
            Chữ ký (base64, ed25519 — do người phát triển ký)
          </label>
          <input id="plg-sig" className="gh-input mono" value={signature} onChange={(e) => setSignature(e.target.value)} spellCheck={false} autoComplete="off" />
        </div>
        {install.isError ? <InlineError>{errorText(install.error)}</InlineError> : null}
      </form>
    </Dialog>
  );
}
