import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { useMutation } from '@tanstack/react-query';
import type { CliKind, Provider, ProviderKind, ProviderTestResult, SetupState } from '@gen-harness/contracts';
import { Button, Icon, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2, useCliProfiles, useProviders } from '../../lib/dataQueries';
import { fmtDMClock, fmtInt, fmtLatency } from '../../lib/format';
import { queryClient } from '../../lib/queryClient';
import { errorDetail, errorText } from '../../lib/errorText';
import { FriendlyErrorText, InlineError, PinHint, SkeletonLines } from '../../screens/common';
import { PROVIDER_PRESETS, isCliKind, testOkText } from '../../screens/api/apiModel';
import { CliLoginPanel } from '../CliLoginPanel';
import { allSourcesOff, providerHasModel, providerReady, testedModels } from '../phase2Model';
import { describeError } from '../types';

type ApiKind = Exclude<ProviderKind, CliKind | 'system_one'>;
interface KindOpt {
  value: string;
  kind: ApiKind;
  label: string;
  name: string;
  endpoint: string;
  keyHint: string | null;
  modelHint: string | null;
}

/** Lựa chọn chính: hai nguồn dán khoá. */
const BASIC_KINDS: KindOpt[] = [
  { value: 'gemini', kind: 'gemini', label: 'Gemini', name: 'Gemini API', endpoint: '', keyHint: null, modelHint: null },
  { value: 'deepseek', kind: 'deepseek', label: 'DeepSeek', name: 'DeepSeek API', endpoint: '', keyHint: null, modelHint: null },
];
/** "Nâng cao": tương thích OpenAI (tự nhập địa chỉ) và mẫu dựng sẵn (OpenRouter). */
const ADVANCED_KINDS: KindOpt[] = [
  { value: 'openai_compat', kind: 'openai_compat', label: 'Tương thích OpenAI', name: '', endpoint: '', keyHint: null, modelHint: null },
  ...PROVIDER_PRESETS.map((p) => ({ value: p.id, kind: p.kind as ApiKind, label: p.label, name: p.name, endpoint: p.endpoint, keyHint: p.keyHint, modelHint: p.modelHint })),
];

export const AI_LATER_TEXT = 'Gen sẽ chưa tóm tắt được bản tin';

export interface StepAiSourceProps {
  /** Thiết lập đã xong, mở lại từ nút "Chọn model" để đổi nguồn AI (máy chủ cho lưu bước 4 sau Hoàn tất). */
  finished?: boolean;
  /** Máy chủ nhận bước 4 (PUT /setup/steps/4). */
  onSaved: (s: SetupState) => void;
  /** "Làm sau" — chỉ có khi thiết lập chưa xong. */
  onLater?: () => void;
}

/**
 * Bước 2 của Thiết lập một màn — Nguồn AI. Hai lựa chọn lớn: (1) Đăng nhập Google (Antigravity); (2) Dán khoá API Gemini hoặc
 * DeepSeek rồi "Kiểm tra" → "Đã chạy được". "Nâng cao" thu gọn: Claude Code CLI, tương thích OpenAI/OpenRouter, thứ tự dự phòng.
 * "Tiếp tục" ghi PUT /setup/steps/4 với các nguồn đã chạy được (cùng thân như bước 4 cũ). "Làm sau" đi tiếp, Gen chưa tóm tắt được tin.
 */
