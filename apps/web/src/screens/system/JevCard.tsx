import { useState } from 'react';
import type { JevBenchmark, JevEnableBody, Provider } from '@gen-harness/contracts';
import { Button, Icon, SelectField, Switch, TextField } from '@gen-harness/ui';
import { useProviders } from '../../lib/dataQueries';
import { errorDetail, errorText } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useCan, useOrgTimezone } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { useCreateProvider, useTestProvider } from '../api/queries';
import { providerStatus } from '../api/apiModel';
import { FriendlyErrorText, InlineError, Panel, PinHint, SkeletonLines } from '../common';
import { useSetTriageSettings, useTriageSettings } from '../queue/queries';
import { useEnableJev, useJevBenchmark, useJevValueSummary, useSkippedItems } from './jevQueries';
import {
  JEV_PRESET,
  JEV_PRIVACY_WARNING,
  benchmarkSummary,
  findOpenRouterSource,
  isBenchmark,
  isSkippedPage,
  isValueSummary,
  jevKeyValid,
  skippedLinkLabel,
  skippedReasonText,
  valueSummaryText,
} from './jevModel';
import { prefilterOn } from './triageModel';

const JEV_BASE_URLS = [
  { value: 'https://openrouter.ai/api/v1', label: 'OpenRouter — openrouter.ai/api/v1' },
  { value: 'https://api.typesafe.ai', label: 'TypeSafe API — api.typesafe.ai' },
];

/**
 * Jev (TypeSafe System One) — nguồn model mới kind `system_one` (v0.1.21, quyết định §9.6): mô hình quyết định nhanh
 * Gen dùng để chọn ý định và bước UI kế tiếp (trần ~1,5 s, lỗi/chậm thì rơi về model lớn). Không nằm trong chuỗi
 * sinh chữ. Khoá lưu như khoá nhà cung cấp khác (mã hoá phong bì).
 *
 * v0.1.55 (G4): "Bật Jev 1 chạm" (preset điền sẵn, dùng lại khoá OpenRouter đang có HOẶC dán khoá — máy chủ chép khoá
 * đã mã hoá, không trả khoá về trình duyệt); cảnh báo QD-12 một dòng; "Thử 12 câu mẫu" (chỉ Owner); số đo giá trị;
 * công tắc "Lọc trước khi trích xuất"; link "Tin đã bỏ qua (N)"; địa chỉ + model Jev nằm trong khối "Nâng cao".
 */
export function JevCard({ test: lifted, onFailed }: { test?: TestMutation; onFailed?: () => void } = {}) {
  const canManage = useCan('system.manage');
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const providers = useProviders();
  const jev = (providers.data ?? []).find((p) => p.kind === 'system_one');
  // Một mutation cho cả thẻ: "Lưu & kiểm tra" ở form, kết quả hiện tiếp ở phần trạng thái sau khi lưu.
  // v0.1.39: BrainTab có thể truyền mutation của nó vào để kết quả "Kiểm tra 1 lần" còn giữ khi thẻ chuyển sang "Nâng cao".
  const own = useTestProvider();
  const test = lifted ?? own;
  return (
    <Panel
      title="Jev — quyết định nhanh cho Gen"
      kicker="Giúp Gen chọn việc nhanh và rẻ hơn — không bắt buộc"
      label="Jev — quyết định nhanh cho Gen"
      genTarget="system.brain.jev"
      bodyClass="jev-body"
    >
      {providers.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : (
        <>
          {jev ? (
            <JevStatus p={jev} test={test} onFailed={onFailed} canEnable={isOwner} />
          ) : canManage ? (
            <JevOneTap providers={providers.data ?? []} test={test} />
          ) : (
            <p className="muted-note">Chưa cấu hình Jev — Gen dùng model lớn cho mọi quyết định.</p>
          )}
          <p className="jev-note" role="note" data-testid="jev-privacy-warning">
            <Icon name="ph ph-shield-warning" size={12} /> {JEV_PRIVACY_WARNING}
          </p>
          {isOwner && jev ? <JevBenchmarkBlock /> : null}
          {isOwner ? <JevValueLine /> : null}
          <PrefilterRow isOwner={isOwner} />
          <SkippedBlock />
          <details className="brain-advanced">
            <summary>Nâng cao — địa chỉ và model Jev</summary>
            {jev ? (
              <dl className="jev-dl">
                <dt>Địa chỉ</dt>
                <dd className="mono">{jev.endpoint}</dd>
                <dt>Model</dt>
                <dd className="mono">{jev.models[0]?.model_name ?? '—'}</dd>
              </dl>
            ) : canManage ? (
              <JevAdvancedForm test={test} />
            ) : (
              <p className="muted-note">Chưa cấu hình Jev.</p>
            )}
          </details>
        </>
      )}
    </Panel>
  );
}

