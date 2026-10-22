import { useEffect, useState } from 'react';
import { KHO_LABEL, KHO_LABEL_MAX, type HubLink, type HubLinkTestResult } from '@gen-harness/contracts';
import { Button, Icon, Switch, TextField } from '@gen-harness/ui';
import { DefaultControls } from '../../defaults/ResetButton';
import { errorText } from '../../lib/errorText';
import { useMe } from '../../lib/queries';
import { CardError, FriendlyErrorText, InlineError, Panel, SkeletonLines } from '../common';
import { ConnectionStatusPill } from '../connections/ConnectionStatusPill';
import { hubStatus } from '../connections/connectionsModel';
import { hubTokenExpiry } from '../../guide/bossChecksModel';
import {
  HUB_BREAKER_STRIP,
  HUB_STATUS_LABEL,
  PUBLIC_NET_HINT,
  SCOPES_READ_ONLY_TEXT,
  WRITE_CONFIRM_ONLY_TEXT,
  HUB_KICKER,
  expiryToIso,
  hubStatusTone,
  isPublicHttpsUrl,
  isoToDay,
  otherWriteTools,
  scopeRows,
  scopesMessage,
  testedToolsText,
  writeScopeRows,
  writeScopesMessage,
} from './mcpModel';
import { useHubLink, useTestHubLink, useUpdateHubLink } from './queries';

/** Hôm nay + 90 ngày (giờ VN) — hạn điền sẵn của token Gen-hub thủ công. */
const defaultExpiryDay = () => isoToDay(hubTokenExpiry());

const fmtTime = (iso: string | null) => (iso ? new Date(iso).toLocaleString('vi-VN') : '—');

/**
 * v0.1.42 (F-61): thẻ này chỉ render ở Kết nối (/connections#genhub) — MCP Hub chỉ còn dòng liên kết tới đây.
 * Gen-hub (v0.1.26, docs/design/gen-hub-link.md §3): Gen đọc Kho dữ liệu qua Gen-hub — chỉ Sếp (Owner). Từ v0.1.50 Gen ghi được
 * Phiên / Việc vào Kho, nhưng CHỈ khi Sếp bấm Xác nhận + nhập mã PIN trên thẻ đề xuất.
 * Token là ô CHỈ GHI: API không bao giờ trả lại (chỉ biết "đã lưu"). Liên kết tắt tới khi bấm "Kiểm tra" xanh;
 * đổi địa chỉ/token thì tắt lại, phải kiểm tra lại. Lưu / Kiểm tra cần PIN (`hub.link`), ghi Nhật ký hành động.
 * v0.1.39 (F-31): gõ địa chỉ https công khai → công tắc "mạng công cộng" bật sẵn (Owner vẫn bỏ được); "Kiểm tra" khi
 * còn thay đổi chưa lưu = lưu rồi kiểm tra luôn (một lần PIN — phiên PIN của lần lưu phủ lần kiểm tra).
 * v0.1.55: ô "Ngày hết hạn token" điền sẵn hôm nay + 90 ngày khi còn trống (token thủ công hết hạn sau 90 ngày) — Owner vẫn sửa
 * được; ô chưa đụng tới thì chỉ gửi kèm khi Owner đang lưu một token mới (mở thẻ rồi bấm Kiểm tra không tự ghi hạn).
 * v0.1.49 (QD-16): khối "Quyền đọc thêm (tuỳ chọn)" — lịch, mail, việc, Drive (Gen chỉ đọc); sau Kiểm tra báo quyền còn
 * thiếu (không làm Kiểm tra đỏ); bộ ngắt F-83 đang mở ⇒ dải "Gen-hub tạm không trả lời".
 * v0.1.50 (F-81, QD-18): khối "Quyền ghi Kho (tuỳ chọn)" dưới "Quyền đọc thêm" — kho_create / kho_update; Gen chỉ ghi khi Sếp
 * Xác nhận + nhập mã PIN trên thẻ đề xuất.
 * v0.1.57 (Nợ #30): ô "Tên Kho (tuỳ chọn)" — Owner tự đặt tên hiển thị của Kho (≤ 40 ký tự; trống = "Kho dữ liệu"); chip Mặc định / Đã
 * đổi + Về mặc định lấy từ sổ mặc định (`kho_label`). Chỉ đổi tên (không đụng địa chỉ/token) vẫn lưu được, kể cả khi chưa nối Gen-hub.
 */
