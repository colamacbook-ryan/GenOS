import { useEffect, useState } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQueryClient } from '@tanstack/react-query';
import { EmptyState, Icon } from '@gen-harness/ui';
import { qk2, useProviders } from '../../lib/dataQueries';
import { useTestProvider } from '../api/queries';
import { chainModelText, fmtQuota, providerStatus } from '../api/apiModel';
import { CardError, Panel, SkeletonLines } from '../common';
import { N4, OK, WARN } from '../data/dataModel';
import { JevCard } from './JevCard';
import { TriageCard } from './TriageCard';
import { BackgroundSourcesCard } from './BackgroundSourcesCard';
import { GenMemoryCard } from './GenMemoryCard';
import { GenCoachCard } from './GenCoachCard';
import { AiBudgetCard } from './AiBudgetCard';
import { useCan } from '../../lib/permissions';
import { StandardModeStrip } from '../../defaults/StandardModeStrip';
import { AI_SOURCE_PATH } from '../../setup/steps';

/**
 * Bộ não AI — góc nhìn vận hành hệ thống (PLAN 4.5): hạn mức theo model, chuỗi chuyển hướng, tài khoản CLI
 * (v0.1.66: không còn chép lại thẻ "Quy tắc chuyển hướng" — chỉ có ở API & Model). Cấu hình chi tiết provider/khoá/gán model theo agent đã có màn riêng `api`
 * (`screens/api/ApiScreen.tsx`, PLAN 4.2) — tab này CHỈ ĐỌC LẠI cùng dữ liệu (`useProviders`), không lặp lại logic sửa/thêm/kéo-thả, và dẫn sang màn đó cho việc cấu hình đầy đủ.
 */
export function BrainTab() {
  // v0.1.42 (F-7): Cài đặt mở cho cả `audit.read` — vai trò không có `system.read` thấy ổ khoá, KHÔNG gọi /providers.
  const canRead = useCan('system.read');
  if (!canRead) {
    return (
      <div className="gh-card">
        <EmptyState
          icon="ph ph-lock-simple"
          title="Vai trò của bạn không xem được Bộ não AI"
          description="Model, hạn mức và chuỗi chuyển hướng chỉ hiện với vai trò có quyền xem hệ thống."
        />
      </div>
    );
  }
  return <BrainTabBody />;
}

