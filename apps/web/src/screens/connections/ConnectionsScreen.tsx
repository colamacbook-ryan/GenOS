import { useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { SCREEN_BY_KEY } from '@gen-harness/contracts';
import { EmptyState, Icon } from '@gen-harness/ui';
import { FOLLOW_UP_KEY } from '../../guide/guideContent';
import { api } from '../../lib/api';
import { useChannels, useCliProfiles, useProviders } from '../../lib/dataQueries';
import { useCan } from '../../lib/permissions';
import { useMe } from '../../lib/queries';
import { AI_SOURCE_PATH } from '../../setup/steps';
import { CardError, Panel, ScreenHead, SkeletonLines } from '../common';
import { HubLinkCard } from '../mcp/HubLinkCard';
import { useMcpServers } from '../mcp/queries';
import { ChannelCard } from '../system/ChannelCard';
import { CliCard } from '../system/CliCard';
import { SocialEntryCard } from '../system/SocialEntryCard';
import { ConnectionStatusPill } from './ConnectionStatusPill';
import { OwnerChannelCard } from './OwnerChannelCard';
import { TelegramCard } from './TelegramCard';
import { activeCliState, brainStatus, isKeySource, mcpEnabledCount, mcpStatus, orderChannels, ownerChannelTypes, visibleChannels } from './connectionsModel';

/**
 * v0.1.42 (F-7, F-61): Kết nối — một trang, mỗi thứ một thẻ, cùng một kiểu viên trạng thái (Đang chạy · Cần Sếp xử
 * lý · Chưa nối) và đúng một nút chính. Thứ tự: Bộ não AI · Zalo · WhatsApp · Telegram (· kênh khác) · Facebook ·
 * Gen-hub · MCP. Đây là nơi DUY NHẤT render thẻ tài khoản CLI (CliCard) và thẻ Gen-hub (HubLinkCard).
 */
export function ConnectionsScreen() {
  const meta = SCREEN_BY_KEY.connections;
  const canManage = useCan('system.manage');
  const isOwner = useMe().data?.role?.code === 'owner';
  const { hash } = useLocation();

  // /connections#brain, #genhub, #telegram (link từ Cài đặt › Bộ não AI, MCP Hub, chuông, Bản tin Gen) → cuộn tới thẻ. Cuộn lại khi
  // danh sách kênh tải xong (thẻ kênh nằm trên Gen-hub — khung chờ thấp hơn thẻ thật làm lệch vị trí).
  const channelsReady = !useChannels().isPending;
  useEffect(() => {
    if (!hash) return;
    const id = hash.slice(1);
    const t = window.setTimeout(() => document.getElementById(id)?.scrollIntoView?.({ block: 'start' }), 50);
    return () => window.clearTimeout(t);
  }, [hash, channelsReady, isOwner]);

  return (
    <div className="screen">
      <ScreenHead title={meta.title} description={meta.description} maxWidth={meta.descMaxWidth} />

      <section id="brain" className="conn-section" aria-label="Nguồn AI và tài khoản CLI">
        <div className="conn-grid">
          <BrainCard />
          {/* Nơi DUY NHẤT render thẻ tài khoản CLI (v0.1.42, F-61). */}
          <CliCard canManage={canManage} kind="antigravity_cli" />
          <CliCard canManage={canManage} kind="claude_code_cli" showCredentials={false} />
        </div>
      </section>

      <section className="conn-section" aria-label="Kênh nhắn tin">
        <ChannelCards canManage={canManage} />
      </section>

      {/* v0.1.72: Kênh quản trị Zalo/WhatsApp (bot tài khoản riêng) — chỉ Owner; neo #owner-channel. Chỉ hiện cho kênh đã cài. */}
      {isOwner ? <OwnerChannelSection /> : null}

      {/* v0.1.44 (F-8c): Telegram nhận báo động sự cố (Trực canh máy chủ) + bản tin — chỉ Owner; neo #telegram. */}
      {isOwner ? (
        <section id="telegram" className="conn-section" aria-label="Báo động & bản tin">
          <div className="conn-grid">
            <TelegramCard />
          </div>
        </section>
      ) : null}

      <section className="conn-section" aria-label="Mạng xã hội và công cụ ngoài">
        <div className="conn-grid">
          <SocialEntryCard />
          <div id="genhub" className="conn-anchor">
            <HubLinkCard />
          </div>
          <McpCard />
        </div>
      </section>
    </div>
  );
}

function BrainCard() {
  const isOwner = useMe().data?.role?.code === 'owner';
  const providers = useProviders();
  const agy = useCliProfiles('antigravity_cli');
  const claude = useCliProfiles('claude_code_cli');
  // "Chưa có model" — bước 4 chưa xong (API `GET /setup/follow-up` chỉ trả cho Owner).
  const followUp = useQuery({ queryKey: FOLLOW_UP_KEY, queryFn: ({ signal }) => api.setup.followUp(signal), enabled: isOwner });
  const step4 = Array.isArray(followUp.data) ? followUp.data.find((s) => s.n === 4) : undefined;
  const noModel = !!step4 && !step4.done;
  // v0.1.68 (F017): "nguồn khoá API" chỉ đếm nguồn dán khoá — tài khoản CLI có thẻ riêng ngay cạnh.
  const keys = (providers.data ?? []).filter(isKeySource);
  const enabled = keys.filter((p) => p.enabled).length;
  // Chỉ Owner biết "chưa chọn model" (follow-up) — vai trò khác không có viên, tránh hiện "Đang chạy" sai.
  const status = isOwner && providers.data
    ? brainStatus({ providers: providers.data, cliStatus: [activeCliState(agy.data), activeCliState(claude.data)], noModel })
    : null;
  return (
    <Panel
      title="Bộ não AI"
      genTarget="connections.brain"
      label="Bộ não AI"
      kicker={
        providers.data
          ? noModel
            ? 'Chưa chọn model cho Gen và Sàng lọc'
            : keys.length
              ? `${enabled}/${keys.length} nguồn khoá API đang bật · tài khoản CLI ở thẻ riêng`
              : 'Chưa có nguồn khoá API · tài khoản CLI ở thẻ riêng'
          : 'Model, khoá API và tài khoản CLI'
      }
      aside={status ? <ConnectionStatusPill status={status} /> : undefined}
      bodyClass="conn-card__body"
    >
      {providers.isPending ? (
        <SkeletonLines rows={1} padding="0" />
      ) : providers.isError ? (
        <CardError error={providers.error} onRetry={() => void providers.refetch()} retrying={providers.isFetching} />
      ) : null}
      <div className="conn-card__actions">
        {isOwner ? (
          // v0.1.68 (F029): MỘT đường tới "thêm nguồn AI" — màn Thiết lập nguồn AI (dán khoá / đăng nhập, Kiểm tra, chọn
          // model). Tab Cài đặt › Bộ não AI chỉ để đọc nên thành liên kết phụ.
          <>
            <Link to={AI_SOURCE_PATH} className="gh-btn gh-btn--secondary btn-27" data-main-action>
              <Icon name="ph ph-plus-circle" size={13} />
              Thêm / kiểm tra nguồn AI
            </Link>
            <Link to="/system?tab=brain" className="conn-card__guide">
              Mở Bộ não AI
            </Link>
          </>
        ) : (
          <Link to="/system?tab=brain" className="gh-btn gh-btn--secondary btn-27" data-main-action>
            <Icon name="ph ph-brain" size={13} />
            Mở Bộ não AI
          </Link>
        )}
      </div>
    </Panel>
  );
}

function ChannelCards({ canManage }: { canManage: boolean }) {
  const channels = useChannels();
  if (channels.isPending) {
    return (
      <div className="conn-grid" aria-busy="true">
        {Array.from({ length: 3 }, (_, i) => (
          <div className="ch-card" key={i} aria-hidden>
            <SkeletonLines rows={2} padding="0" />
          </div>
        ))}
      </div>
    );
  }
  if (channels.isError) {
    return (
      <div className="gh-card" aria-label="Kênh">
        <CardError error={channels.error} onRetry={() => void channels.refetch()} retrying={channels.isFetching} />
      </div>
    );
  }
  const shown = visibleChannels(channels.data);
  if (shown.length === 0) {
    return (
      <div className="gh-card">
        <EmptyState icon="ph ph-plugs" title="Chưa có kênh nào" description="Bản đang chạy chưa có kênh nhắn tin nào." />
      </div>
    );
  }
  return (
    <div className="conn-grid" data-gen-target="system.channels.list">
      {orderChannels(shown).map((c) => (
        <ChannelCard key={c.type} channel={c} canManage={canManage} />
      ))}
    </div>
  );
}

function OwnerChannelSection() {
  const channels = useChannels();
  const types = ownerChannelTypes(channels.data);
  if (types.length === 0) return null;
  return (
    <section id="owner-channel" className="conn-section" aria-label="Kênh quản trị">
      <div className="conn-grid">
        {types.map((t) => (
          <OwnerChannelCard key={t} ch={t} />
        ))}
      </div>
    </section>
  );
}

function McpCard() {
  const servers = useMcpServers();
  const n = mcpEnabledCount(servers.data);
  return (
    <Panel
      title="MCP"
      label="MCP"
      kicker={servers.data ? (n ? `${n} máy chủ đang bật` : 'Chưa bật máy chủ MCP nào') : 'Máy chủ MCP cho agent gọi công cụ ngoài'}
      aside={servers.data ? <ConnectionStatusPill status={mcpStatus(servers.data)} /> : undefined}
      bodyClass="conn-card__body"
    >
      {servers.isPending ? (
        <SkeletonLines rows={1} padding="0" />
      ) : servers.isError ? (
        <CardError error={servers.error} onRetry={() => void servers.refetch()} retrying={servers.isFetching} />
      ) : null}
      <div className="conn-card__actions">
        <Link to="/mcp" className="gh-btn gh-btn--secondary btn-27" data-main-action>
          <Icon name="ph ph-plugs-connected" size={13} />
          Mở MCP Hub
        </Link>
      </div>
    </Panel>
  );
}