export function HubLinkCard() {
  const me = useMe();
  const isOwner = me.data?.role?.code === 'owner';
  const link = useHubLink();
  return (
    <Panel
      title={`Gen-hub — Gen đọc và ghi ${link.data?.kho_label?.trim() || KHO_LABEL}`}
      kicker={
        link.data ? (
          <>
            <span style={{ color: hubStatusTone(link.data.status) }}>{HUB_STATUS_LABEL[link.data.status]}</span> · {HUB_KICKER}
          </>
        ) : (
          HUB_KICKER
        )
      }
      label="Gen-hub"
      genTarget="mcp.hub_link"
      bodyClass="hub-link"
      aside={link.data ? <ConnectionStatusPill status={hubStatus(link.data)} /> : undefined}
    >
      {link.isPending ? (
        <SkeletonLines rows={3} padding="0" />
      ) : link.isError ? (
        <CardError error={link.error} onRetry={() => void link.refetch()} retrying={link.isFetching} />
      ) : (
        <HubLinkBody link={link.data} isOwner={isOwner} />
      )}
    </Panel>
  );
}

function HubLinkBody({ link, isOwner }: { link: HubLink; isOwner: boolean }) {
  const update = useUpdateHubLink();
  const test = useTestHubLink();
  const [endpoint, setEndpoint] = useState(link.endpoint ?? '');
  const [token, setToken] = useState('');
  const savedDay = isoToDay(link.token_expires_at);
  const [expiry, setExpiry] = useState(savedDay || defaultExpiryDay());
  const [expiryTouched, setExpiryTouched] = useState(false);
  const [publicNet, setPublicNet] = useState(link.allow_public_network);
  // Owner đã tự bấm công tắc → không tự bật/tắt theo địa chỉ nữa.
  const [publicTouched, setPublicTouched] = useState(false);
  const [publicAuto, setPublicAuto] = useState(false);
  useEffect(() => {
    setEndpoint(link.endpoint ?? '');
    setExpiry(isoToDay(link.token_expires_at) || defaultExpiryDay());
    setExpiryTouched(false);
    setPublicNet(link.allow_public_network);
    setPublicAuto(false);
  }, [link.endpoint, link.token_expires_at, link.allow_public_network]);
  const onEndpoint = (v: string) => {
    setEndpoint(v);
    if (!publicTouched) {
      const pub = isPublicHttpsUrl(v);
      setPublicNet(pub);
      setPublicAuto(pub);
    }
  };
  const onPublic = (on: boolean) => {
    setPublicTouched(true);
    setPublicAuto(false);
    setPublicNet(on);
  };

  // v0.1.57: Tên Kho — chỉ giữ chữ khi Owner đã đổi (khác mặc định); ô trống = mặc định.
  const khoMax = link.kho_label_max ?? KHO_LABEL_MAX;
  const savedLabel = link.kho_label_custom ? (link.kho_label ?? '') : '';
  const [label, setLabel] = useState(savedLabel);
  useEffect(() => {
    setLabel(savedLabel);
  }, [savedLabel]);
  const labelTrim = label.trim();
  const labelDirty = labelTrim !== savedLabel;
  const labelError = labelTrim.length > khoMax ? `Tên Kho tối đa ${khoMax} ký tự` : null;

  const firstTime = !link.configured;
  const linkValid = /^https?:\/\/\S+$/.test(endpoint.trim()) && (!firstTime || token.trim().length >= 8) && (!token.trim() || token.trim().length >= 8);
  // Hạn điền sẵn (chưa có hạn đã lưu, Owner chưa sửa) chỉ đi kèm khi đang lưu một token mới; Owner tự sửa thì gửi nếu khác hạn đã lưu.
  const expiryAuto = !expiryTouched && !savedDay;
  const expiryChanged = expiryTouched ? expiry !== savedDay : expiryAuto && token.trim() !== '';
  const linkDirty = endpoint.trim() !== (link.endpoint ?? '') || token.trim() !== '' || expiryChanged || publicNet !== link.allow_public_network;
  const dirty = linkDirty || labelDirty;
  // Chỉ đổi tên Kho thì không đòi địa chỉ/token hợp lệ; có đổi địa chỉ/token thì vẫn đòi như cũ.
  const valid = !labelError && (!linkDirty || linkValid);

  const buildBody = () => {
    const body: Parameters<typeof update.mutate>[0] = {};
    if (endpoint.trim() !== (link.endpoint ?? '')) body.endpoint = endpoint.trim();
    if (token.trim()) body.token = token.trim();
    if (expiryChanged) body.token_expires_at = expiryToIso(expiry);
    if (publicNet !== link.allow_public_network) body.allow_public_network = publicNet;
    if (labelDirty) body.kho_label = labelTrim;
    return body;
  };
  const save = () => {
    if (!valid || !dirty) return;
    update.mutate(buildBody(), { onSuccess: () => setToken('') });
  };
  // "Kiểm tra" khi còn thay đổi: lưu trước rồi kiểm tra (lỗi lưu hiện ở dòng lỗi lưu, không kiểm tra).
  const saveAndTest = async () => {
    if (dirty) {
      if (!valid) return;
      try {
        await update.mutateAsync(buildBody());
      } catch {
        return;
      }
      setToken('');
    }
    test.mutate();
  };
  const result = test.data;
  const blocked = result && !result.ok && result.error_code === 'MCP_NETWORK_BLOCKED';

  return (
    <>
      <dl className="jev-dl">
        <dt>Địa chỉ</dt>
        <dd className="mono">{link.endpoint ?? 'Chưa nối'}</dd>
        <dt>Token</dt>
        <dd>
          <Icon name={link.has_token ? 'ph ph-lock-key' : 'ph ph-lock-key-open'} size={12} /> {link.has_token ? 'Đã lưu (mã hoá, không hiện lại)' : 'Chưa có'}
        </dd>
        <dt>Hạn token</dt>
        <dd>{link.token_expires_at ? `${isoToDay(link.token_expires_at)} · còn ${link.days_left ?? 0} ngày` : 'Chưa nhập'}</dd>
        <dt>Kiểm tra xanh gần nhất</dt>
        <dd>{fmtTime(link.last_ok_at)}</dd>
        {link.last_error ? (
          <>
            <dt>Lỗi gần nhất</dt>
            <dd style={{ color: 'var(--color-bad)' }}>{link.last_error}</dd>
          </>
        ) : null}
      </dl>

      {link.breaker?.open === true ? (
        <p className="hub-breaker" role="note" data-testid="hub-breaker">
          <Icon name="ph ph-warning" size={12} /> {HUB_BREAKER_STRIP}
        </p>
      ) : null}

      {isOwner ? (
        <form
          className="jev-form"
          onSubmit={(e) => {
            e.preventDefault();
            save();
          }}
        >
          <TextField label="Địa chỉ Gen-hub" value={endpoint} onChange={(e) => onEndpoint(e.target.value)} placeholder="https://<địa-chỉ-gen-hub-của-bạn>/mcp" className="mono" />
          <TextField
            label={link.has_token ? 'Token mới (bỏ trống để giữ token đã lưu)' : 'Token Gen-hub'}
            type="password"
            autoComplete="off"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Dán token agent gen-harness-… tạo trong Gen-hub"
            data-gen-target="mcp.hub_link.token"
          />
          <TextField
            label="Ngày hết hạn token"
            type="date"
            value={expiry}
            onChange={(e) => {
              setExpiryTouched(true);
              setExpiry(e.target.value);
            }}
            hint={`Token thủ công của Gen-hub hết hạn sau 90 ngày — Gen nhắc Sếp trước 14 ngày.${expiryAuto ? ' Em đã điền sẵn hôm nay + 90 ngày, Sếp sửa được nếu token có hạn khác.' : ''}`}
          />
          <div className="triage-row">
            <Switch checked={publicNet} label="Cho phép Gen-hub ở mạng công cộng" onChange={onPublic} />
            <span>Gen-hub ở Internet (mạng công cộng) — chỉ bật cho máy chủ này</span>
          </div>
          {publicAuto && publicNet ? (
            <p className="muted-note hub-public-note" role="note">
              <Icon name="ph ph-globe" size={12} /> Đã bật sẵn vì địa chỉ là https công khai — Gen-hub sẽ được gọi qua Internet. Bỏ tích nếu Gen-hub nằm trong mạng nội bộ.
            </p>
          ) : null}
          <TextField
            label="Tên Kho (tuỳ chọn)"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            placeholder={KHO_LABEL}
            maxLength={khoMax + 20}
            error={labelError}
            hint={`Tên hiển thị của Kho trong Gen-Harness (thẻ đề xuất, câu Gen trả lời…), tối đa ${khoMax} ký tự. Để trống = "${KHO_LABEL}".`}
            after={<DefaultControls itemKey="kho_label" />}
          />
          <div className="jev-actions">
            <Button variant="primary" type="submit" className="btn-27" icon="ph ph-floppy-disk" disabled={!valid || !dirty} loading={update.isPending}>
              Lưu
            </Button>
            <Button
              variant="secondary"
              type="button"
              className="btn-27"
              icon="ph ph-pulse"
              data-main-action
              disabled={dirty ? !valid || !(link.configured || linkDirty) : !link.configured}
              loading={test.isPending || (update.isPending && dirty)}
              data-gen-target="mcp.hub_link.test"
              onClick={() => void saveAndTest()}
            >
              {dirty ? 'Lưu & kiểm tra' : 'Kiểm tra'}
            </Button>
            {link.enabled ? (
              <Button variant="ghost" type="button" className="btn-27" loading={update.isPending && update.variables?.enabled === false} onClick={() => update.mutate({ enabled: false })}>
                Tắt
              </Button>
            ) : null}
          </div>
          {update.isError ? <InlineError>{errorText(update.error)}</InlineError> : null}
          {test.isError ? <InlineError>{errorText(test.error)}</InlineError> : null}
          {result ? (
            <div className={result.ok ? 'apm-test-result apm-test-result--ok' : 'apm-test-result apm-test-result--bad'} role="status">
              {result.ok ? (
                `Đã nối Kho · ${result.latency_ms} ms · ${testedToolsText(result)}`
              ) : (
                <>
                  <FriendlyErrorText raw={blocked ? PUBLIC_NET_HINT : result.error} fallback="Chưa kết nối được Gen-hub — kiểm tra địa chỉ và thẻ truy cập." />
                  {result.error_code ? (
                    <details className="tech-detail">
                      <summary>Chi tiết kỹ thuật</summary>
                      <code>Mã lỗi {result.error_code}</code>
                    </details>
                  ) : null}
                </>
              )}
            </div>
          ) : null}
          <ReadScopes link={link} result={result} />
          <WriteScopes link={link} result={result} />
          {/* v0.1.28 (UX V14): từng bước bằng lời thường, không tên riêng. */}
          <ol className="muted-note hub-steps">
            <li>Mở Gen-hub, vào mục tạo trợ lý mới, đặt tên có tên công ty (ví dụ gen-harness-congty).</li>
            <li>
              Chọn thời hạn thẻ truy cập 90 ngày. Bật quyền ĐỌC Kho (tóm tắt, tìm, xem một mục) và (tuỳ chọn) quyền đọc lịch, đọc mail, đọc việc, tìm Drive —
              KHÔNG bật quyền ghi lịch, mail hay Drive. Muốn Gen ghi được Kho (Phiên, Việc), tick thêm quyền ghi Kho (tạo và sửa bản ghi — tên trong Gen-hub: kho_create, kho_update) — không bắt buộc.
            </li>
            <li>Chép thẻ truy cập (token) vừa tạo, dán vào ô trên rồi bấm Kiểm tra.</li>
          </ol>
          <p className="muted-note">Nội dung Kho được che số tài khoản, SĐT, email, khoá trước khi gửi cho AI.</p>
        </form>
      ) : (
        <p className="muted-note">Chỉ Sếp (Owner) cấu hình và dùng Gen-hub.</p>
      )}
    </>
  );
}

