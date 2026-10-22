/**
 * v0.1.59 (g5-thiet-lap) — `/owner/them`: Cài đặt, đúng 4 khối:
 *  1. Tài khoản — đổi mật khẩu / mã PIN (Tài khoản của tôi), giao diện sáng/tối, Đăng xuất;
 *  2. Nguồn AI — trạng thái (bước 4 của "Việc thiết lập tiếp") + "Đổi nguồn AI" → /setup?buoc=nguon-ai;
 *  3. Việc nên làm tiếp — MỘT danh sách từ `GET /gen/coach/today` (gồm việc boss.* và sự cố health.*), mỗi dòng "Chưa" kèm nút tới
 *     đích (showMe: làm sáng phần tử, hoặc đi tới link); hết việc thì một dòng "Đã xong";
 *  4. Cài đặt nâng cao — đúng 3 lối: API & Model, Bộ não AI, "Mở Console đầy đủ".
 * (v0.1.55 có khối "Kết nối" lấy từ boss-checks và link Hướng dẫn thiết lập — đã bỏ: việc kết nối nằm trong danh sách ở khối 3.)
 * Mọi chuỗi từ máy chủ qua `asText`; lỗi = câu thân thiện + "Chi tiết kỹ thuật"; không render object.
 */
import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Icon, Segmented } from '@gen-harness/ui';
import type { CoachTodo } from '@gen-harness/contracts';
import { executeUiAction } from '../gen/director';
import { coachUnavailable, requiredText, showMe, type CoachStep } from '../gen/coachModel';
import { useCoachToday } from '../gen/coachQueries';
import { FOLLOW_UP_KEY } from '../guide/guideContent';
import { api } from '../lib/api';
import { navigateTo } from '../lib/navigation';
import { useMe } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { useThemePref } from '../lib/theme';
import { toast } from '../lib/toast';
import { useUiStore, type ThemePref } from '../lib/uiStore';
import { Panel } from '../screens/common';
import { AI_SOURCE_PATH } from '../setup/steps';
import { ADVANCED_SETTINGS_PATH, asText, safeLink } from './ownerModel';
import { ListSkeleton, OwnerEmpty, OwnerError, RowLink } from './parts';
import { useIsOwnerRole } from './queries';

const THEME_OPTIONS: Array<{ value: ThemePref; label: string }> = [
  { value: 'light', label: 'Sáng' },
  { value: 'dark', label: 'Tối' },
  { value: 'system', label: 'Theo máy' },
];

/** Mức hiển thị trên Mặt tiền: Khẩn · Quan trọng · Thường (P0 · P1 · còn lại). */
function todoLevelText(level: unknown): string {
  const l = asText(level);
  return l === 'P0' ? 'Khẩn' : l === 'P1' ? 'Quan trọng' : 'Thường';
}

const NOTHING_TO_SHOW = 'Chưa có chỗ để chỉ cho việc này — Sếp mở trang liên quan trong Cài đặt nâng cao nhé.';

/** Chạy các bước chỉ đường tuần tự (cùng cách thẻ "Hôm nay của Sếp" làm): hành động UI của director, hoặc đi thẳng tới đường dẫn. */
async function runSteps(steps: CoachStep[]): Promise<void> {
  for (const step of steps) {
    if (step.type === 'go') navigateTo(step.to);
    else await executeUiAction(step);
  }
}

