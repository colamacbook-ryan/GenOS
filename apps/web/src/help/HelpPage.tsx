import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Button, Card, Icon } from '@gen-harness/ui';
import { api } from '../lib/api';
import { useGenStore } from '../gen/genStore';
import { can } from '../lib/permissions';
import { useMe } from '../lib/queries';
import { toast } from '../lib/toast';
import { CardError, SkeletonLines } from '../screens/common';
import { ScreenTitle } from '../screens/ScreenPage';
import { DiagnosticsCard } from './DiagnosticsCard';
import { CurriculumCard } from './CurriculumCard';
import { GENH_COMMANDS, GENH_VERSION_LABEL, SERVER_VERSION_LABEL, diagnosticText, pinLimitsFor, withVersions } from './helpModel';
import { roleLabel } from '../screens/system/systemModel';

const GEN_EXAMPLES = ['Hôm nay có gì gấp?', 'Khách nào hỏi giá hôm nay?', 'Sao lưu ở đâu?', 'Mời nhân viên mới thế nào?'];

/**
 * v0.1.49 (QD-16): Gen đọc gì từ Gen-hub — ai cũng đọc được trang này, nhưng chỉ Owner (Sếp) dùng được Gen-hub. v0.1.50 (QD-18):
 * không còn "chỉ đọc" tuyệt đối — Gen ghi Phiên, Việc vào Kho khi Sếp xác nhận + mã PIN, nên dòng đầu chỉ nói Gen đọc gì.
 */
const GENHUB_READS: readonly string[] = [
  'Gen đọc: Kho dữ liệu, lịch, mail (tìm và đọc), việc Google Tasks, tệp Drive.',
  'Gen KHÔNG gửi mail, KHÔNG tạo hay sửa lịch, việc Google Tasks, tệp Drive.',
  'Ghi duy nhất: Phiên, Việc vào Kho dữ liệu — chỉ khi Sếp bấm Xác nhận và nhập mã PIN (cần tick quyền kho_create, kho_update ở Gen-hub).',
  'Email, số điện thoại và khoá trong nội dung được che trước khi gửi cho AI.',
  'Gen-hub lỗi liên tục thì Gen tạm dừng gọi 1 phút và báo chuông nếu quá 15 phút.',
];

