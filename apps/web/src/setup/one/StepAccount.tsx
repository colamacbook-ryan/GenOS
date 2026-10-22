import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ApiError, type SetupState } from '@gen-harness/contracts';
import { Button, Icon, PinInput, SelectField, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorDetail } from '../../lib/errorText';
import { qk } from '../../lib/queries';
import { queryClient } from '../../lib/queryClient';
import { InlineError } from '../../screens/common';
import { CURRENCIES, TIMEZONES } from '../steps';
import { describeError, TOKEN_INVALID } from '../types';
import { useFieldErrors } from '../useFieldErrors';
import {
  DEFAULT_ADDRESSING,
  addressingPreview,
  firstHiddenError,
  passwordStrength,
  step2Errors,
  step3Errors,
  validateToken,
  type Step2Values,
  type Step3Values,
} from '../validation';

/** Tất cả ô của màn Tài khoản (mã thiết lập + bước 2 + bước 3). */
type Values = Step2Values & Step3Values;
type K = keyof Values;

/** Ô nằm trong "Tuỳ chỉnh" (thu gọn) — lỗi máy chủ ở đây phải tự mở phần đó ra. */
const ADVANCED_KEYS: readonly K[] = ['timezone', 'currency', 'self_name', 'bot_calls_me'];
const KNOWN_KEYS: readonly K[] = ['token', 'display_name', 'email', 'password', 'pin', 'pin_confirm', 'org_name', ...ADVANCED_KEYS];

const stepDone = (s: SetupState, n: number): boolean => s.steps.find((x) => x.n === n)?.status === 'done';

export interface StepAccountProps {
  /** Trạng thái máy chủ mới nhất (GET /setup/state) — bước đã xong thì BỎ QUA, tải lại giữa chừng vẫn đi tiếp. */
  state: SetupState;
  /** Mã thiết lập từ ?token= hoặc sessionStorage ('' khi thiếu). */
  token: string;
  setToken: (t: string) => void;
  /** Mỗi PUT thành công: trạng thái mới (SetupPage ghi vào cache, bước kế tự hiện). */
  onState: (s: SetupState) => void;
}

/** Lỗi từng ô khi ô còn dùng: ô nào đã xong ở máy chủ thì không kiểm. */
function accountErrors(v: Values, need: { token: boolean; owner: boolean; org: boolean }): Partial<Record<K, string>> {
  const e: Partial<Record<K, string>> = {};
  if (need.token) {
    const t = validateToken(v.token);
    if (t) e.token = t;
  }
  if (need.owner) {
    const { token: _t, ...rest } = step2Errors({ ...v, token: 'x' });
    Object.assign(e, rest);
  }
  if (need.org) Object.assign(e, step3Errors(v));
  return e;
}

/**
 * Bước 1 của Thiết lập một màn — gom 3 bước máy chủ (mã thiết lập → Owner + PIN → tổ chức) vào một form: Tên Sếp, Email, Mật
 * khẩu, Mã PIN (2 lần), Tên doanh nghiệp; "Tuỳ chỉnh" thu gọn (múi giờ, tiền tệ, xưng hô). "Tiếp tục" gọi lần lượt
 * PUT /setup/steps/1 → 2 (đặt cookie phiên) → 3, BỎ QUA bước đã xong theo trạng thái máy chủ.
 */
