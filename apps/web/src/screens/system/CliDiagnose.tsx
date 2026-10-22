import { useMutation } from '@tanstack/react-query';
import type { Provider } from '@gen-harness/contracts';
import { Button } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { errorText } from '../../lib/errorText';
import { fmtDMClock } from '../../lib/format';
import { useMe } from '../../lib/queries';
import { toast } from '../../lib/toast';
import { choiceText, diagnosisText } from '../api/apiModel';

/**
 * v0.1.32 — "Chẩn đoán" (chỉ Owner) trên nguồn CLI (Antigravity, Claude Code): chạy phiên bản, liệt kê model và MỘT lượt
 * gọi rất ngắn đúng model + mức suy nghĩ đang dùng; hiện stdout/stderr/mã thoát thô (máy chủ đã che token, email) và nút
 * "Chép" để Boss gửi khi còn lỗi. Mỗi lần bấm dùng một lượt gọi thật của gói (chung giới hạn với "Kiểm tra").
 */
export function CliDiagnose({ provider }: { provider: Pick<Provider, 'id' | 'name'> }) {
  const me = useMe();
  const run = useMutation({ mutationFn: () => api.providers.diagnose(provider.id) });
  if (me.data?.role?.code !== 'owner') return null;
  const d = run.data;
  const copy = () => {
    if (!d) return;
    void navigator.clipboard?.writeText(diagnosisText(d)).then(
      () => toast('Đã chép kết quả chẩn đoán — dán gửi cho người hỗ trợ.'),
      () => toast('Trình duyệt không cho chép — bôi đen rồi chép tay.', 'neutral'),
    );
  };
  return (
    <div className="cli-diag" data-testid={`cli-diagnose-${provider.id}`}>
      <div className="cli-diag__actions">
        <Button variant="ghost" size="sm" icon="ph ph-stethoscope" loading={run.isPending} onClick={() => run.mutate()}>
          Chẩn đoán
        </Button>
        {d ? (
          <>
            <Button variant="secondary" size="sm" icon="ph ph-copy" onClick={copy}>
              Chép
            </Button>
            <span className="muted-note">{`Lúc ${fmtDMClock(d.at)} · model ${choiceText(d.model, d.effort) || '—'}`}</span>
          </>
        ) : (
          <span className="muted-note">Chạy phiên bản, danh sách model và 1 lượt gọi rất ngắn; token và email được che.</span>
        )}
      </div>
      {run.isError ? <div className="prov-model__err" role="alert">{errorText(run.error)}</div> : null}
      {d
        ? d.steps.map((s) => (
            <div className="cli-diag__step" key={s.label}>
              <div className="cli-diag__head">
                <strong>{s.label}</strong>
                <span>{`mã thoát ${s.exit_code ?? '—'}`}</span>
                <span>{`${s.ms} ms`}</span>
                {s.note ? <span>{s.note}</span> : null}
              </div>
              <pre className="cli-diag__out">{`$ ${s.command}${s.stdout ? `\n${s.stdout.trimEnd()}` : ''}${s.stderr ? `\n[stderr]\n${s.stderr.trimEnd()}` : ''}`}</pre>
            </div>
          ))
        : null}
    </div>
  );
}
