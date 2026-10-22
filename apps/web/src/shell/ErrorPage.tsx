import { Component, useEffect, useMemo, type ErrorInfo, type ReactNode } from 'react';
import { isRouteErrorResponse, useLocation, useNavigate, useRouteError } from 'react-router-dom';
import { ApiError } from '@gen-harness/contracts';
import { Icon } from '@gen-harness/ui';
import { reportClientError } from '../lib/clientErrors';
import { newErrorId } from '../lib/errorId';

/**
 * v0.1.23 (Đợt B5) — trang lỗi thân thiện + trang 404.
 *
 * - `ErrorBoundary`: bắt lỗi vẽ giao diện. Trong khung Console nó chỉ bọc vùng nội dung (thanh bên/header vẫn dùng
 *   được), đặt lại khi đổi trang. Ở gốc (main.tsx) nó là lưới an toàn cuối cùng.
 * - `RouteErrorPage`: `errorElement` của router (react-router tự bắt lỗi vẽ trong route, không để lọt ra ngoài).
 * - Mỗi lỗi có một mã (`ERR-…`) hiện cho người dùng và ghi kèm vào console — Báo lỗi (trang Trợ giúp) dán mã này.
 * - v0.1.44 (F-4b): lỗi gửi về máy chủ (`reportClientError`, khử trùng theo mã) để tra theo mã Sếp gửi; lỗi API có
 *   "Mã yêu cầu" (X-Request-ID) thì hiện ngay cạnh mã lỗi.
 */

function describe(error: unknown): string {
  if (error instanceof Error) return `${error.name}: ${error.message}`;
  if (isRouteErrorResponse(error)) return `${error.status} ${error.statusText}`;
  try {
    return String(error);
  } catch {
    return 'Lỗi không xác định';
  }
}

interface ErrorViewProps {
  errorId: string;
  error?: unknown;
  /** `page` = chiếm cả màn (ngoài khung Console); `inline` = trong vùng nội dung. */
  variant?: 'page' | 'inline';
  onRetry?: () => void;
}

export function ErrorView({ errorId, error, variant = 'page', onRetry }: ErrorViewProps) {
  const detail = error === undefined ? null : describe(error);
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className={variant === 'page' ? 'err-page' : 'err-inline'} role="alert">
      <div className="err-card">
        <div className="err-card__icon" aria-hidden>
          <Icon name="ph ph-warning-octagon" size={26} />
        </div>
        <h1 className="err-card__title">Đã có lỗi xảy ra</h1>
        <p className="err-card__desc">
          Màn hình này gặp sự cố ngoài dự kiến. Dữ liệu của Sếp không bị ảnh hưởng — thử tải lại trang; nếu vẫn lỗi, gửi
          mã lỗi bên dưới khi Báo lỗi ở trang Trợ giúp.
        </p>
        <div className="err-card__id">
          Mã lỗi: <code data-testid="error-id">{errorId}</code>
          {requestId ? (
            <>
              {' · '}Mã yêu cầu: <code data-testid="request-id">{requestId}</code>
            </>
          ) : null}
        </div>
        <div className="err-card__actions">
          {onRetry ? (
            <button type="button" className="gh-btn gh-btn--secondary" onClick={onRetry}>
              <Icon name="ph ph-arrow-counter-clockwise" size={14} /> Thử lại
            </button>
          ) : null}
          <button type="button" className="gh-btn gh-btn--primary" onClick={() => window.location.reload()}>
            <Icon name="ph ph-arrow-clockwise" size={14} /> Tải lại trang
          </button>
          <button type="button" className="gh-btn gh-btn--secondary" onClick={() => window.location.assign('/')}>
            <Icon name="ph ph-house" size={14} /> Về trang chủ
          </button>
        </div>
        {detail ? (
          <details className="err-card__detail">
            <summary>Chi tiết kỹ thuật</summary>
            <pre>{detail}</pre>
          </details>
        ) : null}
      </div>
    </div>
  );
}

interface BoundaryProps {
  children: ReactNode;
  variant?: 'page' | 'inline';
  /** Đổi giá trị (vd đường dẫn) → xoá lỗi, vẽ lại nội dung. */
  resetKey?: unknown;
}

interface BoundaryState {
  error: unknown;
  errorId: string | null;
  resetKey: unknown;
}

export class ErrorBoundary extends Component<BoundaryProps, BoundaryState> {
  state: BoundaryState = { error: null, errorId: null, resetKey: this.props.resetKey };

  static getDerivedStateFromError(error: unknown): Partial<BoundaryState> {
    return { error, errorId: newErrorId() };
  }

  static getDerivedStateFromProps(props: BoundaryProps, state: BoundaryState): Partial<BoundaryState> | null {
    if (props.resetKey !== state.resetKey) return { resetKey: props.resetKey, error: null, errorId: null };
    return null;
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(`[Gen-Harness] ${this.state.errorId ?? 'ERR'} — lỗi giao diện`, error, info.componentStack);
    if (this.state.errorId) reportClientError({ errorId: this.state.errorId, error, componentStack: info.componentStack });
  }

  private retry = () => this.setState({ error: null, errorId: null });

  render() {
    if (this.state.errorId) {
      return <ErrorView errorId={this.state.errorId} error={this.state.error} variant={this.props.variant} onRetry={this.retry} />;
    }
    return this.props.children;
  }
}

/** `errorElement` của router: 404 từ router → trang 404; mọi lỗi khác → trang lỗi có mã. */
export function RouteErrorPage() {
  const error = useRouteError();
  const errorId = useMemo(() => newErrorId(), []);
  useEffect(() => {
    console.error(`[Gen-Harness] ${errorId} — lỗi route`, error);
    // 404 của router không phải lỗi giao diện — không báo về máy chủ.
    if (!(isRouteErrorResponse(error) && error.status === 404)) reportClientError({ errorId, error });
  }, [error, errorId]);
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage variant="page" />;
  return <ErrorView errorId={errorId} error={error} variant="page" />;
}

/** Trang 404 — đường dẫn không thuộc Console. */
export function NotFoundPage({ variant = 'inline' }: { variant?: 'page' | 'inline' }) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = 'Không tìm thấy · Gen-Harness';
  }, []);
  return (
    <div className={variant === 'page' ? 'err-page' : 'err-inline'}>
      <div className="err-card" aria-labelledby="nf-title">
        <div className="err-card__code" aria-hidden>
          404
        </div>
        <h1 className="err-card__title" id="nf-title">
          Không tìm thấy trang
        </h1>
        <p className="err-card__desc">
          Đường dẫn <code>{pathname}</code> không thuộc Console — có thể đã đổi tên hoặc gõ nhầm. Chọn một màn ở thanh bên
          hoặc về trang chủ.
        </p>
        <div className="err-card__actions">
          <button type="button" className="gh-btn gh-btn--primary" onClick={() => navigate('/')}>
            <Icon name="ph ph-house" size={14} /> Về trang chủ
          </button>
          <button type="button" className="gh-btn gh-btn--secondary" onClick={() => navigate(-1)}>
            <Icon name="ph ph-arrow-left" size={14} /> Quay lại
          </button>
        </div>
      </div>
    </div>
  );
}
