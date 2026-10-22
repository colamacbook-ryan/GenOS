import { useState, type MouseEvent } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { ApiError, type DiagnosticsState } from '@gen-harness/contracts';
import { Button, Card, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { downloadBlob } from '../lib/download';
import { errorDetail, errorText } from '../lib/errorText';
import { fmtDMClock } from '../lib/format';
import { useOrgTimezone } from '../lib/permissions';
import { queryClient } from '../lib/queryClient';
import { toast } from '../lib/toast';
import { CardError, InlineError, SkeletonLines } from '../screens/common';
import {
  DIAGNOSTICS_KEY,
  DIAG_FILTERED_TEXT,
  DIAG_POLL_MS,
  DIAG_SLOW_TEXT,
  DIAG_STALE_TEXT,
  DIAG_UNSUPPORTED_TEXT,
  DIAG_WORKING_TEXT,
  diagApiErrorText,
  diagFailedText,
  diagFileName,
  diagPhase,
  diagSlow,
  diagTechDetail,
  downloadLabel,
} from './diagnosticsModel';

/**
 * v0.1.44 (F-4b) — Trợ giúp › "Gói chẩn đoán cho người hỗ trợ" (chỉ Owner). Console nhờ genh trên máy chủ chạy
 * `genh doctor` (PIN), thăm lại 3 giây tới khi xong, rồi tải tệp .zip đã lọc bí mật. genh cũ ⇒ hiện lệnh chạy tay.
 * Mã yêu cầu + thời điểm hiện kèm để người hỗ trợ (Claude) đối chiếu nhật ký.
 */
export function DiagnosticsCard() {
  const tz = useOrgTimezone();
  const q = useQuery({
    queryKey: DIAGNOSTICS_KEY,
    queryFn: ({ signal }) => api.diagnostics.getDiagnostics(signal),
    refetchInterval: (query) => (diagPhase(query.state.data) === 'working' ? DIAG_POLL_MS : false),
  });
  const download = useMutation({
    mutationFn: async (d: DiagnosticsState) => {
      const blob = await api.diagnostics.downloadDiagnostics();
      downloadBlob(blob, diagFileName(d));
    },
    onSuccess: () => toast('Đã tải gói chẩn đoán — gửi tệp .zip cho người hỗ trợ.', 'ok'),
    onError: (e) => {
      if (e instanceof ApiError && e.code === 'DIAG_NOT_READY') void q.refetch();
    },
  });
  const create = useMutation({
    mutationFn: () => api.diagnostics.requestDiagnostics(),
    onSuccess: (d) => {
      download.reset(); // lỗi tải của gói cũ không còn đúng với gói mới
      queryClient.setQueryData(DIAGNOSTICS_KEY, d);
    },
    onError: (e) => {
      if (e instanceof ApiError && (e.code === 'DIAG_BUSY' || e.code === 'DIAG_UNSUPPORTED')) void q.refetch();
    },
  });

  const d = q.data;
  const phase = diagPhase(d);
  const onDownload = (ev: MouseEvent<HTMLAnchorElement>) => {
    // Có JS: tải qua apiClient (hộp PIN khi 423, lỗi hiện thân thiện) rồi lưu bằng lib/download. `href` giữ đúng địa
    // chỉ cùng gốc để chuột giữa/"Lưu liên kết" vẫn dùng được.
    ev.preventDefault();
    if (d && !download.isPending) download.mutate(d);
  };

  return (
    <Card title="Gói chẩn đoán cho người hỗ trợ" kicker="Nhật ký và cấu hình máy chủ — đã lọc mật khẩu, khoá, token">
      <div data-testid="diagnostics-card" data-state={d ? phase : undefined} style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
        {q.isPending ? (
          <SkeletonLines rows={2} padding="0" />
        ) : q.isError || !d ? (
          <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
        ) : phase === 'unsupported' ? (
          <CommandBlock text={DIAG_UNSUPPORTED_TEXT} command={commandOf(d)} />
        ) : (
          <>
            <p className="help-text">
              Người hỗ trợ cần xem máy chủ? Bấm Tạo gói chẩn đoán, đợi xong rồi tải tệp .zip gửi cho họ — không cần mở dòng lệnh.
            </p>
            {phase === 'stale' ? (
              <div className="muted-note friendly-error" role="alert" data-testid="diagnostics-stale">
                <CommandBlock text={DIAG_STALE_TEXT} command={commandOf(d)} testId="diagnostics-stale-command" />
              </div>
            ) : null}
            {phase === 'working' ? (
              <>
                <p className="muted-note" role="status" data-testid="diagnostics-working">
                  <Icon name="ph ph-circle-notch" size={12} className="spin" /> {DIAG_WORKING_TEXT}
                </p>
                {diagSlow(d, phase) ? <CommandBlock text={DIAG_SLOW_TEXT} command={commandOf(d)} testId="diagnostics-slow-command" /> : null}
              </>
            ) : (
              <div className="boss-actions">
                <Button
                  variant={phase === 'done' ? 'secondary' : 'primary'}
                  icon="ph ph-package"
                  loading={create.isPending}
                  onClick={() => create.mutate()}
                >
                  Tạo gói chẩn đoán
                </Button>
              </div>
            )}
            {phase === 'done' ? (
              <div className="boss-form" data-testid="diagnostics-done">
                <div className="boss-actions">
                  <a
                    className="gh-btn gh-btn--primary"
                    href={api.diagnostics.diagnosticsDownloadUrl}
                    download={diagFileName(d)}
                    onClick={onDownload}
                    aria-busy={download.isPending || undefined}
                    data-testid="diagnostics-download"
                  >
                    <Icon name={download.isPending ? 'ph ph-circle-notch' : 'ph ph-download-simple'} size={14} className={download.isPending ? 'spin' : undefined} />
                    {downloadLabel(d)}
                  </a>
                </div>
                <p className="muted-note">
                  <Icon name="ph ph-shield-check" size={12} /> {DIAG_FILTERED_TEXT}
                </p>
              </div>
            ) : null}
            {phase === 'failed' ? (
              <div className="muted-note friendly-error" role="alert" data-testid="diagnostics-failed">
                {diagFailedText(d)}
                <details className="tech-detail">
                  <summary>Chi tiết kỹ thuật</summary>
                  <code>{diagTechDetail(d)}</code>
                </details>
              </div>
            ) : null}
            {d.request_id ? (
              <p className="muted-note" data-testid="diagnostics-meta">
                {[
                  `Mã yêu cầu ${d.request_id}`,
                  d.requested_at ? `yêu cầu lúc ${fmtDMClock(d.requested_at, tz)}` : '',
                  d.finished_at ? `xong lúc ${fmtDMClock(d.finished_at, tz)}` : '',
                ]
                  .filter(Boolean)
                  .join(' · ')}
              </p>
            ) : null}
          </>
        )}
        {create.isError ? <InlineError detail={errorDetail(create.error)}>{diagApiErrorText(create.error) ?? errorText(create.error)}</InlineError> : null}
        {download.isError ? <InlineError detail={errorDetail(download.error)}>{diagApiErrorText(download.error) ?? errorText(download.error)}</InlineError> : null}
      </div>
    </Card>
  );
}

const commandOf = (d: DiagnosticsState) => (typeof d.command === 'string' && d.command ? d.command : 'genh doctor');

function CommandBlock({ text, command, testId = 'diagnostics-command' }: { text: string; command: string; testId?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(true);
      toast('Đã chép lệnh.', 'ok');
    } catch {
      toast('Không chép được — bôi đen lệnh rồi chép tay.', 'bad');
    }
  };
  return (
    <div className="offsite-cmd" data-testid={testId}>
      <span>{text}</span>
      <div className="offsite-cmd__row">
        <code className="mono">{command}</code>
        <Button variant="ghost" className="btn-27" icon={copied ? 'ph ph-check' : 'ph ph-copy'} onClick={() => void copy()} aria-label="Chép lệnh">
          Chép
        </Button>
      </div>
    </div>
  );
}