/** Trợ giúp / Giới thiệu (`/help`, v0.1.22 — Đợt B3). Ai đăng nhập cũng mở được. */
export function HelpPage() {
  const me = useMe();
  const about = useQuery({ queryKey: ['system', 'about'], queryFn: ({ signal }) => api.about(signal) });
  const setGenOpen = useGenStore((s) => s.setOpen);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    document.title = 'Trợ giúp · Gen-Harness';
  }, []);

  const canGuide = can(me.data, 'system.manage');
  const genOn = !!me.data?.features?.gen;
  // v0.1.28 (UX N9): trang theo vai trò — lệnh genh chạy trên máy chủ chỉ dành cho Owner (người cài), Gen chỉ hiện
  // khi vai trò có Gen; vai trò khác thấy cách nhờ Owner (đặt lại mật khẩu, mở quyền).
  const isOwner = me.data?.role?.code === 'owner';
  const pinLimits = pinLimitsFor(me.data);

  const copyDiagnostics = async () => {
    try {
      await navigator.clipboard.writeText(withVersions(diagnosticText(about.data, me.data), about.data));
      setCopied(true);
      toast('Đã chép thông tin báo lỗi — dán vào tin nhắn gửi người hỗ trợ.');
    } catch {
      toast('Không chép được — trình duyệt chặn bộ nhớ tạm.', 'warn');
    }
  };

  return (
    <div className="screen help">
      <ScreenTitle title="Trợ giúp" description={isOwner ? "Phiên bản đang chạy, cách hỏi Gen, các lệnh genh hay dùng và cách báo lỗi." : "Phiên bản đang chạy, cách nhờ Owner khi cần và cách báo lỗi."} maxWidth={640} />
      <div className="acct-grid">
        <Card title="Giới thiệu" kicker="Gen-Harness · Genesis Harness OS" data-gen-target="help.version">
          {about.isPending ? (
            <SkeletonLines rows={3} />
          ) : about.isError ? (
            <CardError error={about.error} onRetry={() => void about.refetch()} retrying={about.isFetching} />
          ) : (
            <div className="summary help-about">
              {/* v0.1.36 (F-46): bản máy chủ (ảnh Docker) đang chạy và genh có thể lệch nhau — hiện cả hai (dòng
                  "phiên bản" cũ = genh ?? máy chủ, lặp lại một trong hai nên đã bỏ). */}
              <span className="summary__k">{SERVER_VERSION_LABEL}</span>
              <span className="summary__v mono" data-testid="about-image-version">{about.data.image_version || about.data.version || 'bản phát triển'}</span>
              <span className="summary__k">{GENH_VERSION_LABEL}</span>
              <span className="summary__v mono" data-testid="about-genh-version">{about.data.genh_version ?? 'chưa cài'}</span>
              <span className="summary__k">tổ chức</span>
              <span className="summary__v">{about.data.org_name}</span>
              <span className="summary__k">múi giờ</span>
              <span className="summary__v">{about.data.timezone}</span>
              <span className="summary__k">vai trò</span>
              <span className="summary__v">{roleLabel(about.data.role.code, about.data.role.name)}</span>
            </div>
          )}
        </Card>

        {genOn ? (
          <Card title="Hỏi Gen" kicker="Trợ lý quản trị trong app" data-gen-target="help.ask_gen">
            <p className="help-text">
              Bấm nút{' '}
              <span className="help-inline-icon" aria-label="Gen">
                <Icon name="ph ph-sparkle" size={13} />
              </span>{' '}
              ở góc trên bên phải để mở khung Gen, gõ câu hỏi bằng tiếng Việt. Gen tra số liệu, mở đúng màn và khoanh sáng
              chỗ cần bấm — Gen không tự thay đổi dữ liệu.
            </p>
            <ul className="help-examples">
              {GEN_EXAMPLES.map((q) => (
                <li key={q}>“{q}”</li>
              ))}
            </ul>
            {me.data ? (
              <Button variant="secondary" icon="ph ph-sparkle" onClick={() => setGenOpen(me.data.id, true)}>
                Mở Gen
              </Button>
            ) : null}
          </Card>
        ) : null}

        {/* v0.1.54 (Gen hướng dẫn): Lộ trình học cùng Gen — 19 bài kèm trạng thái; chỉ Owner (thẻ tự ẩn với vai trò khác). */}
        <CurriculumCard />

        {/* v0.1.49 (QD-16): ai cũng thấy; ghi rõ chỉ Owner dùng được. v0.1.50 (QD-18): thêm dòng ghi Kho có Xác nhận + PIN. */}
        <Card title="Gen đọc được gì từ Gen-hub" kicker="Chỉ Sếp (Owner) dùng được · Gen đọc; ghi Kho khi Sếp xác nhận + mã PIN" data-testid="help-genhub-reads">
          <ul className="help-examples">
            {GENHUB_READS.map((t) => (
              <li key={t}>{t}</li>
            ))}
            <li>Chỉ Sếp (Owner) dùng được phần này; vai trò khác không đọc được lịch, mail, việc hay Drive qua Gen.</li>
          </ul>
          {isOwner ? (
            <Link className="gh-btn gh-btn--secondary help-link" to="/connections#genhub" data-testid="help-genhub-link">
              <Icon name="ph ph-plugs-connected" size={14} /> Mở Kết nối › Gen-hub
            </Link>
          ) : null}
        </Card>

        {/* v0.1.42 (F-61): thẻ cập nhật chỉ ở Cài đặt › Sao lưu & cập nhật — ở đây là liên kết tới đó. */}
        {canGuide ? (
          <Card title="Cập nhật phần mềm" kicker="Phiên bản đang dùng, bản mới, sao lưu">
            <Link className="gh-btn gh-btn--secondary help-link" to="/system?tab=storage" data-testid="help-update-link">
              <Icon name="ph ph-arrow-circle-up" size={14} /> Sao lưu &amp; cập nhật ở Cài đặt
            </Link>
          </Card>
        ) : null}

        {canGuide ? (
          <Card title="Hướng dẫn thiết lập" kicker="Kênh, nhóm, sàng lọc, agent, đội ngũ, sao lưu, Facebook, Gen-hub">
            <p className="help-text">Các việc thiết lập Sếp đã để sau — từng bước, làm lúc nào cũng được.</p>
            <Link className="gh-btn gh-btn--secondary help-link" to="/guide" data-gen-target="help.guide">
              <Icon name="ph ph-map-trifold" size={14} /> Mở Hướng dẫn thiết lập
            </Link>
          </Card>
        ) : null}

        {isOwner ? (
          <Card title="Lệnh genh hay dùng" kicker="Chạy trên máy chủ cài Gen-Harness" data-gen-target="help.genh" padded={false}>
            <ul className="help-cmds">
              {GENH_COMMANDS.map((c) => (
                <li key={c.cmd}>
                  <code className="mono">{c.cmd}</code>
                  <span>{c.what}</span>
                </li>
              ))}
            </ul>
          </Card>
        ) : (
          <Card title="Cần giúp về tài khoản" kicker="Owner là người quản lý tài khoản của mọi người">
            <ul className="help-examples">
              <li>Quên mật khẩu: nhờ Owner vào Đội ngũ › Người dùng › Đặt lại mật khẩu, rồi đăng nhập bằng mật khẩu tạm Owner gửi.</li>
              <li>Cần xem thêm màn hoặc làm thêm việc: nhờ Owner mở quyền cho vai trò của bạn.</li>
              <li>Đổi mật khẩu, đổi mã PIN của chính mình: menu tài khoản ở góc dưới bên trái › Tài khoản của tôi.</li>
              <li>
                Chưa có mã PIN (tài khoản mới được mời)? Vào Tài khoản của tôi › Đặt mã PIN, nhập mật khẩu đăng nhập và chọn 6 số —
                cần mã PIN để duyệt bản nháp và các thao tác nhạy cảm Owner đã giao cho bạn.
              </li>
            </ul>
          </Card>
        )}

        {/* v0.1.45 (F-60): giới hạn của mã PIN — ai cũng thấy (mỗi người tự giữ mật khẩu của mình); lời lẽ theo vai trò. */}
        <Card
          title={
            <h2 className="help-h2" style={{ font: 'inherit', margin: 0 }}>
              {pinLimits.title}
            </h2>
          }
          kicker={pinLimits.kicker}
          data-testid="help-pin-limits"
        >
          <ul className="help-examples">
            {pinLimits.points.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </Card>

        {/* v0.1.44 (F-4b): gói chẩn đoán (genh doctor) — chỉ Owner (cần PIN, đọc nhật ký máy chủ). */}
        {isOwner ? <DiagnosticsCard /> : null}

        <Card title="Báo lỗi" kicker="Chép thông tin chẩn đoán — không kèm mật khẩu hay khoá">
          <p className="help-text">
            Gặp lỗi? Bấm nút dưới để chép phiên bản, trang đang mở, trình duyệt và thời điểm, rồi dán vào tin nhắn gửi người hỗ trợ kèm mô tả ngắn (đã bấm gì, thấy gì).
          </p>
          <Button variant="primary" icon={copied ? 'ph ph-check' : 'ph ph-bug'} onClick={() => void copyDiagnostics()} data-gen-target="help.report">
            {copied ? 'Đã chép thông tin' : 'Báo lỗi — chép thông tin'}
          </Button>
        </Card>
      </div>
    </div>
  );
}