export function BrainTabBody() {
  const providers = useProviders();
  const canManage = useCan('system.manage');
  const qc = useQueryClient();
  const hash = useLocation().hash;
  const jevHash = hash === '#jev';
  const memoryHash = hash === '#gen-memory';
  const coachHash = hash === '#gen-coach';
  const standardHash = hash === '#chuan';
  // v0.1.43: "Nhập khoá Jev" (Kiểm tra của Boss) dẫn tới #jev — thẻ ở lưới dưới, mở sẵn rồi cuộn tới (giống Kết nối).
  useEffect(() => {
    if (!jevHash) return;
    const t = window.setTimeout(() => document.getElementById('jev')?.scrollIntoView?.({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [jevHash]);
  // v0.1.50 (F-81): "Xem ở Cài đặt" trên thẻ Ghi nhớ dẫn tới #gen-memory — cuộn tới thẻ Gen nhớ.
  useEffect(() => {
    if (!memoryHash) return;
    const t = window.setTimeout(() => document.getElementById('gen-memory')?.scrollIntoView?.({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [memoryHash]);
  // v0.1.54: liên kết tới thẻ "Gen hướng dẫn" (`#gen-coach`) — cuộn tới thẻ.
  useEffect(() => {
    if (!coachHash) return;
    const t = window.setTimeout(() => document.getElementById('gen-coach')?.scrollIntoView?.({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [coachHash]);
  // v0.1.55: gợi ý "Áp model chuẩn theo vai?" (Hôm nay, Gen) dẫn tới `#chuan` — cuộn tới dải "Chế độ tiêu chuẩn".
  useEffect(() => {
    if (!standardHash) return;
    const t = window.setTimeout(() => document.getElementById('chuan')?.scrollIntoView?.({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [standardHash]);
  // v0.1.39 (F-78): Jev không bắt buộc — kiểm tra lỗi thì thu thẻ vào "Nâng cao" thay vì để lỗi đỏ giữa tab.
  // Mutation giữ ở đây để kết quả "Kiểm tra 1 lần" còn nguyên khi thẻ chuyển chỗ.
  const jevTest = useTestProvider();
  const [jevFailedNow, setJevFailedNow] = useState(false);
  const jev = (providers.data ?? []).find((p) => p.kind === 'system_one');
  const jevFailed =
    !!jev && (jevFailedNow || jev.last_test?.ok === false || jev.auth_state === 'error' || jev.auth_state === 'expired');
  const onJevFailed = () => {
    setJevFailedNow(true);
    void qc.invalidateQueries({ queryKey: qk2.providers });
  };
  // Một phần tử duy nhất trong <details> — đổi summary không gỡ thẻ nên kết quả kiểm tra không mất.
  const jevCard = <JevCard test={jevTest} onFailed={onJevFailed} />;
  // Jev (system_one) không nằm trong chuỗi sinh chữ — hiện riêng ở thẻ Jev.
  const sorted = [...(providers.data ?? [])].filter((p) => p.kind !== 'system_one').sort((a, b) => a.failover_rank - b.failover_rank);
  const models = sorted.flatMap((p) => p.models.map((m) => ({ ...m, providerName: p.name, providerId: p.id, enabled: p.enabled })));

  return (
    <div className="sys-tabs-col">
      {/* v0.1.55: "Chế độ tiêu chuẩn: đang dùng / đã đổi N mục · Về mặc định tất cả" (chỉ Owner thấy dải). */}
      <StandardModeStrip />
      <Panel
        title="Hạn mức theo model"
        genTarget="system.brain.quota"
        kicker="Mức dùng trong ngày của từng model"
        label="Hạn mức theo model"
        bodyClass="brain-quota-wrap"
        aside={
          <Link to="/api" className="gh-btn gh-btn--secondary btn-27" data-gen-target="system.brain.open_api">
            <Icon name="ph ph-arrow-square-out" size={13} />
            Mở API &amp; Model
          </Link>
        }
      >
        {providers.isPending ? (
          <SkeletonLines rows={4} padding="10px 16px" />
        ) : providers.isError ? (
          <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
        ) : models.length === 0 ? (
          // v0.1.70 (F029): MỘT đường tới "thêm nguồn AI" (cùng `readiness.FIX_AI` của máy chủ) — màn Thiết lập nguồn AI;
          // "Mở API & Model" ở đầu thẻ là đích khi nguồn có nhưng đang tắt (`readiness.FIX_API`).
          <EmptyState
            icon="ph ph-brain"
            title="Chưa có model nào"
            description="Thêm nguồn AI (dán khoá API hoặc đăng nhập Google / Claude) ở màn Thiết lập nguồn AI."
            action={
              canManage ? (
                <Link to={AI_SOURCE_PATH} className="gh-btn gh-btn--secondary btn-27" data-testid="brain-add-source">
                  <Icon name="ph ph-plus-circle" size={13} />
                  Thêm / kiểm tra nguồn AI
                </Link>
              ) : undefined
            }
          />
        ) : (
          <table className="brain-quota">
            <thead>
              <tr>
                <th>Model</th>
                <th>Nhà cung cấp</th>
                <th>Dùng trong ngày</th>
                <th>Còn lại</th>
                <th>Trạng thái</th>
              </tr>
            </thead>
            <tbody>
              {models.map((m) => {
                const pct = m.daily_quota ? Math.min(100, (m.used_today * 100) / m.daily_quota) : 0;
                const leftPct = m.daily_quota ? Math.max(0, 100 - pct) : null;
                const tone = !m.enabled ? N4 : leftPct == null ? OK : leftPct < 20 ? WARN : OK;
                return (
                  <tr key={m.id}>
                    <td className="mono">{m.model_name}</td>
                    <td>{m.providerName}</td>
                    <td>
                      <div className="brain-quota__bar">
                        <span className="brain-quota__track">
                          <span className="brain-quota__fill" style={{ width: `${pct}%`, background: tone }} />
                        </span>
                        <span className="mono brain-quota__used">{fmtQuota(m.used_today, m.daily_quota)}</span>
                      </div>
                    </td>
                    <td className="mono" style={{ color: tone }}>
                      {leftPct == null ? '—' : `${leftPct.toFixed(0)}%`}
                    </td>
                    <td>
                      <span className="brain-quota__state" style={{ color: tone }}>
                        {!m.enabled ? 'Đã tắt' : leftPct != null && leftPct < 20 ? 'Sắp cạn hạn mức' : 'Khoẻ mạnh'}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>

      {/* v0.1.66 (F137): thêm / sửa nguồn AI, chuỗi và quy tắc chuyển hướng chỉ ở màn API & Model — tab này chỉ xem lại. */}
      <Panel genTarget="system.brain.chain" title="Chuỗi chuyển hướng" kicker="Thứ tự dùng nguồn — xem; sửa và xem quy tắc chuyển hướng ở màn API & Model" label="Chuỗi chuyển hướng" bodyClass="brain-chain">
        {providers.isPending ? (
          <SkeletonLines rows={3} padding="10px 16px" />
        ) : providers.isError ? (
          <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
        ) : sorted.length === 0 ? (
          <EmptyState icon="ph ph-arrows-down-up" title="Chưa có nhà cung cấp nào" />
        ) : (
          <ol className="brain-chain-list">
            {sorted.map((p, i) => (
              <li className="brain-chain-row" key={p.id}>
                <span className="mono brain-chain-row__rank">{String(i + 1).padStart(2, '0')}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="brain-chain-row__name">{p.name}</div>
                  <div className="mono brain-chain-row__model">{chainModelText(p)}</div>
                </div>
                <span className="brain-chain-row__state" style={{ color: providerStatus(p).tone }}>
                  {providerStatus(p).label}
                </span>
              </li>
            ))}
          </ol>
        )}
      </Panel>

      {/* v0.1.50 (F-81, QD-18): Gen nhớ — chỉ Owner thấy (thẻ tự ẩn với vai trò khác, không gọi /gen/memory). */}
      <GenMemoryCard />

      {/* v0.1.54: Gen hướng dẫn — công tắc, chuông nhắc, số bài mỗi ngày, giờ yên lặng, việc đã tắt (chỉ Owner thấy thẻ). */}
      <GenCoachCard />

      <div className="sys-grid2">
        {/* v0.1.43 (F-30): Jev không bắt buộc — luôn nằm trong "Nâng cao"; tới bằng `#jev` (vd từ Kiểm tra của Boss) thì mở sẵn. */}
        <details className="brain-advanced" id="jev" open={jevHash || undefined}>
          <summary>{jevFailed ? 'Nâng cao — Jev (đã ẩn vì kiểm tra lỗi, không bắt buộc)' : 'Nâng cao — Jev (không bắt buộc)'}</summary>
          {jevCard}
        </details>
        {/* v0.1.42 (F-61): thẻ tài khoản CLI chỉ ở một chỗ — Kết nối › Bộ não AI. */}
        <div className="gh-card brain-cli-link">
          <Link to="/connections#brain" className="sys-link">
            <Icon name="ph ph-plugs-connected" size={13} />
            Tài khoản Google / Claude CLI ở Kết nối
          </Link>
        </div>
      </div>

      {/* v0.1.41 (F-86, F-84): nguồn AI cho việc nền (khoá API và Antigravity CLI; Claude Code CLI cần cảnh báo + PIN) và chi phí. */}
      <div className="sys-grid2">
        <BackgroundSourcesCard />
        <AiBudgetCard />
      </div>

      <div className="sys-grid2">
        <TriageCard />
      </div>
    </div>
  );
}
