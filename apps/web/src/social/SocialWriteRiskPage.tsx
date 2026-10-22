import { useEffect, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { SocialWriteGate } from '@gen-harness/contracts';
import { Button, Card, Chip, Dialog, EmptyState, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useMe } from '../lib/queries';
import { toast } from '../lib/toast';
import { useOrgTimezone } from '../lib/permissions';
import { CardError, SkeletonLines } from '../screens/common';
import { ScreenTitle } from '../screens/ScreenPage';
import { SOCIAL_KEY, fmtConsentTime, qkSocial } from './socialModel';
import { ErrorWithDetail } from './WriteProofDialog';

/**
 * `/social/ghi-facebook` — cảnh báo rủi ro khi cho Gen GỬI trả lời bình luận / tin nhắn lên Facebook (v0.1.47, F-85). CHỈ Owner.
 * Cổng ghi mở khi trình duyệt nền chạy trong sandbox (vùng cách ly của trình duyệt) HOẶC Sếp đã đọc cảnh báo và đồng ý.
 */
export function SocialWriteRiskPage() {
  const me = useMe();
  useEffect(() => {
    document.title = 'Gửi trả lời & tin nhắn Facebook · Gen-Harness';
  }, []);
  if (me.isPending) return <div className="screen"><SkeletonLines rows={4} /></div>;
  // Lỗi tải /auth/me (mạng, 5xx) KHÔNG có nghĩa là "không phải Owner" — báo lỗi tải kèm nút thử lại.
  if (me.isError && !me.data)
    return (
      <div className="screen">
        <CardError error={me.error} onRetry={() => void me.refetch()} retrying={me.isFetching} />
      </div>
    );
  if (me.data?.role?.code !== 'owner') {
    return (
      <div className="screen">
        <div className="gh-card">
          <EmptyState icon="ph ph-lock-simple" title="Chỉ Owner dùng được" description="Gửi trả lời và tin nhắn lên Facebook là việc nhân danh tài khoản cá nhân của Sếp — chỉ Owner quyết." />
        </div>
      </div>
    );
  }
  return <RiskBody />;
}

function RiskBody() {
  const tz = useOrgTimezone();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const gate = useQuery({ queryKey: qkSocial.writeGate, queryFn: ({ signal }) => api.social.writeGate(signal), refetchInterval: 30_000 });
  const platforms = useQuery({ queryKey: qkSocial.platforms, queryFn: ({ signal }) => api.social.platforms(signal) });
  const [revoking, setRevoking] = useState(false);
  const fb = platforms.data?.items.find((x) => x.key === 'facebook_personal') ?? platforms.data?.items[0];

  const done = (g: SocialWriteGate) => {
    queryClient.setQueryData(qkSocial.writeGate, g);
    void queryClient.invalidateQueries({ queryKey: SOCIAL_KEY });
  };
  const accept = useMutation({
    mutationFn: (version: string) => api.social.acceptWriteRisk(version),
    onSuccess: (g) => {
      done(g);
      toast('Đã ghi nhận đồng ý — Gen được phép gửi sau khi Sếp bấm Xác nhận và nhập mã PIN', 'ok');
    },
  });
  const revoke = useMutation({
    mutationFn: () => api.social.revokeWriteRisk(),
    onSuccess: (g) => {
      done(g);
      setRevoking(false);
      toast('Đã rút lại đồng ý — gửi lên Facebook bị khoá lại', 'warn');
    },
  });
  const back = () => (location.key !== 'default' ? navigate(-1) : navigate('/social'));

  const g = gate.data;
  return (
    <div className="screen social">
      <ScreenTitle
        title="Gửi trả lời & tin nhắn Facebook — cảnh báo rủi ro"
        description="Gen soạn, Sếp bấm Xác nhận và nhập mã PIN thì hệ thống mới gửi — nhân danh tài khoản Facebook của chính Sếp. Đọc kỹ rủi ro bên dưới."
        maxWidth={760}
      />
      <div className="social-main write-risk">
        {gate.isPending ? (
          <SkeletonLines rows={4} />
        ) : gate.isError || !g ? (
          <CardError error={gate.error} onRetry={() => void gate.refetch()} retrying={gate.isFetching} />
        ) : (
          <>
            <SandboxBox gate={g} />
            <Card title="Rủi ro" kicker="Sếp tự quyết có cho gửi hay không">
              {g.sandbox.enabled === true ? (
                <p className="muted-note" data-testid="write-risk-sandbox-note">
                  <Icon name="ph-fill ph-shield-check" size={14} color="var(--color-ok)" /> Trình duyệt nền đang chạy trong sandbox — rủi ro về việc thiếu sandbox bên dưới hiện không áp dụng.
                </p>
              ) : null}
              <ul className="risk-list" aria-label="Rủi ro khi gửi lên Facebook">
                {g.risk.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </Card>
            {fb ? (
              <Card title="Hệ thống sẽ làm / không làm">
                <div className="social-dos">
                  <div>
                    <div className="setup-section__title">Gen-Harness SẼ</div>
                    <ul className="risk-list">
                      {fb.will_do.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </div>
                  <div>
                    <div className="setup-section__title">Gen-Harness KHÔNG</div>
                    <ul className="risk-list">
                      {fb.wont_do.map((r) => (
                        <li key={r}>{r}</li>
                      ))}
                    </ul>
                  </div>
                </div>
              </Card>
            ) : null}
            <Card title="Quyết định của Sếp" data-testid="write-consent-card">
              {g.sandbox.enabled === true ? (
                <p className="muted-note" data-testid="write-sandbox-on">
                  <Icon name="ph-fill ph-shield-check" size={14} color="var(--color-ok)" /> Trình duyệt đang chạy trong sandbox — không cần đồng ý thêm.
                </p>
              ) : g.consent ? (
                <>
                  <p className="muted-note" data-testid="write-consent-info">
                    <Icon name="ph-fill ph-check-circle" size={14} color="var(--color-ok)" /> Sếp đã đồng ý lúc {fmtConsentTime(g.consent.accepted_at, tz)} (phiên bản cảnh báo {g.consent.version})
                  </p>
                  <Button variant="secondary" className="social-danger" icon="ph ph-arrow-u-up-left" onClick={() => setRevoking(true)}>
                    Rút lại đồng ý
                  </Button>
                </>
              ) : (
                <>
                  <p className="muted-note">Chưa đồng ý — gửi lên Facebook đang khoá. Bấm đồng ý nghĩa là Sếp hiểu các rủi ro ở trên. Cần mã PIN.</p>
                  <Button variant="primary" icon="ph ph-shield-check" loading={accept.isPending} onClick={() => accept.mutate(g.version)}>
                    Tôi hiểu rủi ro và đồng ý
                  </Button>
                </>
              )}
              {accept.isError ? <ErrorWithDetail error={accept.error} /> : null}
              <div className="write-risk__nav">
                <Button variant="ghost" size="sm" icon="ph ph-arrow-left" onClick={back}>
                  Quay lại
                </Button>
                <Link to="/social">Tài khoản mạng xã hội</Link>
              </div>
            </Card>
          </>
        )}
      </div>
      <Dialog
        open={revoking}
        onClose={() => setRevoking(false)}
        title="Rút lại đồng ý?"
        kicker="Gửi lên Facebook sẽ bị khoá lại"
        actions={
          <>
            <Button variant="secondary" onClick={() => setRevoking(false)}>
              Không
            </Button>
            <Button variant="primary" className="social-danger" loading={revoke.isPending} onClick={() => revoke.mutate()}>
              Rút lại đồng ý
            </Button>
          </>
        }
      >
        <p className="risk-box__text">Sau khi rút lại, mọi đề xuất trả lời / nhắn tin Facebook bị khoá cho tới khi Sếp đồng ý lại (hoặc trình duyệt bật được sandbox). Việc đã gửi không bị thu hồi.</p>
        {revoke.isError ? <ErrorWithDetail error={revoke.error} /> : null}
      </Dialog>
    </div>
  );
}

/** Ô trạng thái sandbox: đã bật / chưa bật (+ lý do) / trình duyệt nền chưa chạy. */
function SandboxBox({ gate }: { gate: SocialWriteGate }) {
  const { sandbox, worker_online: online } = gate;
  const state = !online ? 'offline' : sandbox.enabled === true ? 'on' : 'off';
  return (
    <Card title="Sandbox trình duyệt" kicker="Vùng cách ly của trình duyệt nền" data-testid="write-sandbox">
      {state === 'on' ? (
        <p className="muted-note">
          <Chip tone="ok" dot>Đã bật</Chip> Trình duyệt nền chạy trong vùng cách ly nên trang web không chạm được vào máy chủ.
        </p>
      ) : state === 'offline' ? (
        <p className="muted-note">
          <Chip tone="neutral" dot>Chưa rõ</Chip> Trình duyệt nền chưa chạy (dịch vụ browser) — chưa biết sandbox có bật được không.
        </p>
      ) : (
        <p className="muted-note">
          <Chip tone="warn" dot>Chưa bật</Chip> {sandbox.reason ?? 'Máy chủ không cho bật vùng cách ly của trình duyệt.'}
        </p>
      )}
    </Card>
  );
}
