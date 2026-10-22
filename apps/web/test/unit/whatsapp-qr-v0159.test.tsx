import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import type { Channel } from '@gen-harness/contracts';
import { ChannelCard } from '../../src/screens/system/ChannelCard';
import { channelErrorText, isDenseQr, qrHint } from '../../src/screens/system/systemModel';
import { queryClient } from '../../src/lib/queryClient';
import { qk2, useChannels } from '../../src/lib/dataQueries';
import { applyEvent } from '../../src/lib/realtime';

const channel = (over: Partial<Channel> = {}): Channel => ({
  type: 'whatsapp', name: 'WhatsApp', installed: true, id: 'c2', state: 'logged_out', account_label: null, started_at: null,
  groups_listening: 0, outbound_queued: 0, last_heartbeat_at: null, stats: null, qr: null, ...over,
});
const qr = (over = {}) => ({ session_id: 's1', image: 'data:image/png;base64,AA', expires_at: new Date(Date.now() + 60_000).toISOString(), scanned: false, ...over });

/** Đọc kênh từ cache như màn Kết nối, để sự kiện realtime vẽ lại thẻ. */
function Card({ large }: { large?: boolean }) {
  const channels = useChannels();
  const c = channels.data?.[0];
  return c ? <ChannelCard channel={c} canManage large={large} /> : null;
}

function renderCard(c: Channel, large?: boolean) {
  queryClient.setQueryData(qk2.channels, [c]);
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Card large={large} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  queryClient.clear();
  queryClient.setDefaultOptions({ queries: { retry: false, staleTime: Infinity } });
  vi.unstubAllGlobals();
});