export function StepAccount({ state, token, setToken, onState }: StepAccountProps) {
  const step1Done = stepDone(state, 1);
  const step2Done = stepDone(state, 2);
  const step3Done = stepDone(state, 3);
  const [v, setV] = useState<Values>({
    token,
    display_name: '',
    email: '',
    password: '',
    pin: '',
    pin_confirm: '',
    org_name: '',
    timezone: 'Asia/Ho_Chi_Minh',
    currency: 'VND',
    self_name: DEFAULT_ADDRESSING,
    bot_calls_me: DEFAULT_ADDRESSING,
  });
  // Mã thiết lập chỉ hiện khi thiếu (trang vừa tải lại không còn mã) hoặc máy chủ từ chối mã đang có.
  const needToken = !step1Done || !step2Done;
  const [showToken, setShowToken] = useState(false);
  const tokenVisible = needToken && (!token || showToken);
  const [advOpen, setAdvOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [formDetail, setFormDetail] = useState<string | null>(null);
  const [expired, setExpired] = useState(false);

  const effectiveToken = tokenVisible ? v.token : token || v.token;
  const clientErrors = accountErrors({ ...v, token: effectiveToken }, { token: needToken, owner: !step2Done, org: !step3Done });
  const f = useFieldErrors<K>(clientErrors);
  const strength = passwordStrength(v.password);
  const canContinue = Object.keys(clientErrors).length === 0 && !f.hasServerErrors;

  // Tên Sếp đã tạo (sau PUT 2 có phiên) — chỉ hỏi khi bước 2 đã xong.
  const me = useQuery({ queryKey: qk.me, queryFn: ({ signal }) => api.auth.me(signal), enabled: step2Done, retry: false });

  const set = (k: K, val: string) => {
    setV((s) => ({ ...s, [k]: val }));
    f.changed(k);
  };

  const save = async () => {
    f.setSubmitted(true);
    if (!canContinue) return;
    setBusy(true);
    setFormError(null);
    setFormDetail(null);
    setExpired(false);
    let cur = state;
    let stage = 1;
    try {
      const tok = effectiveToken.trim();
      if (!stepDone(cur, 1)) {
        stage = 1;
        cur = await api.setup.step1({ token: tok, language: 'vi', mode: 'empty' });
        setToken(tok);
        onState(cur);
      }
      if (!stepDone(cur, 2)) {
        stage = 2;
        cur = await api.setup.step2({
          token: tok,
          display_name: v.display_name.trim(),
          email: v.email.trim(),
          password: v.password,
          pin: v.pin,
          pin_confirm: v.pin_confirm,
        });
        await queryClient.invalidateQueries({ queryKey: qk.me });
        onState(cur);
      }
      if (!stepDone(cur, 3)) {
        stage = 3;
        cur = await api.setup.step3({
          org_name: v.org_name.trim(),
          timezone: v.timezone,
          currency: v.currency,
          self_name: v.self_name.trim(),
          bot_calls_me: v.bot_calls_me.trim(),
        });
        void queryClient.invalidateQueries({ queryKey: qk.me });
        onState(cur);
      }
    } catch (e) {
      if (e instanceof ApiError && stage <= 2 && (e.code === 'SETUP_TOKEN_INVALID' || e.status === 403)) {
        setShowToken(true);
        f.setServer({ token: TOKEN_INVALID });
      } else if (e instanceof ApiError && e.status === 422) {
        const known = Object.entries(e.fieldErrors).filter(([k]) => (KNOWN_KEYS as readonly string[]).includes(k)) as Array<[K, string]>;
        if (known.length) {
          f.setServer(Object.fromEntries(known) as Partial<Record<K, string>>);
          if (known.some(([k]) => ADVANCED_KEYS.includes(k))) setAdvOpen(true);
          setFormError(describeError(e));
        } else {
          // Máy chủ báo trường mà màn này không có ô: nói thẳng câu của máy chủ thay vì "kiểm tra các trường được đánh dấu".
          setFormError(Object.values(e.fieldErrors).filter((x) => typeof x === 'string').join(' ') || describeError(e));
        }
        setFormDetail(errorDetail(e));
      } else if (e instanceof ApiError && e.status === 401 && stage === 3) {
        setExpired(true);
        setFormError('Phiên đăng nhập của Sếp đã hết — đăng nhập lại rồi quay về đây để tiếp tục thiết lập.');
        setFormDetail(errorDetail(e));
      } else {
        setFormError(describeError(e));
        setFormDetail(errorDetail(e));
      }
      // Bước trước đó đã lưu thì ghi nhận ngay — bấm lại sẽ chạy tiếp từ bước còn dở.
      if (cur !== state) onState(cur);
    } finally {
      setBusy(false);
    }
  };

  const shownError = (k: K): string | null => (k === 'pin_confirm' && v.pin_confirm.length === 6 ? 'shown' : f.errorOf(k));
  const blocked = canContinue ? null : firstHiddenError(clientErrors, shownError);

  const text = (k: K, label: string, extra: Partial<Parameters<typeof TextField>[0]> = {}) => (
    <TextField label={label} value={v[k]} onChange={(e) => set(k, e.target.value)} onBlur={() => f.blur(k)} error={f.errorOf(k)} {...extra} />
  );

  return (
    <form
      className="setup-one__form"
      noValidate
      aria-labelledby="setup-one-title"
      data-testid="setup-account"
      onSubmit={(e) => {
        e.preventDefault();
        if (canContinue && !busy) void save();
      }}
    >
      <div className="setup-one__panel">
        <h1 className="setup-one__title" id="setup-one-title">
          Tạo tài khoản Sếp
        </h1>
        <p className="setup-one__lead">Một lần điền là xong: tài khoản đăng nhập, mã PIN và tên doanh nghiệp. Các việc còn lại làm dần sau.</p>

        <div className="setup-one__fields">
          {tokenVisible ? (
            <TextField
              label="Mã thiết lập"
              className="gh-input setup-mono"
              value={v.token}
              autoFocus
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => set('token', e.target.value)}
              onBlur={() => f.blur('token')}
              error={f.errorOf('token')}
              hint="Hiện trong màn hình dòng lệnh của trình cài — tự điền khi mở từ đường dẫn của trình cài."
            />
          ) : null}

          {step2Done ? (
            <div className="setup-one__note" data-tone="ok" data-testid="setup-account-done">
              <Icon name="ph ph-check-circle" size={16} color="var(--color-ok)" />
              <span>
                Tài khoản Sếp đã tạo{me.data?.display_name ? ` — ${me.data.display_name}` : ''}. Chỉ còn tên doanh nghiệp.
              </span>
            </div>
          ) : (
            <>
              <div className="setup-one__grid setup-one__grid--two">
                {text('display_name', 'Tên Sếp', { autoComplete: 'name', autoFocus: !tokenVisible })}
                {text('email', 'Email', { type: 'email', autoComplete: 'username' })}
              </div>
              <TextField
                label="Mật khẩu"
                revealable
                autoComplete="new-password"
                value={v.password}
                onChange={(e) => set('password', e.target.value)}
                onBlur={() => f.blur('password')}
                error={f.errorOf('password')}
                hint="Ít nhất 12 ký tự. Một câu dài dễ nhớ an toàn hơn mật khẩu ngắn phức tạp."
                after={
                  <div className="setup-strength" data-tone={strength.tone} aria-live="polite">
                    <span className="setup-strength__bar">
                      <span className="setup-strength__fill" style={{ width: `${(strength.score / 4) * 100}%` }} />
                    </span>
                    <span className="setup-strength__label">Độ mạnh: {strength.label}</span>
                  </div>
                }
              />
              <div className="setup-one__grid setup-one__grid--two">
                <PinField label="Mã PIN (6 số)" id="pin" value={v.pin} onChange={(val) => set('pin', val)} onBlur={() => f.blur('pin')} error={f.errorOf('pin')} />
                <PinField
                  label="Nhập lại mã PIN"
                  id="pin_confirm"
                  value={v.pin_confirm}
                  onChange={(val) => set('pin_confirm', val)}
                  onBlur={() => f.blur('pin_confirm')}
                  error={f.errorOf('pin_confirm') ?? (v.pin_confirm.length === 6 ? (clientErrors.pin_confirm ?? null) : null)}
                />
              </div>
              <p className="setup-one__hint">Mã PIN xác nhận mọi thao tác nhạy cảm (nối kênh, đổi nguồn AI, mời người, đổi quyền). Nhập sai 5 lần sẽ khoá 15 phút.</p>
            </>
          )}

          {step3Done ? null : (
            <>
              {text('org_name', 'Tên doanh nghiệp', { autoComplete: 'organization', autoFocus: step2Done && !tokenVisible })}
              <details className="setup-one__more" open={advOpen} onToggle={(e) => setAdvOpen((e.currentTarget as HTMLDetailsElement).open)}>
                <summary>Tuỳ chỉnh</summary>
                <div className="setup-one__more-body">
                  <div className="setup-one__grid setup-one__grid--two">
                    <SelectField
                      label="Múi giờ"
                      value={v.timezone}
                      options={TIMEZONES}
                      onChange={(e) => set('timezone', e.target.value)}
                      onBlur={() => f.blur('timezone')}
                      error={f.errorOf('timezone')}
                    />
                    <SelectField
                      label="Tiền tệ"
                      value={v.currency}
                      options={CURRENCIES}
                      onChange={(e) => set('currency', e.target.value)}
                      onBlur={() => f.blur('currency')}
                      error={f.errorOf('currency')}
                    />
                  </div>
                  <div className="setup-one__grid setup-one__grid--two">
                    {text('self_name', 'Sếp tự xưng là', { placeholder: 'Anh, Chị, Tôi…', hint: 'Điền sẵn “Sếp” — đổi nếu Sếp muốn xưng khác.' })}
                    {text('bot_calls_me', 'Gen gọi Sếp là', { placeholder: 'Sếp, Anh, Chị…' })}
                  </div>
                  <p className="setup-one__muted" aria-live="polite">
                    {addressingPreview(v.self_name, v.bot_calls_me)}
                  </p>
                </div>
              </details>
            </>
          )}
        </div>

        {formError ? (
          <InlineError detail={formDetail}>
            {formError}
            {expired ? (
              <>
                {' '}
                <Link to={`/login?next=${encodeURIComponent('/setup')}`}>Đăng nhập lại</Link>
              </>
            ) : null}
          </InlineError>
        ) : null}
        {blocked ? (
          <p className="setup-one__hint" role="status">
            {blocked}
          </p>
        ) : null}

        <div className="setup-one__actions">
          <Button variant="primary" type="submit" disabled={!canContinue} loading={busy} iconRight="ph ph-arrow-right">
            Tiếp tục
          </Button>
        </div>
      </div>
    </form>
  );
}

function PinField({
  label,
  id,
  value,
  onChange,
  onBlur,
  error,
}: {
  label: string;
  id: string;
  value: string;
  onChange: (v: string) => void;
  onBlur: () => void;
  error: string | null;
}) {
  return (
    <div className="gh-field">
      <label className="gh-field__label" htmlFor={`${id}-0`}>
        {label}
      </label>
      <PinInput idPrefix={id} label={label} value={value} onChange={onChange} onBlur={onBlur} invalid={!!error} describedBy={error ? `${id}-error` : undefined} />
      {error ? (
        <div className="gh-field__error" id={`${id}-error`}>
          {error}
        </div>
      ) : null}
    </div>
  );
}
