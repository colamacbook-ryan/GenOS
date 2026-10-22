import { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import type {
  TelegramChat,
  TelegramConfig,
  TelegramFindChatResult,
  TelegramPairStart,
  TelegramSaveBody,
  TelegramTestResult,
} from '@gen-harness/contracts';
import { ApiError } from '@gen-harness/contracts';
import { Button, Dialog, Icon, Switch, TextField } from '@gen-harness/ui';
import { BOSS_CHECKS_KEY } from '../../guide/bossChecksModel';
import { api } from '../../lib/api';
import { errorDetail, errorText } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useOrgTimezone } from '../../lib/permissions';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { CardError, InlineError, Panel, SkeletonLines } from '../common';
import { qkSystem } from '../system/queries';
import { ConnectionStatusPill } from './ConnectionStatusPill';
import { telegramConnStatus } from './connectionsModel';
import {
  BOTFATHER_STEPS,
  FIND_CHAT_EMPTY,
  HOST_STATE_LABEL,
  INBOUND_NOTE,
  INBOUND_TITLE,
  PAIR_CODE_HINT,
  TELEGRAM_KEY,
  TELEGRAM_WARNING,
  type HostWait,
  asInbound,
  asTelegramConfig,
  hostPollMs,
  inboundStatusText,
  pairInstruction,
  pairPollMs,
  spacedCode,
  hostTestText,
  hostWarning,
  hostWarningDetail,
  scheduleText,
  str,
  telegramErrorText,
  telegramTechDetail,
  telegramKicker,
  testOkText,
  tokenFormatError,
} from './telegramModel';

/**
 * v0.1.44 (F-8c) — Kết nối › Telegram ("Báo động & bản tin"), CHỈ Owner. Bot Telegram của Sếp nhận cảnh báo sự cố
 * từ "Trực canh máy chủ" và bản tin 07:30/17:30, nhắc việc. Token chỉ GHI (máy chủ không bao giờ trả lại): sau khi
 * lưu, ô token xoá trắng, thẻ chỉ hiện "@bot → chat •••1234". Lưu/Tắt cần PIN (apiClient tự mở hộp PIN khi 423).
 *
 * v0.1.64 (Telegram 2 chiều): khối "Nhận tin từ Sếp" — Ghép Telegram (mã 6 số sống 10 phút, Sếp gửi `/start <mã>` cho
 * bot), công tắc nhận tin (mặc định TẮT với bản cài cũ), Gỡ ghép. Qua Telegram Gen chỉ đọc/đề xuất: không có nút Duyệt,
 * không nhập mã PIN ở đây ngoài hộp PIN chuẩn của Console. Mã ghép chỉ nằm trong state của thẻ, không vào cache.
 */