/**
 * v0.1.49 (QD-16): "Quyền đọc thêm (tuỳ chọn)" — 4 dòng Có / Chưa / Chưa kiểm lấy từ lần Kiểm tra XANH vừa xong
 * (`test.data.read_scopes`) hoặc, chưa bấm, từ lần kiểm xanh gần nhất máy chủ nhớ (`link.read_scopes`). Chưa nối / chưa
 * từng kiểm xanh (`last_ok_at` trống) ⇒ 4 dòng "Chưa kiểm", không lời nhắc. Thiếu quyền đọc chỉ là lời nhắc — không làm
 * Kiểm tra đỏ. Mọi giá trị từ máy chủ được kiểm kiểu trước khi hiện (chỉ chuỗi / boolean).
 */
function ReadScopes({ link, result }: { link: HubLink; result: HubLinkTestResult | undefined }) {
  const fromLink = link.configured && !!link.last_ok_at ? link.read_scopes : undefined;
  // Kiểm tra vừa đỏ (không nối được) thì chưa nói gì về quyền: dùng lần kiểm xanh gần nhất (nếu có), không lời nhắc.
  const scopes = result?.ok ? (result.read_scopes ?? fromLink) : fromLink;
  const rows = scopeRows(scopes);
  const msg = result ? (result.ok ? scopesMessage(result.read_missing, scopes) : null) : scopesMessage(undefined, scopes);
  const writeTools = result ? otherWriteTools(result.write_tools) : [];
  return (
    <div className="hub-scopes" data-testid="hub-scopes">
      <p className="hub-scopes__title">Quyền đọc thêm (tuỳ chọn)</p>
      <ul className="hub-scopes__list">
        {rows.map((r) => (
          <li key={r.key} className="hub-scopes__row" data-has={r.state === 'unknown' ? undefined : r.state}>
            <span className="hub-scopes__name">{r.label}</span>
            <span className="hub-scopes__val">{r.text}</span>
          </li>
        ))}
      </ul>
      {msg ? (
        <p className="hub-scopes__msg" data-tone={msg.tone} role="note">
          {msg.text}
        </p>
      ) : null}
      {writeTools.length > 0 ? (
        <p className="hub-scopes__msg" data-tone="warn" role="note">
          Token đang có thêm quyền GHI ({writeTools.join(', ')}) — Gen không bao giờ dùng, nên tắt các quyền này trong Gen-hub.
        </p>
      ) : null}
      <p className="muted-note">{SCOPES_READ_ONLY_TEXT}</p>
    </div>
  );
}

