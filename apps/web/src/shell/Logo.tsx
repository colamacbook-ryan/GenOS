/**
 * Logo: đầu heo vui vẻ (Owner chọn 24/09/2026). Thiết kế ghi `ph-radar`, icon
 * này không có trong Phosphor 2.1 nên ô logo trong thiết kế để trống. Nét vẽ
 * theo phong cách Phosphor regular (lưới 256, nét 16, bo tròn), ăn theo
 * `currentColor` của ô logo. `public/favicon.svg` dùng cùng hình.
 */
export function PigMark({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 256" fill="none" stroke="currentColor" strokeWidth={16}
      strokeLinecap="round" strokeLinejoin="round" aria-hidden focusable="false">
      <path d="M60 104 Q30 70 34 30 Q76 34 106 62" />
      <path d="M196 104 Q226 70 222 30 Q180 34 150 62" />
      <ellipse cx="128" cy="146" rx="96" ry="82" />
      <path d="M84 124 q14 -14 28 0" />
      <path d="M144 124 q14 -14 28 0" />
      <ellipse cx="128" cy="164" rx="34" ry="24" />
      <g fill="currentColor" stroke="none">
        <circle cx="115" cy="164" r="6" />
        <circle cx="141" cy="164" r="6" />
        <circle cx="68" cy="158" r="12" opacity="0.35" />
        <circle cx="188" cy="158" r="12" opacity="0.35" />
      </g>
      <path d="M106 202 q22 14 44 0" />
    </svg>
  );
}

/** "0.1.42" / "v0.1.42" → "v0.1.42"; rỗng → null. */
function versionLabel(version: string | null | undefined): string | null {
  const v = (version ?? '').trim();
  if (!v) return null;
  return /^\d/.test(v) ? `v${v}` : v;
}

/**
 * v0.1.42 (F-67): dòng phụ là phiên bản THẬT đang chạy (GET /system/about, cùng khoá truy vấn với Trợ giúp) —
 * "Gen-Harness · v0.1.42"; chưa tải được thì bỏ dòng (không còn chữ cố định "v2.2" của bản thiết kế).
 */
export function Logo({ wide, version }: { wide: boolean; version?: string | null }) {
  const label = versionLabel(version);
  return (
    <div className="sb-logo">
      <div className="sb-logo__tile" aria-hidden>
        <PigMark />
      </div>
      {wide ? (
        <div className="sb-logo__text">
          <div className="sb-logo__name">GEN&#8209;HARNESS</div>
          {label ? <div className="sb-logo__sub" data-testid="logo-version">Gen-Harness · {label}</div> : null}
        </div>
      ) : (
        <span className="visually-hidden">Gen-Harness</span>
      )}
    </div>
  );
}