export type TestMutation = ReturnType<typeof useTestProvider>;

function JevStatus({ p, test, onFailed, canEnable }: { p: Provider; test: TestMutation; onFailed?: () => void; canEnable: boolean }) {
  // v0.1.39 (F-78): Jev không bắt buộc — chỉ cần kiểm 1 lần; có kết quả trong phiên thì ẩn nút.
  const result = test.data && test.variables === p.id ? test.data : null;
  const status = providerStatus(p);
  const tone = status.tone;
  const enable = useEnableJev();
  return (
    <>
      <dl className="jev-dl">
        <dt>Khoá</dt>
        <dd className="mono">{p.keys.length ? p.keys.map((k) => `${k.label} ····${k.last4}`).join(' · ') : 'chưa có khoá'}</dd>
        <dt>Trạng thái</dt>
        <dd style={{ color: tone }}>{status.label}</dd>
      </dl>
      <div className="jev-actions">
        {result?.ok ? (
          <span className="jev-note" role="status">
            <Icon name="ph ph-check-circle" size={12} /> Đã kiểm tra — không cần kiểm thêm
          </span>
        ) : result ? (
          // Lỗi: không dùng dấu tích xanh (mâu thuẫn với ô lỗi đỏ ngay dưới) — Jev không bắt buộc nên bỏ qua được.
          <span className="jev-note" role="status">
            <Icon name="ph ph-info" size={12} /> Đã kiểm tra — Jev không bắt buộc, có thể bỏ qua
          </span>
        ) : (
          <Button
            variant="secondary"
            className="btn-27"
            icon="ph ph-pulse"
            data-gen-target="system.brain.jev.test"
            loading={test.isPending}
            onClick={() =>
              test.mutate(p.id, {
                onSuccess: (r) => {
                  if (!r.ok) onFailed?.();
                },
              })
            }
          >
            Kiểm tra 1 lần
          </Button>
        )}
        {canEnable && !p.enabled ? (
          <Button variant="secondary" className="btn-27" icon="ph ph-power" loading={enable.isPending} onClick={() => enable.mutate({ use_existing_openrouter: false })}>
            Bật lại Jev
          </Button>
        ) : null}
        <span className="jev-note">
          <Icon name="ph ph-info" size={12} /> Gen hỏi Jev trước (tối đa 1,5 giây); lỗi hoặc chậm thì dùng model lớn.
        </span>
      </div>
      {result ? (
        <div className={result.ok ? 'apm-test-result apm-test-result--ok' : 'apm-test-result apm-test-result--bad'} role="status">
          {result.ok ? `Jev trả lời được · ${result.latency_ms} ms` : <FriendlyErrorText raw={result.error} />}
        </div>
      ) : null}
      {test.isError ? <InlineError>{errorText(test.error)}</InlineError> : null}
      {enable.isError ? <InlineError detail={errorDetail(enable.error)}>{errorText(enable.error)}</InlineError> : null}
    </>
  );
}

/**
 * "Bật Jev 1 chạm": preset điền sẵn (OpenRouter + model Jev). Có nguồn OpenRouter thì một nút dùng lại khoá đó (máy chủ
 * chép khoá đã mã hoá — trình duyệt không bao giờ thấy khoá); không thì dán khoá. Vẫn cần mã PIN như việc tạo nguồn.
 */
