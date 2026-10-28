import type { ReactNode } from 'react';
import '../owner/owner.css';

/** v0.1.78 (G0): màn của Mặt tiền cũ (IntelScreen, FeedScreen…) cần vùng `.kit-scope` (số đo + token) khi nhúng vào khung Console. */
export function OwnerEmbed({ children }: { children: ReactNode }) {
  return <div className="kit-scope owner-embed">{children}</div>;
}