export function TelegramCard() {
  // Gửi thử đạt + đã nhờ Trực canh máy chủ gửi tin thứ hai ⇒ hỏi lại mỗi 5 giây (tối đa ~2 phút) tới khi kết quả tin
  // thử từ máy chủ đổi — Sếp thấy ngay dòng "Tin thử từ máy chủ: Đạt/Lỗi" mà không phải tải lại trang.
  const [hostWait, setHostWait] = useState<HostWait | null>(null);
  const q = useQuery({
    queryKey: TELEGRAM_KEY,
    queryFn: ({ signal }) => api.notify.getTelegram(signal),
    refetchInterval: (query) => {
      const d = asTelegramConfig(query.state.data);
      return pairPollMs(d) || hostPollMs(hostWait, d?.host?.test?.at);
    },
  });
  const t = asTelegramConfig(q.data);
  const [editing, setEditing] = useState(false);
  const status = q.data !== undefined ? telegramConnStatus(t) : null;

  return (
    <Panel
      title="Telegram"
      genTarget="system.channels.telegram"
      label="Telegram — báo động & bản tin"
      kicker={telegramKicker(t)}
      aside={status ? <ConnectionStatusPill status={status} /> : undefined}
      bodyClass="conn-card__body"
    >
      {q.isPending ? (
        <SkeletonLines rows={2} padding="0" />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : t?.configured && !editing ? (
        <ConfiguredView
          t={t}
          onEdit={() => setEditing(true)}
          onHostRequested={() => setHostWait({ since: Date.now(), prevAt: str(t.host?.test?.at) })}
        />
      ) : (
        <SetupForm t={t} onDone={() => setEditing(false)} onCancel={t?.configured ? () => setEditing(false) : undefined} />
      )}
      {t ? <HostBlock t={t} canResave={t.configured && !editing} /> : null}
      <p className="muted-note" role="note" data-testid="telegram-warning">
        <Icon name="ph ph-shield-warning" size={12} /> {TELEGRAM_WARNING}
      </p>
    </Panel>
  );
}

function applyConfig(c: TelegramConfig) {
  queryClient.setQueryData(TELEGRAM_KEY, c);
  void queryClient.invalidateQueries({ queryKey: TELEGRAM_KEY });
  void queryClient.invalidateQueries({ queryKey: BOSS_CHECKS_KEY });
  // Tắt Telegram đóng sự cố telegram.failed ở máy chủ — dải "Cần Sếp xử lý" và chuông cập nhật ngay.
  void queryClient.invalidateQueries({ queryKey: qkSystem.health });
}

/** Chưa cấu hình (hoặc "Đổi token/chat_id"): 6 bước BotFather, token, Tìm chat_id, chat_id, 2 công tắc, Lưu. */
function SetupForm({ t, onDone, onCancel }: { t: TelegramConfig | null; onDone: () => void; onCancel?: () => void }) {
  const configured = !!t?.configured;
  const [token, setToken] = useState('');
  const [chatId, setChatId] = useState('');
  const [briefing, setBriefing] = useState(t?.briefing ?? true);
  const [reminders, setReminders] = useState(t?.reminders ?? true);
  const [tokenError, setTokenError] = useState<string | null>(null);
  const [chatError, setChatError] = useState<string | null>(null);
  const [found, setFound] = useState<TelegramFindChatResult | null>(null);

  const find = useMutation({
    mutationFn: (tok?: string) => api.notify.findTelegramChat(tok),
    onSuccess: (r) => setFound(r),
  });
  const save = useMutation({
    mutationFn: (body: TelegramSaveBody) => api.notify.saveTelegram(body),
    onSuccess: (c) => {
      setToken('');
      setChatId('');
      setFound(null);
      applyConfig(c);
      toast('Đã lưu Telegram — bấm Gửi thử để kiểm tra.', 'ok');
      onDone();
    },
  });

  const checkToken = (): string | null | false => {
    const tok = token.trim();
    if (!tok) {
      if (configured) return null; // giữ token đã lưu
      setTokenError('Dán token BotFather gửi vào đây trước.');
      return false;
    }
    const err = tokenFormatError(tok);
    setTokenError(err);
    return err ? false : tok;
  };

  const onFind = () => {
    const tok = checkToken();
    if (tok === false) return;
    setFound(null);
    find.mutate(tok ?? undefined);
  };

  const onSave = () => {
    const tok = checkToken();
    const cid = chatId.trim();
    // Đã cấu hình: chat_id trống = giữ chat cũ (Sếp chỉ thấy dạng che •••1234, không phải tìm lại).
    const needChat = !configured && !cid;
    setChatError(needChat ? 'Chọn một chat ở "Tìm chat_id" hoặc nhập chat_id.' : null);
    if (tok === false || needChat) return;
    // Chưa cấu hình ⇒ nối mới = bật; đã cấu hình ⇒ GIỮ trạng thái hiện tại (đang Tạm dừng rồi sửa token không tự bật lại).
    const body: TelegramSaveBody = { enabled: configured ? t?.enabled === true : true, briefing, reminders };
    if (cid) body.chat_id = cid;
    if (tok) body.token = tok;
    save.mutate(body);
  };

  const saveErr = save.error;
  const fields = saveErr instanceof ApiError && saveErr.status === 422 ? saveErr.fieldErrors : {};
  const fieldToken = typeof fields.token === 'string' ? fields.token : null;
  const fieldChat = typeof fields.chat_id === 'string' ? fields.chat_id : null;
  const saveCode = saveErr instanceof ApiError ? saveErr.code : null;

  return (
    <div className="boss-form" data-testid="telegram-setup">
      {!configured ? (
        <ol className="telegram-steps" data-testid="botfather-steps" style={{ margin: 0, paddingLeft: 20, display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12.5 }}>
          {BOTFATHER_STEPS.map((s) => (
            <li key={s}>{s}</li>
          ))}
        </ol>
      ) : null}
      <TextField
        label={configured ? 'Token mới (bỏ trống để giữ)' : 'Token'}
        type="password"
        revealable
        autoComplete="off"
        spellCheck={false}
        value={token}
        onChange={(e) => {
          setToken(e.target.value);
          if (tokenError) setTokenError(null);
        }}
        onBlur={() => setTokenError(tokenFormatError(token))}
        placeholder="123456789:AAH…"
        error={tokenError ?? fieldToken}
      />
      <div className="boss-actions">
        <Button variant="secondary" className="btn-27" icon="ph ph-magnifying-glass" loading={find.isPending} onClick={onFind}>
          Tìm chat_id
        </Button>
      </div>
      {find.isError ? <InlineError detail={errorDetail(find.error)}>{errorText(find.error)}</InlineError> : null}
      {found ? <FoundChats result={found} selected={chatId} onPick={(c) => setChatId(c.chat_id)} /> : null}
      <TextField
        label={configured ? `chat_id mới (bỏ trống để giữ ${str(t?.chat_id_masked) ?? 'chat đã lưu'})` : 'chat_id'}
        inputMode="text"
        autoComplete="off"
        value={chatId}
        onChange={(e) => {
          setChatId(e.target.value);
          if (chatError) setChatError(null);
        }}
        placeholder="vd 987654321"
        className="mono"
        error={chatError ?? fieldChat}
        hint="Bấm Tìm chat_id rồi chọn tên, hoặc nhập tay."
      />
      <div className="triage-row">
        <Switch checked={briefing} label="Gửi bản tin 07:30/17:30" onChange={setBriefing} />
        <span>Gửi bản tin 07:30/17:30</span>
      </div>
      <div className="triage-row">
        <Switch checked={reminders} label="Gửi nhắc việc" onChange={setReminders} />
        <span>Gửi nhắc việc</span>
      </div>
      <div className="boss-actions">
        <Button variant="primary" className="btn-27" icon="ph ph-floppy-disk" loading={save.isPending} onClick={onSave} data-main-action>
          Lưu
        </Button>
        {onCancel ? (
          <Button variant="ghost" className="btn-27" onClick={onCancel}>
            Huỷ
          </Button>
        ) : null}
      </div>
      {saveErr && !fieldToken && !fieldChat ? (
        <InlineError detail={errorDetail(saveErr)}>{saveCode && saveCode.startsWith('TELEGRAM_') ? telegramErrorText(saveCode, saveErr.message) : errorText(saveErr)}</InlineError>
      ) : null}
    </div>
  );
}

function FoundChats({ result, selected, onPick }: { result: TelegramFindChatResult; selected: string; onPick: (c: TelegramChat) => void }) {
  const chats = Array.isArray(result.chats) ? result.chats.filter((c) => c && typeof c.chat_id === 'string') : [];
  if (result.error_code) {
    return (
      <div className="muted-note friendly-error" role="status" data-testid="telegram-find-error">
        {telegramErrorText(result.error_code, result.message)}
        <details className="tech-detail">
          <summary>Chi tiết kỹ thuật</summary>
          <code>{telegramTechDetail(result)}</code>
        </details>
      </div>
    );
  }
  if (!chats.length) {
    return (
      <p className="muted-note" role="status" data-testid="telegram-find-empty">
        {FIND_CHAT_EMPTY}
      </p>
    );
  }
  return (
    <div className="boss-actions" role="group" aria-label="Chọn chat nhận tin" data-testid="telegram-chats">
      {chats.map((c) => {
        const name = str(c.name) ?? c.chat_id;
        const user = str(c.username);
        return (
          <Button
            key={c.chat_id}
            variant={selected === c.chat_id ? 'primary' : 'secondary'}
            className="btn-27"
            icon={selected === c.chat_id ? 'ph ph-check' : 'ph ph-user'}
            aria-pressed={selected === c.chat_id}
            onClick={() => onPick(c)}
          >
            {user ? `${name} (@${user})` : name}
          </Button>
        );
      })}
    </div>
  );
}

/** Đã cấu hình: "@bot → chat •••1234", Gửi thử, Đổi token/chat_id, Tắt Telegram (xác nhận). */
function ConfiguredView({ t, onEdit, onHostRequested }: { t: TelegramConfig; onEdit: () => void; onHostRequested: () => void }) {
  const tz = useOrgTimezone();
  const [confirmOff, setConfirmOff] = useState(false);
  const test = useMutation({
    mutationFn: () => api.notify.testTelegram(),
    onSuccess: (r) => {
      if (r.host_requested === true) onHostRequested();
      if (!r.transient) void queryClient.invalidateQueries({ queryKey: TELEGRAM_KEY });
      void queryClient.invalidateQueries({ queryKey: BOSS_CHECKS_KEY });
    },
  });
  // v0.1.70 (F092): tạm dừng / bật lại KHÔNG xoá token (PUT {enabled}, cần PIN) — "Tắt Telegram" bên dưới mới là gỡ hẳn.
  const pause = useMutation({
    mutationFn: (enabled: boolean) => api.notify.saveTelegram({ enabled }),
    onSuccess: (c) => {
      applyConfig(c);
      toast(c.enabled ? 'Đã bật lại Telegram.' : 'Đã tạm dừng Telegram — token vẫn giữ, bấm Bật lại khi cần.', 'ok');
    },
  });
  const bot = str(t.bot_username);
  const chat = str(t.chat_id_masked);
  const last = t.last_test;

  return (
    <>
      <p className="muted-note" data-testid="telegram-target">
        <span className="mono">{`${bot ? `@${bot}` : 'bot'} → chat ${chat ?? '•••'}`}</span>
        {` · ${t.enabled ? 'đang bật' : 'đang tắt'}${t.briefing ? ' · bản tin 07:30/17:30' : ''}${t.reminders ? ' · nhắc việc' : ''}`}
      </p>
      <div className="conn-card__actions">
        <Button variant="primary" className="btn-27" icon="ph ph-paper-plane-tilt" loading={test.isPending} onClick={() => test.mutate()} data-main-action data-gen-target="system.channels.telegram.test">
          Gửi thử
        </Button>
        <Button variant="secondary" className="btn-27" icon="ph ph-pencil-simple" onClick={onEdit}>
          Đổi token/chat_id
        </Button>
        <Button variant="secondary" className="btn-27" icon={t.enabled ? 'ph ph-pause' : 'ph ph-play'} loading={pause.isPending} onClick={() => pause.mutate(!t.enabled)}>
          {t.enabled ? 'Tạm dừng' : 'Bật lại'}
        </Button>
        <Button variant="ghost" className="btn-27" icon="ph ph-power" onClick={() => setConfirmOff(true)}>
          Tắt Telegram
        </Button>
      </div>
      {test.data ? (
        <TestResult r={test.data} host={t.host && typeof t.host === 'object' ? t.host : null} />
      ) : last ? (
        <div className="muted-note" data-testid="telegram-last-test">
          {last.status === 'pass' ? (
            `Gửi thử gần nhất: Đạt · ${fmtDMClock(last.checked_at, tz)}`
          ) : (
            <span className="friendly-error">
              {`Gửi thử gần nhất: Lỗi · ${telegramErrorText(last.error_code, last.message)}`}
              <details className="tech-detail">
                <summary>Chi tiết kỹ thuật</summary>
                <code>{telegramTechDetail(last)}</code>
              </details>
            </span>
          )}
        </div>
      ) : (
        <p className="muted-note">Chưa Gửi thử lần nào — bấm Gửi thử để chắc tin tới được điện thoại.</p>
      )}
      {test.isError ? <InlineError detail={errorDetail(test.error)}>{errorText(test.error)}</InlineError> : null}
      {pause.isError ? <InlineError detail={errorDetail(pause.error)}>{errorText(pause.error)}</InlineError> : null}
      <InboundBlock t={t} />
      {confirmOff ? <DisableDialog onClose={() => setConfirmOff(false)} /> : null}
    </>
  );
}

/** Phần "Nhận tin từ Sếp" (v0.1.64): ghép bằng mã 6 số, công tắc, gỡ ghép. Ẩn khi máy chủ cũ chưa có khối `inbound`. */
function InboundBlock({ t }: { t: TelegramConfig }) {
  const tz = useOrgTimezone();
  const inbound = asInbound(t);
  const [pair, setPair] = useState<TelegramPairStart['pair'] | null>(null);
  const [confirmUnpair, setConfirmUnpair] = useState(false);
  const start = useMutation({
    mutationFn: () => api.notify.pairStartTelegram(),
    onSuccess: (r) => {
      const { pair: p, ...cfg } = r; // mã ghép chỉ ở state thẻ — không đưa vào cache của react-query
      setPair(p);
      applyConfig(cfg);
    },
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.notify.setTelegramInbound(enabled),
    onSuccess: (c) => applyConfig(c),
  });
  if (!inbound) return null;
  // Đã ghép xong (hoặc mã hết hạn) ⇒ bỏ mã khỏi màn hình.
  const showCode = pair !== null && inbound.pair_pending && !inbound.paired;
  const statusText = inboundStatusText(inbound, (iso) => fmtDMClock(iso, tz));
  const err = toggle.error ?? start.error;
  return (
    <div className="telegram-inbound" data-testid="telegram-inbound" role="group" aria-label={INBOUND_TITLE}>
      <div className="gh-card__kicker">{INBOUND_TITLE}</div>
      <div className="triage-row">
        <Switch
          checked={inbound.enabled}
          label={INBOUND_TITLE}
          disabled={!inbound.paired || toggle.isPending}
          onChange={(v) => toggle.mutate(v)}
        />
        <span>{INBOUND_TITLE}</span>
      </div>
      <p className="muted-note" data-testid="telegram-inbound-status">
        {statusText}
      </p>
      {inbound.error_code && inbound.enabled ? (
        <div className="muted-note friendly-error" role="status" data-testid="telegram-inbound-error">
          {telegramErrorText(inbound.error_code, inbound.message)}
          <details className="tech-detail">
            <summary>Chi tiết kỹ thuật</summary>
            <code>{telegramTechDetail({ error_code: inbound.error_code, message: inbound.message })}</code>
          </details>
        </div>
      ) : null}
      {showCode && pair ? (
        <div className="telegram-pair" data-testid="telegram-pair" role="status">
          <div className="mono" style={{ fontSize: 28, letterSpacing: 4 }} data-testid="telegram-pair-code">
            {spacedCode(pair.code)}
          </div>
          <p className="muted-note">{pairInstruction(pair.bot_username ?? t.bot_username, pair.code)}</p>
          <p className="muted-note">{`${PAIR_CODE_HINT} Hết hạn lúc ${fmtDMClock(pair.expires_at, tz)}.`}</p>
        </div>
      ) : null}
      <div className="conn-card__actions">
        <Button variant="secondary" className="btn-27" icon="ph ph-link" loading={start.isPending} onClick={() => start.mutate()}>
          {inbound.paired ? 'Ghép lại' : 'Ghép Telegram'}
        </Button>
        {inbound.paired ? (
          <Button variant="ghost" className="btn-27" icon="ph ph-link-break" onClick={() => setConfirmUnpair(true)}>
            Gỡ ghép
          </Button>
        ) : null}
      </div>
      {err ? <InlineError detail={errorDetail(err)}>{inboundErrorText(err)}</InlineError> : null}
      <p className="muted-note" role="note" data-testid="telegram-inbound-note">
        <Icon name="ph ph-shield-check" size={12} /> {INBOUND_NOTE}
      </p>
      {confirmUnpair ? <UnpairDialog onClose={() => setConfirmUnpair(false)} onDone={() => setPair(null)} /> : null}
    </div>
  );
}

/** Lỗi API của phần Nhận tin: mã đã biết ⇒ câu riêng; còn lại ⇒ câu chung của errorText. Luôn là chuỗi. */
function inboundErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code && (e.code === 'TELEGRAM_NOT_PAIRED' || e.code.startsWith('TELEGRAM_'))) {
    return telegramErrorText(e.code, e.message);
  }
  return errorText(e);
}

function UnpairDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const del = useMutation({
    mutationFn: () => api.notify.unpairTelegram(),
    onSuccess: (c) => {
      applyConfig(c);
      toast('Đã gỡ ghép — bot không còn nhận tin từ Sếp.', 'ok');
      onDone();
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Gỡ ghép Telegram?"
      kicker={INBOUND_TITLE}
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Giữ lại
          </Button>
          <Button variant="primary" loading={del.isPending} onClick={() => del.mutate()}>
            Gỡ ghép
          </Button>
        </>
      }
    >
      <p className="help-text">
        Bot sẽ ngừng nhận tin từ tài khoản Telegram đã ghép. Báo động, bản tin, nhắc việc vẫn gửi như cũ. Muốn nhắn Gen qua Telegram lại, Sếp ghép lại bằng mã 6 số.
      </p>
      {del.isError ? <InlineError detail={errorDetail(del.error)}>{errorText(del.error)}</InlineError> : null}
    </Dialog>
  );
}

function TestResult({ r, host }: { r: TelegramTestResult; host: TelegramConfig['host'] | null }) {
  if (r.status === 'pass') {
    return (
      <div className="muted-note" role="status" data-testid="telegram-test-result" data-status="pass">
        <Icon name="ph ph-check-circle" size={12} /> {testOkText(r, host)}
      </div>
    );
  }
  return (
    <div className="muted-note friendly-error" role="status" data-testid="telegram-test-result" data-status="fail">
      {`${r.transient ? '' : 'Lỗi · '}${telegramErrorText(r.error_code, r.message)}`}
      <details className="tech-detail">
        <summary>Chi tiết kỹ thuật</summary>
        <code>{telegramTechDetail(r)}</code>
      </details>
    </div>
  );
}

