/**
 * v0.1.28 — sửa theo rà soát UX (ux-audit): lỗi kỹ thuật → câu dễ hiểu, một nhãn trạng thái nguồn model, bước 4 phải có
 * model, bước 12 nói thật việc còn thiếu, ghi chú phát hành đọc được, mật khẩu tạm + lời nhắn đủ thông tin, hộp mật
 * khẩu tạm không đóng nhầm, phụ đề tiếng Anh mặc định tắt.
 */
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { Provider } from '@gen-harness/contracts';
import { Dialog } from '@gen-harness/ui';
import { friendlyError } from '../../src/lib/friendlyError';
import { useUiStore } from '../../src/lib/uiStore';
import { FriendlyErrorText } from '../../src/screens/common';
import { providerStatus } from '../../src/screens/api/apiModel';
import { inviteMessage } from '../../src/screens/system/usersModel';
import { providerHasModel, providerReady, testedModels } from '../../src/setup/phase2Model';
import { readableNotes } from '../../src/update/updateModel';

const P = (over: Partial<Provider>): Provider => ({
  id: 'p1', kind: 'openai_compat', name: 'Model nội bộ', endpoint: 'http://x/v1', failover_rank: 1, enabled: true, auth_state: 'ok',
  keys: [], models: [], last_test: null, ...over,
});

describe('N2 — lỗi kỹ thuật thành câu tiếng Việt, chi tiết gốc để thu gọn', () => {
  it('lỗi mạng, thiếu CLI, khoá sai, hạn mức', () => {
    expect(friendlyError('mạng: All connection attempts failed').message).toMatch(/Không gọi được địa chỉ này/);
    expect(friendlyError('mạng: All connection attempts failed').detail).toBe('mạng: All connection attempts failed');
    expect(friendlyError('[Errno 2] No such file or directory: agy').message).toMatch(/chưa cài công cụ đăng nhập Google/);
    expect(friendlyError('HTTP 401: API key not valid').message).toMatch(/Khoá API không đúng/);
    expect(friendlyError('429 RESOURCE_EXHAUSTED').message).toMatch(/hết hạn mức/);
    // Câu đã viết cho người dùng giữ nguyên, không có "chi tiết kỹ thuật".
    expect(friendlyError('Chưa có khoá API')).toEqual({ message: 'Chưa có khoá API', detail: null });
    // Lỗi lạ bằng tiếng Anh → câu chung + chi tiết.
    expect(friendlyError('Traceback: ValueError boom')).toEqual({ message: 'Không kiểm tra được nguồn này — thử lại sau.', detail: 'Traceback: ValueError boom' });
  });

  it('FriendlyErrorText: chi tiết kỹ thuật đóng sẵn, bấm mới hiện', async () => {
    render(<FriendlyErrorText raw="mạng: All connection attempts failed" />);
    expect(screen.getByText(/Không gọi được địa chỉ này/)).toBeInTheDocument();
    const details = screen.getByText('Chi tiết kỹ thuật').closest('details')!;
    expect(details).not.toHaveAttribute('open');
    await userEvent.setup().click(screen.getByText('Chi tiết kỹ thuật'));
    expect(details).toHaveAttribute('open');
  });
});

describe('N1 — một nhãn trạng thái nguồn model cho mọi màn', () => {
  it('lỗi / hết hạn / chưa kiểm tra / chưa chọn model / đã tắt', () => {
    expect(providerStatus(P({ auth_state: 'error' })).label).toBe('Lỗi kết nối');
    expect(providerStatus(P({ auth_state: 'expired' })).label).toBe('Hết hạn');
    expect(providerStatus(P({ auth_state: 'unconfigured' })).label).toBe('Chưa kiểm tra');
    expect(providerStatus(P({ auth_state: 'ok', models: [] })).label).toBe('Chưa chọn model');
    expect(providerStatus(P({ auth_state: 'ok', models: [{ id: 'm', model_name: 'x', daily_quota: null, used_today: 0 }] })).label).toBe('Hoạt động');
    expect(providerStatus(P({ enabled: false, auth_state: 'error' })).label).toBe('Đã tắt');
    expect(providerStatus(P({ kind: 'antigravity_cli', auth_state: 'unconfigured' })).label).toBe('Chưa đăng nhập');
  });
});