function JevOneTap({ providers, test }: { providers: Provider[]; test: TestMutation }) {
  const enable = useEnableJev();
  const [key, setKey] = useState('');
  const openRouter = findOpenRouterSource(providers);
  const run = (body: JevEnableBody) =>
    enable.mutate(body, {
      onSuccess: (r) => {
        setKey('');
        test.mutate(r.provider_id);
      },
    });
  return (
    <form
      className="jev-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (jevKeyValid(key)) run({ use_existing_openrouter: false, key: key.trim() });
      }}
    >
      <strong>Bật Jev 1 chạm</strong>
      <p className="muted-note">
        Em điền sẵn {JEV_PRESET.endpointLabel} · <span className="mono">{JEV_PRESET.model}</span> — Sếp chỉ cần chọn khoá.
      </p>
      {openRouter ? (
        <div className="jev-actions">
          <Button
            variant="primary"
            className="btn-27"
            icon="ph ph-key"
            loading={enable.isPending && enable.variables?.use_existing_openrouter === true}
            disabled={enable.isPending}
            data-gen-target="system.brain.jev.enable"
            onClick={() => run({ use_existing_openrouter: true })}
          >
            Dùng khoá OpenRouter đang có
          </Button>
          <PinHint />
        </div>
      ) : null}
      <TextField
        label={openRouter ? 'Hoặc dán khoá OpenRouter khác' : 'Khoá OpenRouter'}
        type="password"
        autoComplete="off"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        placeholder="sk-or-…"
      />
      <div className="jev-actions">
        <Button variant={openRouter ? 'secondary' : 'primary'} type="submit" className="btn-27" icon="ph ph-lightning" disabled={!jevKeyValid(key) || enable.isPending} loading={enable.isPending && enable.variables?.use_existing_openrouter === false}>
          Bật Jev
        </Button>
        {!openRouter ? <PinHint /> : null}
      </div>
      {enable.isError ? <InlineError detail={errorDetail(enable.error)}>{errorText(enable.error)}</InlineError> : null}
    </form>
  );
}

/** Khối "Nâng cao" khi CHƯA có Jev: tự chọn địa chỉ + model (đi đường tạo nguồn như trước v0.1.55). */
function JevAdvancedForm({ test }: { test: TestMutation }) {
  const create = useCreateProvider();
  const [base, setBase] = useState(JEV_BASE_URLS[0].value);
  const [model, setModel] = useState(JEV_PRESET.model as string);
  const [key, setKey] = useState('');
  const valid = jevKeyValid(key) && model.trim().length > 0;
  const save = () => {
    if (!valid) return;
    create.mutate(
      { kind: 'system_one', name: 'Jev (System One)', endpoint: base, keys: [key.trim()], models: [model.trim()] },
      { onSuccess: (p) => test.mutate(p.id) },
    );
  };
  return (
    <form
      className="jev-form"
      onSubmit={(e) => {
        e.preventDefault();
        save();
      }}
    >
      <SelectField label="Địa chỉ gọi" value={base} onChange={(e) => setBase(e.target.value)} options={JEV_BASE_URLS} />
      <TextField label="Model" value={model} onChange={(e) => setModel(e.target.value)} className="mono" />
      <TextField label="Khoá API" type="password" autoComplete="off" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sk-or-…" />
      <div className="jev-actions">
        <Button variant="primary" type="submit" className="btn-27" icon="ph ph-floppy-disk" disabled={!valid} loading={create.isPending} data-gen-target="system.brain.jev.test">
          Lưu &amp; kiểm tra
        </Button>
        <PinHint />
      </div>
      {create.isError ? <InlineError detail={errorDetail(create.error)}>{errorText(create.error)}</InlineError> : null}
    </form>
  );
}