export function MoreScreen() {
  const owner = useIsOwnerRole();
  const me = useMe();
  const navigate = useNavigate();
  const [leaving, setLeaving] = useState(false);
  const userId = me.data?.id ?? null;
  const themePref = useThemePref(userId);
  const setTheme = useUiStore((s) => s.setTheme);
  const followUp = useQuery({ queryKey: FOLLOW_UP_KEY, queryFn: ({ signal }) => api.setup.followUp(signal), enabled: owner });
  const coach = useCoachToday(owner);
  useEffect(() => {
    document.title = 'Cài đặt · Gen-Harness';
  }, []);

  const logout = async () => {
    setLeaving(true);
    try {
      await api.auth.logout();
    } catch {
      // phiên đã mất thì cũng về trang đăng nhập
    }
    queryClient.clear();
    navigate('/login', { replace: true });
  };

  const step4 = Array.isArray(followUp.data) ? followUp.data.find((s) => s.n === 4) : undefined;
  const aiKnown = !!step4;
  const aiDone = !!step4?.done;
  const rawTodos = coach.data?.todos;
  const todos: CoachTodo[] = Array.isArray(rawTodos) ? rawTodos : [];
  const progress = coach.data?.progress;
  // Chưa biết vai trò thì các truy vấn Owner chưa chạy (disabled): vẫn là "đang tải", không phải "rỗng".
  const followUpLoading = owner ? followUp.isPending : me.isPending;
  const coachLoading = owner ? coach.isPending : me.isPending;

  const open = (t: CoachTodo) => {
    const steps = showMe(t);
    if (!steps.length) {
      toast(NOTHING_TO_SHOW, 'warn');
      return;
    }
    void runSteps(steps);
  };

  return (
    <div className="owner-screen" data-testid="owner-more">
      <Panel title={me.data?.display_name ? `Tài khoản · ${asText(me.data.display_name)}` : 'Tài khoản'} kicker="Mật khẩu, mã PIN và giao diện" flush>
        <div className="owner-list" data-testid="owner-more-account">
          <RowLink to="/account" title="Đổi mật khẩu" meta="Ở Tài khoản của tôi" lead={<Icon name="ph ph-key" size={16} />} testId="owner-more-password" />
          <RowLink to="/account" title="Đổi mã PIN" meta="PIN xác nhận mọi thao tác nhạy cảm" lead={<Icon name="ph ph-lock-simple" size={16} />} testId="owner-more-pin" />
          <RowLink to="/help" title="Trợ giúp" lead={<Icon name="ph ph-question" size={16} />} />
        </div>
        <div className="owner-advanced" data-testid="owner-more-theme">
          <Segmented<ThemePref> label="Giao diện" options={THEME_OPTIONS} value={themePref} onChange={(v) => setTheme(v, userId)} />
        </div>
        <div className="owner-advanced">
          <Button variant="secondary" icon="ph ph-sign-out" loading={leaving} onClick={() => void logout()}>
            Đăng xuất
          </Button>
        </div>
      </Panel>

      <Panel title="Nguồn AI" kicker="Gen dùng nguồn này để đọc và tóm tắt tin" flush>
        <div className="owner-list" data-testid="owner-more-ai">
          {followUpLoading ? (
            <ListSkeleton rows={1} label="Đang tải trạng thái nguồn AI" />
          ) : followUp.isError ? (
            <OwnerError error={followUp.error} onRetry={() => void followUp.refetch()} retrying={followUp.isFetching} />
          ) : (
            <div className="owner-row" data-testid="owner-more-ai-status" data-state={aiKnown ? (aiDone ? 'ok' : 'todo') : 'unknown'}>
              <span className="owner-dot" data-state={aiKnown ? (aiDone ? 'ok' : 'todo') : 'unknown'} aria-hidden />
              <span className="owner-row__body">
                <span className="owner-row__title">{aiKnown ? (aiDone ? 'Đã chạy' : 'Chưa nối nguồn AI') : 'Chưa rõ trạng thái'}</span>
                <span className="owner-row__meta">
                  {aiDone ? 'Gen đang tóm tắt tin cho Sếp.' : 'Gen sẽ chưa tóm tắt được bản tin cho tới khi Sếp nối nguồn AI.'}
                </span>
              </span>
            </div>
          )}
        </div>
        <div className="owner-advanced">
          <Link to={safeLink(AI_SOURCE_PATH)} className="gh-btn gh-btn--primary" data-testid="owner-more-ai-change">
            <Icon name="ph ph-brain" size={14} /> Đổi nguồn AI
          </Link>
        </div>
      </Panel>

      <Panel
        title="Việc nên làm tiếp"
        kicker={progress && typeof progress.required_total === 'number' && progress.required_total > 0 ? requiredText(progress.required_done, progress.required_total) : 'Gen nhắc những việc còn dở'}
        flush
      >
        <div data-testid="owner-more-todo">
          {coachLoading ? (
            <ListSkeleton rows={3} label="Đang tải việc nên làm tiếp" />
          ) : coach.isError && coachUnavailable(coach.error) ? (
            <OwnerEmpty icon="ph ph-list-checks" title="Chưa có danh sách việc" hint="Gen hướng dẫn chưa bật cho tài khoản này." />
          ) : coach.isError ? (
            <OwnerError error={coach.error} onRetry={() => void coach.refetch()} retrying={coach.isFetching} />
          ) : todos.length === 0 ? (
            <div className="owner-list">
              <div className="owner-row owner-row--ok" data-testid="owner-more-todo-row" data-done="">
                <span className="owner-dot" data-state="ok" aria-hidden />
                <span className="owner-row__body">
                  <span className="owner-row__title">Không còn việc nào cần làm tiếp</span>
                  <span className="owner-row__meta">Gen sẽ nhắc ở đây khi có việc mới.</span>
                </span>
                <span className="owner-row__right">Đã xong</span>
              </div>
            </div>
          ) : (
            <div className="owner-list">
              {todos.map((t) => (
                <div className="owner-row" key={asText(t.key)} data-testid="owner-more-todo-row" data-level={asText(t.level)}>
                  <span className="owner-dot" data-state="todo" aria-hidden />
                  <span className="owner-row__body">
                    <span className="owner-row__title">{asText(t.title)}</span>
                    {asText(t.why) ? <span className="owner-row__meta">{asText(t.why)}</span> : null}
                    <span className="owner-row__meta">
                      {todoLevelText(t.level)} · Chưa
                    </span>
                  </span>
                  <Button variant="secondary" size="sm" style={{ minHeight: 44 }} onClick={() => open(t)} aria-label={`Làm ngay: ${asText(t.title)}`}>
                    Làm ngay
                  </Button>
                </div>
              ))}
            </div>
          )}
        </div>
      </Panel>

      <Panel title="Cài đặt nâng cao" kicker="Ba lối vào toàn bộ Console" flush>
        <div className="owner-list" data-testid="owner-more-advanced">
          <RowLink to="/api" title="API & Model" meta="Khoá API, model, thứ tự dự phòng" lead={<Icon name="ph ph-plugs" size={16} />} testId="owner-adv-api" />
          <RowLink to="/system?tab=brain" title="Bộ não AI" meta="Nguồn AI, Gen hướng dẫn" lead={<Icon name="ph ph-brain" size={16} />} testId="owner-adv-brain" />
          <RowLink to={ADVANCED_SETTINGS_PATH} title="Mở Console đầy đủ" meta="Có nút “Về Mặt tiền” để quay lại" lead={<Icon name="ph ph-gear" size={16} />} testId="owner-advanced" />
        </div>
      </Panel>
    </div>
  );
}
