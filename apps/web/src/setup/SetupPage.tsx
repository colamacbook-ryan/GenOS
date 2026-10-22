import { useCallback, useEffect, useRef, useState } from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ApiError, type SetupState } from '@gen-harness/contracts';
import { ErrorState, Icon, Skeleton } from '@gen-harness/ui';
import { FOLLOW_UP_KEY } from '../guide/guideContent';
import { api } from '../lib/api';
import { qk } from '../lib/queries';
import { queryClient } from '../lib/queryClient';
import { useRealtime } from '../lib/realtime';
import { toast } from '../lib/toast';
import { PigMark } from '../shell/Logo';
import { StepAccount } from './one/StepAccount';
import { StepAiSource } from './one/StepAiSource';
import { StepDone } from './one/StepDone';
import './one/setup-one.css';
import { AI_LATER_KEY, AI_SOURCE_PATH, SETUP_VIEWS, resolveSetupRoute, type SetupView } from './steps';
import { describeError } from './types';

const TOKEN_KEY = 'gh_setup_token';

function readStoredToken(): string {
  try {
    return window.sessionStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}
function storeToken(t: string) {
  try {
    window.sessionStorage.setItem(TOKEN_KEY, t);
  } catch {
    /* private mode: keep it in memory only */
  }
}
function readAiLater(): boolean {
  try {
    return window.sessionStorage.getItem(AI_LATER_KEY) === '1';
  } catch {
    return false;
  }
}
function storeAiLater() {
  try {
    window.sessionStorage.setItem(AI_LATER_KEY, '1');
  } catch {
    /* private mode: giữ trong bộ nhớ trang */
  }
}

/**
 * v0.1.59 (g5-thiet-lap) — Thiết lập MỘT màn, 3 bước: 1 Tài khoản · 2 Nguồn AI · 3 Xong. Máy chủ vẫn 12 bước (chỉ 1, 2, 3 và
 * 12 bắt buộc); bước 5–11 làm sau ở /guide/5…/guide/11. URL `?buoc=tai-khoan|nguon-ai|xong`; `?step=` cũ được ánh xạ
 * (`resolveSetupRoute`). Đã xong thiết lập: chỉ `?buoc=nguon-ai` mở được (đổi nguồn AI, cần phiên Owner), còn lại về /owner.
 */
export function SetupPage() {
  const [params, setParams] = useSearchParams();
  const urlToken = params.get('token') ?? '';
  const [token, setTokenState] = useState(() => urlToken || readStoredToken());
  const [aiLater, setAiLater] = useState(readAiLater);
  // Đích rời trang (sau PUT 12 / lưu nguồn AI khi đã xong). Ghi vào ref NGAY (đồng bộ) trước khi cập nhật cache trạng thái: cache vừa
  // báo "đã xong" là lần vẽ kế tiếp sẽ chuyển về /owner — đích đã chọn (vd /guide/5) phải thắng, không phụ thuộc thứ tự cập nhật.
  const exitRef = useRef<string | null>(null);
  const [, bump] = useState(0);
  const setExitTo = (to: string) => {
    exitRef.current = to;
    bump((n) => n + 1);
  };

  const state = useQuery({ queryKey: qk.setupState, queryFn: ({ signal }) => api.setup.state(signal) });
  const route = state.data ? resolveSetupRoute({ buoc: params.get('buoc'), step: params.get('step'), state: state.data, aiLater }) : null;
  const view: SetupView | null = route?.kind === 'view' ? route.view : null;
  const reopenAi = !!state.data?.finished && view === 'nguon-ai';
  // Live CLI login / tiến độ lọc tin lần đầu khi Owner đã đăng nhập (sau bước 3); mở lại Nguồn AI sau Hoàn tất cũng cần.
  useRealtime(!!state.data && state.data.current_step >= 4 && (!state.data.finished || reopenAi));
  // Mở lại sau Hoàn tất: máy chủ chỉ cho Owner lưu bước 4 — kiểm phiên trước khi vẽ.
  const me = useQuery({ queryKey: qk.me, queryFn: ({ signal }) => api.auth.me(signal), enabled: reopenAi, retry: false });

  useEffect(() => {
    document.title = 'Thiết lập Owner · Gen-Harness';
  }, []);
  useEffect(() => {
    if (urlToken) {
      storeToken(urlToken);
      setTokenState(urlToken);
    }
  }, [urlToken]);
  // URL chuẩn: `?buoc=<bước>` (bỏ `?step=` cũ; giữ `?token=`). replace — Quay lại của trình duyệt không vòng lại.
  useEffect(() => {
    if (!view) return;
    if (params.get('buoc') === view && !params.has('step')) return;
    const next = new URLSearchParams(params);
    next.delete('step');
    next.set('buoc', view);
    setParams(next, { replace: true });
  }, [view, params, setParams]);

  const setToken = useCallback((t: string) => {
    storeToken(t);
    setTokenState(t);
  }, []);

  if (exitRef.current) return <Navigate to={exitRef.current} replace />;
  if (state.isPending) return <SetupSkeleton />;
  if (state.isError) {
    return (
      <div className="setup-one">
        <div className="setup-one__card">
          <div className="gh-card setup-one__panel">
            <ErrorState
              title="Không tải được trạng thái thiết lập"
              message={describeError(state.error)}
              onRetry={() => void state.refetch()}
              retrying={state.isFetching}
            />
          </div>
        </div>
      </div>
    );
  }
  if (!route || route.kind === 'owner') return <Navigate to="/owner" replace />;
  if (route.kind === 'guide') return <Navigate to={`/guide/${route.n}`} replace />;
  const data = state.data;

  if (reopenAi) {
    if (me.isPending) return <SetupSkeleton />;
    if (me.isError) {
      if (me.error instanceof ApiError && me.error.status === 401) return <Navigate to={`/login?next=${encodeURIComponent(AI_SOURCE_PATH)}`} replace />;
      return (
        <div className="setup-one">
          <div className="setup-one__card">
            <div className="gh-card setup-one__panel">
              <ErrorState title="Không kiểm tra được phiên đăng nhập" message={describeError(me.error)} onRetry={() => void me.refetch()} retrying={me.isFetching} />
            </div>
          </div>
        </div>
      );
    }
    // Chỉ Owner đổi được nguồn AI — vai trò khác về trang chủ của vai trò.
    if (me.data.role?.code !== 'owner') return <Navigate to="/" replace />;
  }

  const onState = (s: SetupState) => queryClient.setQueryData(qk.setupState, s);
  // Đi tới một bước bằng URL (replace). Bước tiến lên phải đổi `?buoc=` rõ ràng: từ Xong vẫn được quay lại Nguồn AI nên
  // `?buoc=nguon-ai` còn hiệu lực dù bước 4 đã xong.
  const goView = (view: SetupView) => {
    const next = new URLSearchParams(params);
    next.delete('step');
    next.set('buoc', view);
    setParams(next, { replace: true });
  };
  const onAiSaved = (s: SetupState) => {
    void queryClient.invalidateQueries({ queryKey: FOLLOW_UP_KEY });
    if (data.finished) {
      toast('Đã lưu nguồn AI — Gen dùng được ngay.', 'ok');
      setExitTo('/owner');
    } else {
      goView('xong');
    }
    onState(s);
  };
  const onLater = () => {
    storeAiLater();
    setAiLater(true);
    goView('xong');
  };
  const onFinished = (s: SetupState, to: string) => {
    setExitTo(to);
    onState(s);
  };

  return (
    <div className="setup-one" data-view={view}>
      <main className="setup-one__card">
        <header className="setup-one__head">
          <SetupBrand />
          <ol className="setup-one__steps" aria-label="Các bước thiết lập">
            {SETUP_VIEWS.map((v) => {
              const at = SETUP_VIEWS.findIndex((x) => x.key === view);
              const mine = SETUP_VIEWS.findIndex((x) => x.key === v.key);
              const st = v.key === view ? 'current' : mine < at ? 'done' : 'todo';
              return (
                <li key={v.key} className="setup-one__step" data-state={st} aria-current={st === 'current' ? 'step' : undefined} data-testid={`setup-step-${v.key}`}>
                  <span className="setup-one__num" aria-hidden>
                    {st === 'done' ? <Icon name="ph ph-check" size={12} /> : v.n}
                  </span>
                  <span className="setup-one__label">
                    <span className="visually-hidden">{`Bước ${v.n}: `}</span>
                    {v.label}
                  </span>
                </li>
              );
            })}
          </ol>
        </header>
        <div className="gh-card" key={view}>
          {view === 'tai-khoan' ? (
            <StepAccount state={data} token={token} setToken={setToken} onState={onState} />
          ) : view === 'nguon-ai' ? (
            <StepAiSource finished={data.finished} onSaved={onAiSaved} onLater={data.finished ? undefined : onLater} />
          ) : (
            <StepDone state={data} aiLater={aiLater} onFinished={onFinished} onBack={() => goView('nguon-ai')} />
          )}
        </div>
      </main>
    </div>
  );
}

/** Logo của Thiết lập: cùng kiểu với Mặt tiền (ô hình heo + "Gen-Harness"), không viết hoa toàn bộ như logo Console. */
function SetupBrand() {
  return (
    <div className="setup-one__brand" data-testid="setup-brand">
      <span className="setup-one__brand-tile" aria-hidden>
        <PigMark size={20} />
      </span>
      <span className="setup-one__brand-name">Gen-Harness</span>
    </div>
  );
}

function SetupSkeleton() {
  return (
    <div className="setup-one" aria-busy="true">
      <div className="setup-one__card">
        <header className="setup-one__head">
          <SetupBrand />
        </header>
        <div className="gh-card setup-one__panel">
          <Skeleton width={200} height={20} />
          <Skeleton width="70%" height={12} style={{ marginTop: 9 }} />
          <Skeleton width="100%" height={160} style={{ marginTop: 16 }} />
        </div>
      </div>
    </div>
  );
}