describe('WhatsApp quét QR (v0.1.59): mã dày phải vẽ đủ lớn', () => {
  it('chỉ WhatsApp là mã dày; Zalo giữ khối nhỏ trên màn Kết nối', () => {
    expect(isDenseQr({ type: 'whatsapp' })).toBe(true);
    expect(isDenseQr({ type: 'zalo' })).toBe(false);
  });

  it('thẻ WhatsApp ở màn Kết nối (không large) vẫn dùng khối QR lớn, có hướng dẫn từng bước', () => {
    renderCard(channel({ state: 'pending_qr', qr: qr() }));
    const block = screen.getByTestId('qr-whatsapp');
    expect(block).toHaveClass('qr-block', 'qr-block--lg', 'qr-block--dense');
    expect(screen.getByRole('img', { name: /Mã QR đăng nhập WhatsApp/ })).toBeInTheDocument();
    expect(within(block).getByText(/Thiết bị đã liên kết › quét mã/)).toBeInTheDocument();
  });

  it('thẻ Zalo ở màn Kết nối giữ khối QR nhỏ như cũ', () => {
    renderCard(channel({ type: 'zalo', name: 'Zalo', state: 'pending_qr', qr: qr() }));
    const block = screen.getByTestId('qr-zalo');
    expect(block).not.toHaveClass('qr-block--dense');
    expect(block).not.toHaveClass('qr-block--lg');
  });

  it('gợi ý đúng thực tế: WhatsApp đổi mã 60 giây đầu rồi 20 giây; Zalo không hứa số giây cố định', () => {
    expect(qrHint({ type: 'whatsapp', outbound_queued: 0 })).toMatch(/60 giây.*20 giây/);
    expect(qrHint({ type: 'whatsapp', outbound_queued: 0 })).toMatch(/chưa liên kết được/);
    expect(qrHint({ type: 'zalo', outbound_queued: 0 })).not.toMatch(/60 giây/);
  });

  it('mã mới tới qua WebSocket thì thay ảnh ngay (mã làm mới sau khi máy chủ yêu cầu cũng vậy)', async () => {
    renderCard(channel({ state: 'pending_qr', qr: qr({ image: 'data:image/png;base64,AA' }) }));
    expect(screen.getByRole('img', { name: /Mã QR đăng nhập WhatsApp/ })).toHaveAttribute('src', 'data:image/png;base64,AA');
    act(() =>
      applyEvent(queryClient, {
        type: 'channel.qr',
        data: { type: 'whatsapp', session_id: 's1', image: 'data:image/png;base64,BB', expires_at: new Date(Date.now() + 20_000).toISOString() },
        at: new Date().toISOString(),
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole('img', { name: /Mã QR đăng nhập WhatsApp/ })).toHaveAttribute('src', 'data:image/png;base64,BB'),
    );
  });
});

describe('ghép WhatsApp không thành: câu thân thiện + Chi tiết kỹ thuật', () => {
  it('phiên hết hạn vì hết lượt mã QR → hướng dẫn quét lại, mã lỗi nằm ở Chi tiết kỹ thuật', () => {
    renderCard(channel({ state: 'expired', error: 'QR_EXPIRED' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Mã QR đã hết hạn mà chưa ghép được');
    expect(alert).toHaveTextContent('không liên kết được thiết bị');
    const detail = within(alert).getByText('Chi tiết kỹ thuật').closest('details')!;
    expect(within(detail).getByText('QR_EXPIRED')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Quét lại QR/ })).toBeInTheDocument();
  });

  it('Zalo hết hạn QR (lỗi hay gặp nhất của Zalo) không nhận lời khuyên riêng của WhatsApp', () => {
    renderCard(channel({ type: 'zalo', name: 'Zalo', state: 'expired', error: 'QR_EXPIRED' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('Mã QR đã hết hạn mà chưa quét');
    expect(alert).toHaveTextContent('Mở Zalo trên điện thoại');
    expect(alert).not.toHaveTextContent('không liên kết được thiết bị');
    expect(alert).not.toHaveTextContent('WhatsApp');
    expect(channelErrorText('QR_EXPIRED', 'WhatsApp', 'whatsapp')).toContain('không liên kết được thiết bị');
    expect(channelErrorText('QR_EXPIRED', 'Zalo', 'zalo')).not.toContain('không liên kết được thiết bị');
  });

  it('WhatsApp từ chối mở phiên (405) và không tới được máy chủ đều có câu riêng', () => {
    const { unmount } = renderCard(channel({ state: 'error', error: 'WA_NO_QR_405' }));
    expect(screen.getByRole('alert')).toHaveTextContent('từ chối mở phiên ghép thiết bị mới (mã 405)');
    expect(screen.getByText('WA_NO_QR_405')).toBeInTheDocument();
    unmount();
    queryClient.clear();
    renderCard(channel({ state: 'error', error: 'WA_NETWORK' }));
    expect(screen.getByRole('alert')).toHaveTextContent('không nối được tới máy chủ WhatsApp');
  });

  it('mã lỗi lạ không bao giờ lộ ra câu chính, chỉ ở Chi tiết kỹ thuật', () => {
    renderCard(channel({ state: 'error', error: 'SOMETHING_NEW' }));
    const alert = screen.getByRole('alert');
    expect(alert.querySelector('p')!.textContent).not.toContain('SOMETHING_NEW');
    expect(alert.querySelector('p')!.textContent).toContain('Quét lại QR');
    expect(within(alert).getByText('SOMETHING_NEW')).toBeInTheDocument();
  });

  it('không hiện lỗi khi đang chờ quét, khi đang chạy, hoặc khi chưa có mã lỗi', () => {
    const { unmount } = renderCard(channel({ state: 'pending_qr', qr: qr(), error: 'QR_EXPIRED' }));
    expect(screen.queryByText('Chi tiết kỹ thuật')).not.toBeInTheDocument();
    unmount();
    queryClient.clear();
    const b = renderCard(channel({ state: 'active', error: 'QR_EXPIRED' }));
    expect(screen.queryByText('Chi tiết kỹ thuật')).not.toBeInTheDocument();
    b.unmount();
    queryClient.clear();
    renderCard(channel({ state: 'expired', error: null }));
    expect(screen.queryByText('Chi tiết kỹ thuật')).not.toBeInTheDocument();
  });

  it('channel.status expired kèm error cập nhật ngay thẻ; chuyển sang chờ quét thì xoá lỗi cũ', async () => {
    renderCard(channel({ state: 'pending_qr', qr: qr() }));
    act(() =>
      applyEvent(queryClient, {
        type: 'channel.status',
        data: { type: 'whatsapp', state: 'error', account_label: null, scanned: false, error: 'PAIRING_REJECTED' },
        at: new Date().toISOString(),
      }),
    );
    expect(await screen.findByRole('alert')).toHaveTextContent('từ chối lần ghép này');
    expect(queryClient.getQueryData<Channel[]>(qk2.channels)![0].error).toBe('PAIRING_REJECTED');
    act(() =>
      applyEvent(queryClient, {
        type: 'channel.status',
        data: { type: 'whatsapp', state: 'pending_qr', account_label: null, scanned: false },
        at: new Date().toISOString(),
      }),
    );
    expect(queryClient.getQueryData<Channel[]>(qk2.channels)![0].error).toBeNull();
  });

  it('mọi mã lỗi bridge đã biết đều có câu tiếng Việt có dấu, không rỗng, không chứa mã thô', () => {
    const codes = [
      'QR_EXPIRED', 'PAIRING_REJECTED', 'WA_NETWORK', 'WA_NO_QR_405', 'WA_NO_QR_428', 'FORBIDDEN', 'LOGGED_OUT_REMOTELY',
      'CREDENTIAL_REJECTED', 'CREDENTIAL_INVALID', 'CREDENTIAL_UNREADABLE', 'CONNECTION_REPLACED', 'MULTIDEVICE_MISMATCH',
      'RECONNECT_FAILED', 'BRIDGE_KEY_MISSING', 'LOGIN_FAILED', 'NO_LOGIN_INFO', 'CHANNEL_UNAVAILABLE', 'KHÔNG_BIẾT',
    ];
    for (const code of codes) {
      const text = channelErrorText(code, 'WhatsApp');
      expect(text.length).toBeGreaterThan(30);
      expect(text).toMatch(/[ăâêôơưđáàảãạéèẻẽẹíìỉĩịóòỏõọúùủũụýỳỷỹỵ]/i);
      expect(text).not.toContain(code);
    }
  });
});