export function StepAiSource({ finished = false, onSaved, onLater }: StepAiSourceProps) {
  const providers = useProviders();
  const agyProfiles = useCliProfiles('antigravity_cli');
  const claudeProfiles = useCliProfiles('claude_code_cli');
  const [tested, setTested] = useState<Record<string, ProviderTestResult>>({});
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formDetail, setFormDetail] = useState<string | null>(null);

  // Jev (system_one) không phải model chữ — không bao giờ vào chuỗi (cùng bộ lọc với máy chủ).
  const list = useMemo(() => [...(providers.data ?? [])].filter((p) => p.kind !== 'system_one').sort((a, b) => a.failover_rank - b.failover_rank), [providers.data]);
  const agyActive = agyProfiles.data?.find((p) => p.active);
  const claudeActive = claudeProfiles.data?.find((p) => p.active);
  const cliActive = { antigravity_cli: !!agyActive && agyActive.state !== 'expired', claude_code_cli: !!claudeActive && claudeActive.state !== 'expired' };
  const ready = list.filter((p) => providerReady(p, tested, cliActive));
  const withModel = ready.filter((p) => providerHasModel(p, tested));
  const canContinue = withModel.length > 0;
  // "Đã chạy được" chỉ khi nguồn có model hoặc vừa gọi thử xanh — đăng nhập xong mà chưa kiểm tra thì chưa nói chạy được.
  const shown = ready.filter((p) => providerHasModel(p, tested) || tested[p.id]?.ok === true);
  const keyProviders = list.filter((p) => !isCliKind(p.kind));
  const agyRows = list.filter((p) => p.kind === 'antigravity_cli');
  const claudeRows = list.filter((p) => p.kind === 'claude_code_cli');
  const test = useMutation({
    mutationFn: (id: string) => api.providers.test(id),
    onSuccess: (r, id) => {
      setTested((t) => ({ ...t, [id]: r }));
      void queryClient.invalidateQueries({ queryKey: qk2.providers }); // trạng thái do máy chủ ghi — đọc lại cho khớp mọi màn
    },
    onError: (e, id) => setTested((t) => ({ ...t, [id]: { ok: false, latency_ms: null, models: [], error: errorText(e) } })),
  });

  // Đăng nhập Google/Claude Code xong, máy chủ chưa có dòng model và chưa gọi thử lần nào ⇒ tự "Kiểm tra" MỘT lần (máy chủ
  // lưu cặp model chạy được ở đúng lượt này). Nếu không, "Tiếp tục" khoá mà Sếp chẳng có nút nào để mở.
  const autoTried = useRef<Set<string>>(new Set());
  useEffect(() => {
    const target = ready.find((p) => isCliKind(p.kind) && !p.models.length && !p.last_test && !tested[p.id] && !autoTried.current.has(p.id));
    if (!target || test.isPending) return;
    autoTried.current.add(target.id);
    test.mutate(target.id);
  });

  const blockReason = canContinue
    ? null
    : ready.length
      ? test.isPending
        ? 'Đang kiểm tra kết nối với nguồn vừa đăng nhập…'
        : 'Đã đăng nhập nhưng chưa kiểm tra được model — bấm “Kiểm tra lại” ở nguồn bên trên.'
      : allSourcesOff(list)
        ? 'Mọi nguồn AI đang tắt — bật lại một nguồn ở API & Model, hoặc thêm nguồn mới ở trên rồi bấm Kiểm tra.'
        : 'Đăng nhập Google hoặc dán một khoá API và bấm Kiểm tra — có một nguồn chạy được là tiếp tục được.';

  const save = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!canContinue || busy) return;
    setBusy(true);
    setFormError(null);
    setFormDetail(null);
    try {
      const state = await api.setup.step4(ready.map((p) => p.id));
      // Máy chủ có thể vừa tự chọn model + xếp lại thứ tự — đọc lại cho các màn khác.
      void queryClient.invalidateQueries({ queryKey: qk2.providers });
      void queryClient.invalidateQueries({ queryKey: qk2.cliProfiles });
      onSaved(state);
    } catch (err) {
      setFormError(describeError(err));
      setFormDetail(errorDetail(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="setup-one__form"
      noValidate
      aria-labelledby="setup-one-title"
      data-testid="setup-ai-source"
      onSubmit={(e) => void save(e)}
      // Enter trong một ô nhập chỉ làm việc của ô đó (thêm khoá / gửi mã xác thực), không tự "Tiếp tục".
      onKeyDown={(e) => {
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') e.preventDefault();
      }}
    >
      <div className="setup-one__panel">
        <h1 className="setup-one__title" id="setup-one-title">
          {finished ? 'Đổi nguồn AI' : 'Nối nguồn AI cho Gen'}
        </h1>
        <p className="setup-one__lead">
          Gen cần một nguồn AI để đọc và tóm tắt tin cho Sếp. Chọn một cách dưới đây — chừng hai phút là xong.
        </p>

        <section className="setup-one__choice" aria-labelledby="setup-ai-google-title" data-testid="setup-ai-google" data-ready={cliActive.antigravity_cli || undefined}>
          <h2 className="setup-one__choice-title" id="setup-ai-google-title">
            Đăng nhập Google (Antigravity)
          </h2>
          <p className="setup-one__muted">Dùng tài khoản Google của Sếp, không cần dán khoá.</p>
          <CliLoginPanel kind="antigravity_cli" bare />
          <ProviderRows providers={agyRows} cli tested={tested} testing={test.isPending ? test.variables : null} onTest={(id) => test.mutate(id)} />
        </section>

        <section className="setup-one__choice" aria-labelledby="setup-ai-key-title" data-testid="setup-ai-key">
          <h2 className="setup-one__choice-title" id="setup-ai-key-title">
            Dán khoá API
          </h2>
          <p className="setup-one__muted">Gemini hoặc DeepSeek. Khoá được mã hoá khi lưu; Console chỉ hiện 4 ký tự cuối.</p>
          <KeyForm kinds={BASIC_KINDS} idPrefix="basic" onAdded={(p) => test.mutate(p.id)} />
          <ProviderRows providers={keyProviders} loading={providers.isPending} tested={tested} testing={test.isPending ? test.variables : null} onTest={(id) => test.mutate(id)} />
        </section>

        <details className="setup-one__more" data-testid="setup-ai-advanced">
          <summary>Nâng cao</summary>
          <div className="setup-one__more-body">
            <div data-testid="setup-ai-claude">
              <h3 className="setup-one__choice-title">Claude Code CLI · gói Claude (tuỳ chọn)</h3>
              <CliLoginPanel kind="claude_code_cli" bare />
              <ProviderRows providers={claudeRows} cli tested={tested} testing={test.isPending ? test.variables : null} onTest={(id) => test.mutate(id)} />
            </div>
            <div data-testid="setup-ai-compat">
              <h3 className="setup-one__choice-title">Tương thích OpenAI / OpenRouter</h3>
              <KeyForm kinds={ADVANCED_KINDS} idPrefix="adv" onAdded={(p) => test.mutate(p.id)} />
            </div>
            <Link to="/api" className="setup-one__link" data-testid="setup-ai-order">
              <Icon name="ph ph-arrows-down-up" size={14} />
              Thứ tự dự phòng
            </Link>
          </div>
        </details>

        {shown.length ? (
          <div className="setup-one__ready" role="status" data-testid="setup-ai-ready">
            <Icon name="ph ph-check-circle" size={16} color="var(--color-ok)" />
            <span>Đã chạy được</span>
            <span className="setup-one__ready-names">{shown.map((p) => p.name).join(' → ')}</span>
          </div>
        ) : null}
        {!canContinue && providers.data ? (
          <p className="setup-one__hint" role="status">
            {blockReason}
          </p>
        ) : null}
        {formError ? <InlineError detail={formDetail}>{formError}</InlineError> : null}

        <div className="setup-one__actions">
          <Button variant="primary" type="submit" disabled={!canContinue} loading={busy} iconRight="ph ph-arrow-right">
            Tiếp tục
          </Button>
          <div className="setup-one__later">
            {finished || !onLater ? (
              <Link to="/owner" className="setup-one__link setup-one__link--quiet">
                Để sau, về Mặt tiền
              </Link>
            ) : (
              <>
                <button type="button" className="setup-one__link setup-one__link--quiet" onClick={onLater} data-testid="setup-ai-later">
                  Làm sau
                </button>
                <p className="setup-one__muted">{AI_LATER_TEXT}.</p>
              </>
            )}
          </div>
        </div>
      </div>
    </form>
  );
}

/** Các nguồn dán khoá đã có — mỗi nguồn một dòng với kết quả gọi thử và nút kiểm tra lại. */
function ProviderRows({
  providers,
  loading = false,
  cli = false,
  tested,
  testing,
  onTest,
}: {
  providers: Provider[];
  loading?: boolean;
  /** Nguồn CLI (đăng nhập tài khoản, không có khoá): dòng riêng để còn nút "Kiểm tra lại" và kết quả gọi thử. */
  cli?: boolean;
  tested: Record<string, ProviderTestResult>;
  testing: string | null | undefined;
  onTest: (id: string) => void;
}) {
  if (loading) return <SkeletonLines rows={1} padding="0" />;
  if (!providers.length) return null;
  return (
    <div className="setup-one__prov" aria-label={cli ? 'Nguồn đăng nhập tài khoản' : 'Nguồn khoá API đã thêm'} data-testid={cli ? 'setup-ai-cli-providers' : 'setup-ai-providers'}>
      {providers.map((p) => {
        const t = tested[p.id] ?? p.last_test ?? null;
        const offered = testedModels(p, tested);
        return (
          <div className="setup-one__prov-row" key={p.id} data-testid={cli ? 'setup-ai-cli-provider' : 'setup-ai-provider'}>
            <div className="setup-one__prov-main">
              <div className="setup-one__prov-name" title={p.name}>
                {p.name}
              </div>
              {p.enabled ? null : (
                <div className="setup-one__muted" data-testid="setup-ai-provider-off">
                  Đã tắt — bật lại ở{' '}
                  <Link to="/api" className="setup-one__link">
                    API & Model
                  </Link>
                </div>
              )}
              <div className="setup-one__muted">
                {cli ? 'Tài khoản đã đăng nhập' : `${fmtInt(p.keys.length)} khoá${p.keys[0] ? ` · …${p.keys[0].last4}` : ''}`}
                {offered.length ? ` · ${offered.length} model` : ''}
                {cli && !t && !offered.length && !p.models.length ? ' · chưa kiểm tra' : ''}
              </div>
              {t ? (
                <div className="setup-one__prov-test" data-bad={t.ok ? undefined : ''} role="status">
                  {t.ok ? (
                    testOkText(t, fmtLatency)
                  ) : (
                    <>
                      <FriendlyErrorText raw={t.error} prefix="Chưa dùng được: " fallback="không gọi được nguồn này." />
                      {t.at ? ` (lúc ${fmtDMClock(t.at)})` : null}
                      {t.error_detail ? (
                        <details className="tech-detail">
                          <summary>Chi tiết kỹ thuật</summary>
                          <code>{t.error_detail}</code>
                        </details>
                      ) : null}
                    </>
                  )}
                </div>
              ) : null}
            </div>
            <Button variant="secondary" loading={testing === p.id} onClick={() => onTest(p.id)} aria-label={`Kiểm tra lại ${p.name}`}>
              Kiểm tra lại
            </Button>
          </div>
        );
      })}
    </div>
  );
}

/** Dán khoá: tạo nguồn (cần phiên PIN `ai.route_change`, hộp PIN tự mở khi gặp 423) rồi gọi thử ngay. */
function KeyForm({ kinds, idPrefix, onAdded }: { kinds: KindOpt[]; idPrefix: string; onAdded: (p: Provider) => void }) {
  const [choice, setChoice] = useState(kinds[0].value);
  const opt = kinds.find((k) => k.value === choice) ?? kinds[0];
  const [name, setName] = useState(kinds[0].name);
  const [endpoint, setEndpoint] = useState(kinds[0].endpoint);
  const [key, setKey] = useState('');
  const custom = opt.kind === 'openai_compat';
  const add = useMutation({
    mutationFn: () =>
      api.providers.create({
        kind: opt.kind,
        name: name.trim() || opt.label,
        ...(opt.kind === 'openai_compat' ? { endpoint: endpoint.trim() } : {}),
        keys: [key.trim()],
      }),
    onSuccess: (p) => {
      queryClient.setQueryData<Provider[]>(qk2.providers, (old) => (old ? [...old.filter((x) => x.id !== p.id), p] : [p]));
      setKey('');
      onAdded(p);
    },
  });
  const keyShort = key.trim().length > 0 && key.trim().length < 8;
  const endpointBad = custom && endpoint.trim().length > 0 && !/^https?:\/\//.test(endpoint.trim());
  const valid = key.trim().length >= 8 && (!custom || /^https?:\/\//.test(endpoint.trim()));
  return (
    <div className="setup-one__fields" data-testid={`setup-ai-keyform-${idPrefix}`}>
      {kinds.length > 1 ? (
        <SelectField
          label={idPrefix === 'basic' ? 'Loại khoá' : 'Loại nguồn'}
          value={choice}
          options={kinds.map((k) => ({ value: k.value, label: k.label }))}
          onChange={(e) => {
            const next = kinds.find((x) => x.value === e.target.value) ?? kinds[0];
            setChoice(next.value);
            setName(next.name);
            setEndpoint(next.endpoint);
          }}
        />
      ) : null}
      {idPrefix !== 'basic' ? <TextField label="Tên hiển thị" value={name} onChange={(e) => setName(e.target.value)} /> : null}
      {custom ? (
        <TextField
          label="Địa chỉ gọi (Endpoint)"
          placeholder="https://…/v1"
          value={endpoint}
          error={endpointBad ? 'Địa chỉ cần bắt đầu bằng https:// (http:// chỉ dùng với máy trong mạng nội bộ, vd Ollama)' : null}
          onChange={(e) => setEndpoint(e.target.value)}
        />
      ) : null}
      <TextField
        label="Khoá API"
        id={`setup-ai-key-${idPrefix}`}
        type="password"
        autoComplete="off"
        value={key}
        error={keyShort ? 'Khoá API có vẻ quá ngắn — kiểm tra lại đã dán đủ chưa' : null}
        onChange={(e) => setKey(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            e.stopPropagation();
            if (valid && !add.isPending) add.mutate();
          }
        }}
      />
      {opt.keyHint ? (
        <p className="setup-one__muted" data-testid="setup-provider-preset-hint">
          {opt.keyHint}
          {opt.modelHint ? ` · Gợi ý model: ${opt.modelHint}` : ''}
        </p>
      ) : null}
      <div>
        <Button variant="secondary" icon="ph ph-plugs-connected" disabled={!valid} loading={add.isPending} onClick={() => add.mutate()}>
          Kiểm tra
        </Button>
      </div>
      {/* v0.1.35 (F-20): thêm nhà cung cấp AI cần phiên PIN `ai.route_change` — hộp PIN tự mở khi máy chủ trả 423. */}
      <PinHint />
      {add.isError ? <InlineError detail={errorDetail(add.error)}>{errorText(add.error)}</InlineError> : null}
    </div>
  );
}