/**
 * v0.1.50 (F-81, QD-18): "Quyền ghi Kho (tuỳ chọn)" — 2 dòng Có / Chưa / Chưa kiểm, MỖI dòng theo đúng tool của nó (kho_create,
 * kho_update; máy chủ cũ chỉ có `kho` ⇒ hai dòng theo `kho`). Owner tự đóng tool ghi ở MCP Hub ⇒ Kiểm tra không mở lại và thẻ nói
 * rõ (tắt hẳn = bỏ tick ở Gen-hub) — cả sau khi tải lại trang (`link.write_hidden`). Nguồn như quyền đọc:
 * lần Kiểm tra XANH vừa xong (`test.data.write_scopes`), không thì lần kiểm xanh gần nhất máy chủ nhớ (`link.write_scopes`); chưa
 * nối / chưa từng kiểm xanh ⇒ "Chưa kiểm", không lời nhắc. Thiếu quyền ghi chỉ là lời nhắc — không làm Kiểm tra đỏ.
 */
function WriteScopes({ link, result }: { link: HubLink; result: HubLinkTestResult | undefined }) {
  const fromLink = link.configured && !!link.last_ok_at ? link.write_scopes : undefined;
  const scopes = result?.ok ? (result.write_scopes ?? fromLink) : fromLink;
  const rows = writeScopeRows(scopes);
  // Chưa bấm Kiểm tra trong lần mở trang này ⇒ tool Owner tự đóng lấy từ `GET /hub/link` (máy chủ tính lại mỗi lần đọc).
  const msg = result
    ? result.ok
      ? writeScopesMessage(result.write_missing, scopes, result.write_hidden)
      : null
    : writeScopesMessage(undefined, scopes, fromLink !== undefined ? link.write_hidden : undefined);
  return (
    <div className="hub-scopes hub-write-scopes" data-testid="hub-write-scopes">
      <p className="hub-scopes__title">Quyền ghi Kho (tuỳ chọn)</p>
      <ul className="hub-scopes__list">
        {rows.map((r) => (
          <li key={r.key} className="hub-scopes__row" data-has={r.state === 'unknown' ? undefined : r.state}>
            <span className="hub-scopes__name">
              {r.label} (<span className="mono">{r.key}</span>)
            </span>
            <span className="hub-scopes__val">{r.text}</span>
          </li>
        ))}
      </ul>
      {msg ? (
        <p className="hub-scopes__msg" data-tone={msg.tone} role="note">
          {msg.text}
        </p>
      ) : null}
      <p className="muted-note">{WRITE_CONFIRM_ONLY_TEXT}</p>
    </div>
  );
}