describe('C1 — bước 4 chỉ cho Tiếp tục khi có model; kết quả gọi thử còn sau khi tải lại', () => {
  it('last_test (lưu ở máy chủ) thay cho kết quả trong phiên; bỏ model embedding', () => {
    const p = P({ auth_state: 'ok', last_test: { ok: true, latency_ms: 20, models: ['text-embedding-3', 'gpt-4o-mini'], error: null } });
    expect(testedModels(p, {})).toEqual(['gpt-4o-mini']);
    expect(providerHasModel(p, {})).toBe(true);
    expect(providerHasModel(P({ auth_state: 'ok' }), {})).toBe(false);
    // Nguồn gọi thử lỗi không bao giờ "sẵn sàng" (không vào đầu chuỗi).
    expect(providerReady(P({ auth_state: 'error' }), {}, false)).toBe(false);
  });
});

describe('V11 — ghi chú phát hành đọc được', () => {
  it('bỏ phần tự sinh tiếng Anh của GitHub', () => {
    const md = "## What's Changed\n* Sửa lỗi sao lưu by @user in https://github.com/x/y/pull/31\n\n**Full Changelog**: https://github.com/x/y/compare/a...b";
    expect(readableNotes(md)).toBe('## Điểm mới\n* Sửa lỗi sao lưu');
  });
});

describe('V4 — mời người dùng', () => {
  it('lời nhắn gồm địa chỉ đăng nhập, email, mật khẩu tạm', () => {
    const msg = inviteMessage({ user: { display_name: 'Lan', email: 'lan@x.vn' } as never, temp_password: 'abcd-efgh-jkmn' }, 'https://gh.local/login');
    expect(msg).toContain('https://gh.local/login');
    expect(msg).toContain('lan@x.vn');
    expect(msg).toContain('abcd-efgh-jkmn');
  });

  it('hộp không cho đóng bằng Esc hoặc nút × khi dismissable=false', async () => {
    const onClose = vi.fn();
    render(
      <Dialog open onClose={onClose} dismissable={false} title="Mật khẩu tạm" actions={<button onClick={onClose}>Đã gửi, đóng</button>}>
        nội dung
      </Dialog>,
    );
    expect(screen.queryByRole('button', { name: 'Đóng' })).toBeNull();
    const user = userEvent.setup();
    await user.click(screen.getByText('nội dung'));
    await user.keyboard('{Escape}');
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Đã gửi, đóng' }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe('N5 → v0.1.42 (F-63): bỏ hẳn phụ đề tiếng Anh', () => {
  it('trình duyệt đã lưu bản cũ (version 0 hoặc 1, có showEnglish) — bỏ field, giữ các lựa chọn khác', async () => {
    const persist = useUiStore.persist;
    for (const version of [0, 1]) {
      localStorage.setItem('gh-ui', JSON.stringify({ state: { sidebarMode: 'rail', showEnglish: true, navOpen: { 'Hộp thư & Việc': true } }, version }));
      await persist.rehydrate();
      const s = useUiStore.getState() as unknown as Record<string, unknown>;
      expect('showEnglish' in s, `version ${version}`).toBe(false);
      expect(s.sidebarMode).toBe('rail');
      expect(s.navOpen).toEqual({ 'Hộp thư & Việc': true });
    }
    // v0.1.54: version 3 (bỏ followUpHiddenByUser của nút "Ẩn" cũ) — migrate vẫn bỏ showEnglish của bản 0/1.
    expect(persist.getOptions().version).toBe(3);
    expect(JSON.stringify(persist.getOptions().partialize!(useUiStore.getState()))).not.toContain('showEnglish');
    localStorage.removeItem('gh-ui');
    useUiStore.setState({ sidebarMode: 'full', navOpen: {} });
  });
});
