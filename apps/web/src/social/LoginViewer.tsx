import { useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent, type WheelEvent } from 'react';
import type { SocialLiveInput, SocialLiveMessage } from '@gen-harness/contracts';
import { Button, Dialog, Icon } from '@gen-harness/ui';
import { keyToInput, liveUrl, mapPoint } from './socialModel';
import { openViewerSocket, type ViewerSocket } from './viewerSocket';


const STATE_TEXT: Record<string, string> = {
  connecting: 'Đang mở trình duyệt…',
  waiting: 'Sếp tự đăng nhập trong khung dưới (mật khẩu, mã 2FA).',
  logged_in: 'Đã đăng nhập — đang lưu phiên (mã hoá).',
  cancelled: 'Đã huỷ.',
  timeout: 'Hết 10 phút — bấm Đăng nhập để thử lại.',
  closed: 'Cửa sổ đã đóng.',
};

/**
 * Cửa sổ trình duyệt từ xa để Owner TỰ đăng nhập (docs/design/gen-browser-agent.md §3.2): khung hình JPEG từ
 * browser-worker (CDP screencast) vẽ lên canvas; chuột/phím của Sếp gửi ngược qua WS. Hệ thống không lưu, không ghi
 * log khung hình hay phím gõ; không có ô nhập mật khẩu nào của Gen-Harness.
 */
export function LoginViewer({
  open,
  ticket,
  label,
  onClose,
  onLoggedIn,
}: {
  open: boolean;
  ticket: string | null;
  label: string;
  onClose: () => void;
  onLoggedIn: () => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const sock = useRef<ViewerSocket | null>(null);
  const remote = useRef({ w: 1280, h: 800 });
  const [state, setState] = useState('connecting');
  const [message, setMessage] = useState(STATE_TEXT.connecting);
  const [frames, setFrames] = useState(0);

  const send = useCallback((ev: SocialLiveInput) => {
    const s = sock.current;
    if (s && s.readyState === 1) s.send(JSON.stringify(ev));
  }, []);

  useEffect(() => {
    if (!open || !ticket) return;
    setState('connecting');
    setMessage(STATE_TEXT.connecting);
    setFrames(0);
    const s = openViewerSocket(liveUrl(ticket));
    sock.current = s;
    s.onmessage = (ev) => {
      let msg: SocialLiveMessage;
      try {
        msg = JSON.parse(String(ev.data)) as SocialLiveMessage;
      } catch {
        return;
      }
      if (msg.type === 'frame') {
        const img = new Image();
        img.onload = () => {
          const c = canvas.current;
          if (!c) return;
          remote.current = { w: msg.w || img.width || 1280, h: msg.h || img.height || 800 };
          c.width = img.width;
          c.height = img.height;
          c.getContext('2d')?.drawImage(img, 0, 0);
        };
        img.src = `data:image/jpeg;base64,${msg.data}`;
        setFrames((n) => n + 1);
      } else if (msg.type === 'status') {
        setState(msg.state);
        setMessage(msg.message || STATE_TEXT[msg.state] || msg.state);
        if (msg.state === 'logged_in') onLoggedIn();
      }
    };
    s.onclose = (ev) => {
      if (ev.code === 4403) {
        setState('closed');
        setMessage('Phiên đăng nhập này không còn hiệu lực — bấm Đăng nhập lại.');
      }
    };
    return () => {
      sock.current = null;
      s.close();
    };
  }, [open, ticket, onLoggedIn]);

  const point = (e: MouseEvent<HTMLCanvasElement> | WheelEvent<HTMLCanvasElement>) =>
    mapPoint(e.clientX, e.clientY, e.currentTarget.getBoundingClientRect(), remote.current);

  const cancel = () => {
    send({ type: 'cancel' });
    onClose();
  };

  return (
    <Dialog
      open={open}
      onClose={cancel}
      width={900}
      title={`Đăng nhập ${label}`}
      kicker="Cửa sổ trình duyệt từ xa — chính Sếp đăng nhập"
      actions={
        <>
          <Button variant="secondary" onClick={cancel}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-check" onClick={() => send({ type: 'done' })} disabled={state === 'logged_in'}>
            Tôi đã đăng nhập xong
          </Button>
        </>
      }
    >
      <div className="social-viewer">
        <p className="social-viewer__status" role="status" aria-live="polite" data-state={state}>
          <Icon name={state === 'logged_in' ? 'ph ph-check-circle' : 'ph ph-monitor'} size={14} /> {message}
        </p>
        <div className="social-viewer__frame">
          <canvas
            ref={canvas}
            className="social-viewer__canvas"
            tabIndex={0}
            aria-label="Màn hình trình duyệt từ xa — bấm vào rồi gõ như bình thường"
            data-testid="social-viewer-canvas"
            data-frames={frames}
            onClick={(e) => {
              e.currentTarget.focus();
              send({ type: 'mouse', action: 'click', button: 'left', ...point(e) });
            }}
            onWheel={(e) => send({ type: 'wheel', dx: Math.round(e.deltaX), dy: Math.round(e.deltaY), ...point(e) })}
            onKeyDown={(e: KeyboardEvent<HTMLCanvasElement>) => {
              const ev = keyToInput(e);
              if (!ev) return;
              e.preventDefault();
              send(ev);
            }}
            onPaste={(e) => {
              const text = e.clipboardData.getData('text').slice(0, 256);
              if (text) send({ type: 'text', text });
              e.preventDefault();
            }}
          />
          {frames === 0 ? <div className="social-viewer__wait">Đang chờ hình từ trình duyệt…</div> : null}
        </div>
        <p className="muted-note">
          Phím gõ đi thẳng tới trình duyệt từ xa (qua kết nối mã hoá); hệ thống không lưu và không ghi lại mật khẩu, mã 2FA
          hay hình ảnh. Gặp CAPTCHA/xác minh thì Sếp tự làm trong khung này — hệ thống không tự giải. Tự đóng sau 10 phút.
        </p>
      </div>
    </Dialog>
  );
}
