import { useEffect, useLayoutEffect, useState, type CSSProperties } from 'react';
import { createPortal } from 'react-dom';
import { Button, Icon } from '@gen-harness/ui';
import { closeSpotlight, revealTarget, targetSelector, tourBack, tourNext } from './director';
import { useGenStore } from './genStore';

interface Box {
  top: number;
  left: number;
  width: number;
  height: number;
}

const PAD = 6;
const BUBBLE_W = 300;

function reducedMotion(): boolean {
  try {
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  } catch {
    return false;
  }
}

/**
 * Lớp phủ làm sáng (docs/design/gen-v1.md §3.1): nền mờ + viền phát sáng quanh phần tử `data-gen-target`, bong bóng
 * lời nhắn (tour: Bước i/n, Quay lại / Tiếp). Không chặn chuột — Sếp bấm thẳng vào phần tử được chỉ (bấm vào nó thì
 * tour sang bước kế). Esc để thoát; theo `prefers-reduced-motion`.
 */
export function Spotlight() {
  const spot = useGenStore((s) => s.spotlight);
  const [box, setBox] = useState<Box | null>(null);

  useLayoutEffect(() => {
    if (!spot || spot.missing) {
      setBox(null);
      return;
    }
    let el: Element | null = null;
    let revealed: Element | null = null;
    let raf = 0;
    const measure = () => {
      el = document.querySelector(targetSelector(spot.target));
      if (!el) {
        setBox(null);
        return;
      }
      // Mở <details> đang gập chứa đích MỘT lần cho mỗi phần tử (Sếp gập lại sau đó thì không ép mở nữa).
      if (revealed !== el) {
        revealTarget(el);
        revealed = el;
      }
      const r = el.getBoundingClientRect();
      setBox((b) =>
        b && b.top === r.top && b.left === r.left && b.width === r.width && b.height === r.height
          ? b
          : { top: r.top, left: r.left, width: r.width, height: r.height },
      );
    };
    measure();
    if (el) (el as HTMLElement).scrollIntoView?.({ block: 'center', inline: 'nearest', behavior: reducedMotion() ? 'auto' : 'smooth' });
    const onFrame = () => {
      measure();
      raf = requestAnimationFrame(onFrame);
    };
    raf = requestAnimationFrame(onFrame);
    const onClick = (e: MouseEvent) => {
      const t = document.querySelector(targetSelector(spot.target));
      if (!t || !(e.target instanceof Node) || !t.contains(e.target)) return;
      if (spot.tour) tourNext();
      else if (spot.waitFor !== 'none') closeSpotlight();
    };
    document.addEventListener('click', onClick, true);
    return () => {
      cancelAnimationFrame(raf);
      document.removeEventListener('click', onClick, true);
    };
  }, [spot]);

  useEffect(() => {
    if (!spot) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeSpotlight();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [spot]);

  if (!spot) return null;
  const tour = spot.tour;
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800;
  const ring: CSSProperties | undefined = box
    ? { top: box.top - PAD, left: box.left - PAD, width: box.width + PAD * 2, height: box.height + PAD * 2 }
    : undefined;
  const below = box ? box.top + box.height + PAD + 12 : vh / 2 - 60;
  const placeAbove = box ? below + 150 > vh && box.top > 170 : false;
  const bubble: CSSProperties = {
    width: Math.min(BUBBLE_W, vw - 32),
    left: Math.max(16, Math.min((box?.left ?? vw / 2 - BUBBLE_W / 2), vw - Math.min(BUBBLE_W, vw - 32) - 16)),
    ...(placeAbove && box ? { bottom: vh - box.top + PAD + 12 } : { top: Math.min(below, vh - 170) }),
  };

  return createPortal(
    <div className="gen-spot" data-reduced={reducedMotion() || undefined} data-testid="gen-spotlight">
      {ring ? <div className="gen-spot__ring" style={ring} aria-hidden /> : <div className="gen-spot__dim" aria-hidden />}
      <div className="gen-spot__bubble" style={bubble} role="dialog" aria-live="polite" aria-label="Gen đang chỉ">
        <div className="gen-spot__head">
          <span className="gen-spot__who">
            <Icon name="ph ph-sparkle" size={12} /> Gen
          </span>
          {tour ? (
            <span className="gen-spot__step mono">
              Bước {tour.index + 1}/{tour.steps.length}
            </span>
          ) : null}
          <button type="button" className="gen-spot__close" onClick={closeSpotlight} aria-label="Tắt làm sáng">
            <Icon name="ph ph-x" size={12} />
          </button>
        </div>
        <p className="gen-spot__msg">{spot.missing ? `Em không thấy phần này trên màn hình. ${spot.message}` : spot.message}</p>
        {tour ? (
          <div className="gen-spot__actions">
            <Button size="sm" variant="ghost" onClick={tourBack} disabled={tour.index === 0}>
              Quay lại
            </Button>
            <Button size="sm" variant="primary" onClick={tourNext} iconRight={tour.index + 1 < tour.steps.length ? 'ph ph-arrow-right' : undefined}>
              {tour.index + 1 < tour.steps.length ? 'Tiếp' : 'Xong'}
            </Button>
          </div>
        ) : null}
      </div>
    </div>,
    document.body,
  );
}