/** "Thử 12 câu mẫu" (chỉ Owner): 6 câu ý định + 6 câu lọc tin, tuần tự, mỗi câu tối đa 1,5 giây. */
function JevBenchmarkBlock() {
  const bench = useJevBenchmark();
  const data: JevBenchmark | null = isBenchmark(bench.data) ? bench.data : null;
  return (
    <div className="jev-bench" data-testid="jev-bench">
      <div className="jev-actions">
        <Button variant="secondary" className="btn-27" icon="ph ph-list-checks" loading={bench.isPending} onClick={() => bench.mutate()}>
          Thử 12 câu mẫu
        </Button>
        <span className="jev-note">
          <Icon name="ph ph-info" size={12} /> 6 câu hỏi của Gen + 6 câu lọc tin, chữ mẫu không chứa dữ liệu thật.
        </span>
      </div>
      {bench.isError ? <InlineError detail={errorDetail(bench.error)}>{errorText(bench.error)}</InlineError> : null}
      {bench.isSuccess && !data ? <InlineError>Chưa đọc được kết quả thử — thử lại sau.</InlineError> : null}
      {data ? (
        <>
          <p className="jev-note" role="status">
            {benchmarkSummary(data)}
          </p>
          <div className="apm-table-wrap">
            <table className="apm-table" style={{ minWidth: 0 }}>
              <thead>
                <tr>
                  <th scope="col">Câu mẫu</th>
                  <th scope="col">Kỳ vọng</th>
                  <th scope="col">Jev chọn</th>
                  <th scope="col">Kết quả</th>
                  <th scope="col">Độ trễ</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((it, i) => (
                  <tr key={`${i}-${it.question}`}>
                    <td>{it.question}</td>
                    <td>{it.expected}</td>
                    <td>
                      {it.got ?? '—'}
                      {it.error_text ? <div className="muted-note">{it.error_text}</div> : null}
                    </td>
                    <td>{it.ok ? 'Đúng' : 'Sai'}</td>
                    <td>{it.latency_ms} ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      ) : null}
    </div>
  );
}

/** Số đo 7 ngày — chỉ đếm số lần, không quy ra tiền. Máy chủ chưa có endpoint / trả lạ thì ẩn. */
function JevValueLine() {
  const q = useJevValueSummary(7, true);
  const line = isValueSummary(q.data) ? valueSummaryText(q.data, 7) : null;
  if (!line) return null;
  return (
    <p className="jev-note" role="status" data-testid="jev-value">
      <Icon name="ph ph-chart-line-up" size={12} /> {line}
    </p>
  );
}

/** Công tắc `triage.prefilter` — chỉ Owner đổi; vai khác chỉ xem. */
function PrefilterRow({ isOwner }: { isOwner: boolean }) {
  const settings = useTriageSettings();
  const save = useSetTriageSettings();
  if (!settings.data || typeof settings.data !== 'object' || Array.isArray(settings.data)) return null;
  return (
    <>
      <div className="triage-row">
        <Switch
          checked={prefilterOn(settings.data)}
          label="Lọc trước khi trích xuất"
          disabled={!isOwner || save.isPending}
          onChange={(v) => save.mutate({ prefilter: v })}
        />
        <span>Lọc trước khi trích xuất</span>
      </div>
      <p className="muted-note">
        Bỏ qua tin trùng hẳn và tin rác chắc chắn trước khi gửi model — không xoá, vẫn xem lại ở “Tin đã bỏ qua”.
      </p>
      {save.isError ? <InlineError detail={errorDetail(save.error)}>{errorText(save.error)}</InlineError> : null}
    </>
  );
}

/** Link "Tin đã bỏ qua (N)" → danh sách chỉ đọc (50 tin gần nhất, số dài đã che với vai không phải Owner). */
function SkippedBlock() {
  const q = useSkippedItems(50, true);
  const tz = useOrgTimezone();
  const [open, setOpen] = useState(false);
  if (!isSkippedPage(q.data)) return null;
  const { total, items } = q.data;
  return (
    <div className="jev-skipped" data-testid="jev-skipped">
      <Button variant="ghost" size="sm" icon="ph ph-eye" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        {skippedLinkLabel(total)}
      </Button>
      {open ? (
        items.length ? (
          <ul className="muted-note" style={{ margin: '6px 0 0', paddingLeft: 16, display: 'flex', flexDirection: 'column', gap: 6 }}>
            {items.map((it) => (
              <li key={it.id} style={{ overflowWrap: 'anywhere' }}>
                <strong>{skippedReasonText(it.reason, it.reason_text)}</strong> · {it.group ?? 'Tin riêng'}
                {it.person ? ` · ${it.person}` : ''} · {fmtDMClock(it.at, tz)}
                <div>{it.text}</div>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted-note">Chưa có tin nào bị bỏ qua.</p>
        )
      ) : null}
    </div>
  );
}
