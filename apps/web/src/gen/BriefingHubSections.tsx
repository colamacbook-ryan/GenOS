import type { GenBriefingSection } from '@gen-harness/contracts';

/**
 * v0.1.49 (F-8, QD-16) — các mục Bản tin Gen lấy từ Gen-hub: Lịch hôm nay, Mail cần trả lời, Việc Google đang mở. Chỉ ĐỌC.
 * Chỉ vẽ mục `external === true` (mục nội bộ — việc tới hạn, khách nóng… — đã nằm trong các bước `say` của bản tin).
 *
 * - `ok`: danh sách dòng (đã che bên máy chủ). Chỉ render chuỗi: phần tử không phải chuỗi bị bỏ, không bao giờ in object.
 *   `more === true` (chạm trần 10 mục máy chủ đọc) ⇒ số đếm hiện "10+".
 * - `empty`: câu thân thiện theo khoá mục.
 * - `error` / `breaker`: câu thân thiện + "Chi tiết kỹ thuật" (chỉ khi `detail` là chuỗi).
 * - Mục chưa nối / thiếu quyền KHÔNG có trong `sections`: lời nhắc "tick thêm quyền" tới từ bước `say` + nút "Mở thẻ Gen-hub".
 */

const EMPTY_TEXT: Record<string, string> = {
  calendar_today: 'Hôm nay Sếp không có lịch.',
  mail_reply: 'Không có mail cần trả lời.',
  gtasks_open: 'Không có việc Google đang mở.',
};
const EMPTY_FALLBACK = 'Không có gì cần báo ở mục này.';

export const HUB_ERROR_TEXT = 'Chưa đọc được mục này lần này';
export const HUB_BREAKER_TEXT = 'Gen-hub tạm không trả lời — bản tin sau Gen thử lại.';

/** Mục nào của bản tin do Gen-hub cung cấp (khoá `external: true`) — an toàn với phần tử hỏng. */
function externalSections(sections: GenBriefingSection[] | undefined | null): GenBriefingSection[] {
  if (!Array.isArray(sections)) return [];
  return sections.filter((s) => !!s && typeof s === 'object' && s.external === true);
}

function TechDetail({ detail }: { detail: unknown }) {
  if (typeof detail !== 'string' || !detail) return null;
  return (
    <details className="tech-detail">
      <summary>Chi tiết kỹ thuật</summary>
      <code>{detail}</code>
    </details>
  );
}

function Section({ s }: { s: GenBriefingSection }) {
  const title = typeof s.title === 'string' && s.title ? s.title : typeof s.key === 'string' ? s.key : 'Gen-hub';
  const key = typeof s.key === 'string' ? s.key : '';
  const lines = Array.isArray(s.lines) ? s.lines.filter((l): l is string => typeof l === 'string' && l !== '') : [];
  const state = s.state === 'empty' || s.state === 'error' || s.state === 'breaker' ? s.state : 'ok';
  const count = typeof s.count === 'number' && Number.isFinite(s.count) ? s.count : lines.length;
  const countText = s.more === true ? `${count}+` : String(count);
  return (
    <section className="gen-hub-sec" data-state={state} data-key={key || undefined} aria-label={title}>
      <h4 className="gen-hub-sec__title">
        {title}
        {state === 'ok' ? <span className="gen-hub-sec__count"> ({countText})</span> : null}
      </h4>
      {state === 'ok' ? (
        lines.length > 0 ? (
          <ul className="gen-hub-sec__lines">
            {lines.map((l, i) => (
              <li key={i}>{l}</li>
            ))}
          </ul>
        ) : (
          <p className="gen-hub-sec__note">{EMPTY_TEXT[key] ?? EMPTY_FALLBACK}</p>
        )
      ) : null}
      {state === 'empty' ? <p className="gen-hub-sec__note">{EMPTY_TEXT[key] ?? EMPTY_FALLBACK}</p> : null}
      {state === 'error' ? (
        <div className="gen-hub-sec__note gen-hub-sec__note--warn">
          {HUB_ERROR_TEXT}
          <TechDetail detail={s.detail} />
        </div>
      ) : null}
      {state === 'breaker' ? (
        <div className="gen-hub-sec__note gen-hub-sec__note--warn">
          {HUB_BREAKER_TEXT}
          <TechDetail detail={s.detail} />
        </div>
      ) : null}
    </section>
  );
}

export function BriefingHubSections({ sections }: { sections?: GenBriefingSection[] | null }) {
  const list = externalSections(sections);
  if (list.length === 0) return null;
  return (
    <div className="gen-hub-secs" data-testid="briefing-hub-sections">
      {list.map((s, i) => (
        <Section key={`${typeof s.key === 'string' ? s.key : ''}-${i}`} s={s} />
      ))}
    </div>
  );
}