function DisableDialog({ onClose }: { onClose: () => void }) {
  const del = useMutation({
    mutationFn: () => api.notify.deleteTelegram(),
    onSuccess: (c) => {
      applyConfig(c);
      toast('Đã tắt Telegram — không còn báo động & bản tin qua Telegram.', 'ok');
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      width={440}
      title="Tắt Telegram?"
      kicker="Báo động & bản tin"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Giữ lại
          </Button>
          <Button variant="primary" loading={del.isPending} onClick={() => del.mutate()}>
            Tắt Telegram
          </Button>
        </>
      }
    >
      <p className="help-text">
        Gen-Harness sẽ xoá token bot đã lưu và ngừng gửi báo động sự cố, bản tin, nhắc việc qua Telegram. Muốn bật lại phải dán token và chọn chat từ đầu.
      </p>
      {del.isError ? <InlineError detail={errorDetail(del.error)}>{errorText(del.error)}</InlineError> : null}
    </Dialog>
  );
}

/**
 * Khối "Trực canh máy chủ": lần chạy gần nhất, lịch, sự cố đang mở, tin thử từ máy chủ; cảnh báo key_mismatch (nút
 * "Lưu lại" gửi PUT {} qua PIN — giữ token và chat_id), gửi lỗi (câu theo mã + Chi tiết kỹ thuật), genh cũ.
 */
function HostBlock({ t, canResave }: { t: TelegramConfig; canResave: boolean }) {
  const tz = useOrgTimezone();
  const host = t.host && typeof t.host === 'object' ? t.host : null;
  const warn = hostWarning(host);
  const warnDetail = hostWarningDetail(host);
  const incidents = Array.isArray(host?.incidents) ? host.incidents.filter((i) => i && typeof i.title === 'string') : [];
  const state = str(host?.state);
  const hostTest = hostTestText(host?.test, (iso) => fmtDMClock(iso, tz));
  const resave = useMutation({
    mutationFn: () => api.notify.saveTelegram({}),
    onSuccess: (c) => {
      applyConfig(c);
      toast('Đã lưu lại cấu hình Telegram cho máy chủ.', 'ok');
    },
  });
  return (
    <div className="telegram-host" data-testid="telegram-host" aria-label="Trực canh máy chủ" role="group">
      <div className="gh-card__kicker">Trực canh máy chủ</div>
      {warn ? (
        <div className="muted-note friendly-error" role="alert" data-testid="telegram-host-warning">
          <Icon name="ph ph-warning" size={12} /> {warn}
          {canResave && host?.telegram === 'key_mismatch' ? (
            <>
              {' '}
              <Button variant="secondary" className="btn-27" loading={resave.isPending} onClick={() => resave.mutate()}>
                Lưu lại
              </Button>
            </>
          ) : null}
          {warnDetail ? (
            <details className="tech-detail">
              <summary>Chi tiết kỹ thuật</summary>
              <code>{warnDetail}</code>
            </details>
          ) : null}
        </div>
      ) : null}
      {resave.isError ? <InlineError detail={errorDetail(resave.error)}>{errorText(resave.error)}</InlineError> : null}
      {host?.supported !== false ? (
        <ul className="muted-note" style={{ margin: 0, paddingLeft: 18 }}>
          <li>{`Lần chạy gần nhất: ${str(host?.last_run_at) ? fmtDMClock(host?.last_run_at, tz) : 'chưa chạy'}${state ? ` · ${HOST_STATE_LABEL[state] ?? state}` : ''}`}</li>
          <li>{`Lịch: ${scheduleText(str(host?.schedule))}`}</li>
          <li>
            {incidents.length
              ? `Sự cố đang mở: ${incidents.map((i) => `${i.title}${str(i.since) ? ` (từ ${fmtDMClock(i.since, tz)})` : ''}`).join('; ')}`
              : 'Không có sự cố đang mở'}
          </li>
          {hostTest ? <li data-testid="telegram-host-test">{hostTest}</li> : null}
        </ul>
      ) : null}
    </div>
  );
}
