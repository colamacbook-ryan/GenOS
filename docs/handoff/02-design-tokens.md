# 02 · Design tokens

Nguồn: `design/_ds/nocturne-…/styles.css` (hệ Nocturne). Bản máy đọc: `design/tokens.json`. Ở sản phẩm, xuất các giá trị này thành CSS custom properties trong một tệp duy nhất và **mọi component chỉ tham chiếu biến**, không viết hex trực tiếp.

## Màu nền & bề mặt

| Token | Hex | Dùng cho |
|---|---|---|
| `--color-bg` | `#161826` | Nền trang, nền ô nhập, nền khung vẽ đồ thị, nền log |
| `--rail-bg` | `#131524` | Nền sidebar (sâu hơn nền trang 1 bậc) |
| `--color-surface` | `#232532` | Thẻ, bảng, panel |
| `--surface-raised` | `#1a1c2b` | Pill trạng thái trên header |
| `--row-hover` / `--color-neutral-900` | `#292b31` | Hover hàng, hàng đang chọn |
| `--nav-active` | `#232639` | Mục danh mục đang chọn |
| `--nav-hover` | `#1d2031` | Hover mục danh mục |
| `--color-divider` | `rgba(233,233,237,0.16)` | Viền thẻ, đường kẻ hàng |

## Chữ

| Token | Hex | Dùng cho | Tương phản trên surface |
|---|---|---|---|
| `--color-text` | `#e9e9ed` | Tiêu đề, giá trị chính | 13.1:1 |
| `--color-neutral-300` | `#cfd3e5` | Nội dung ô bảng | 10.2:1 |
| `--color-neutral-400` | `#b2b6ca` | Mô tả, phụ đề | 7.6:1 |
| `--color-neutral-500` | `#9397ab` | Nhãn mục, mono phụ, chữ mờ nhất được phép | 5.1:1 |
| `--color-neutral-700` | `#595d6c` | **Chỉ icon, caret, viền.** Cấm dùng làm màu chữ (2.3:1) | — |
| `--color-neutral-800` | `#3f424d` | Viền nút phụ, viền chip | — |

## Nhấn (mono accent)

| Token | Hex | Dùng cho |
|---|---|---|
| `--color-accent` | `#9184d9` | Viền nút chính, thanh chỉ mục đang chọn, công tắc bật, tab đang chọn. Tweakable — mặc định `#9184d9`, lựa chọn: `#7fb3c8`, `#c2a06b`, `#8fbf9f` |
| `--color-accent-300` | `#d2cefd` | Chữ trên nút chính, chữ tên agent, chip đang bật |
| `--color-accent-400` | `#b5abfc` | Ưu tiên P3, nhãn thông tin |
| `--color-accent-800` | `#423a6a` | Viền avatar, viền chip nhấn |
| `--color-accent-900` | `#2b2741` | Nền avatar, nền chip nhấn |

## Màu trạng thái (OKLCH, đồng lightness)

| Vai trò | Giá trị | Dùng cho |
|---|---|---|
| OK | `oklch(0.76 0.12 162)` | Khoẻ, cơ hội, tin cậy cao, đã gửi |
| WARN | `oklch(0.84 0.13 84)` | Cảnh báo, chờ duyệt, P2, trung bình |
| BAD | `oklch(0.76 0.15 24)` | Rủi ro, P1, lỗi, bị chặn |
| Tint nền | cùng giá trị + `/ 0.14–0.22` | Nền badge, ô heatmap |

Trạng thái luôn là **chấm 5–6px + nhãn chữ**, hoặc **chip viền 1px** cùng màu — không bao giờ tô đặc diện rộng.

## Chữ

Font duy nhất **Inter**, `-webkit-font-smoothing: antialiased`. Mono: `ui-monospace, SFMono-Regular, Menlo, monospace` cho ID, số đo, thời gian, tên model, tên sự kiện.

| Vai trò | Cỡ | Weight | Letter-spacing | Khác |
|---|---|---|---|---|
| Tiêu đề màn | 20px | 500 | -0.01em | |
| Tiêu đề header | 14px | 600 | | |
| Tiêu đề thẻ | 13px | 600 | | |
| KPI lớn | 24–27px | 600 | -0.02em | line-height 1 |
| Nội dung | 12–12.5px | 400 | | line-height 1.45–1.6 |
| Ô bảng | 11.5–12.5px | 400 | | |
| Nhãn phụ thẻ | 10px | 400 | 0.1em | UPPERCASE |
| Header cột bảng | 9.5px | 600 | 0.13em | UPPERCASE |
| Nhãn miền danh mục | 9.5px | 700 | 0.18em | UPPERCASE, màu theo miền |
| Mono | 10–11.5px | 400 | | |

Tối thiểu 9.5px chỉ cho nhãn UPPERCASE; chữ thường tối thiểu 10.5px.

## Khoảng cách, bo góc, bóng

- Khoảng cách chính: **16px** giữa các thẻ, **22px** padding vùng nội dung, **12–16px** padding trong thẻ, **12px** gap lưới KPI.
- Thang Nocturne (density 0.7×): `2.8 / 5.6 / 8.4 / 11.2 / 16.8 / 22.4px`.
- Bo góc: `--radius-sm 4px` (chip, ô nhỏ), `--radius-md 8px` (thẻ, nút, ô nhập), `999px` (pill, công tắc).
- Bóng: `--shadow-sm 0 0 0 1px #3f424d`; `--shadow-md 0 0 0 1px #595d6c, 0 6px 18px rgba(0,0,0,.55)`; `--shadow-lg` cho dialog. Trên nền tối, độ nổi là **viền + bóng môi trường**, không chồng bóng dày.

## Khung

- Sidebar: **244px** (chế độ đầy đủ) / **60px** (thanh icon, tweakable). Sticky, cao 100vh.
- Header: **58px**, sticky, viền dưới `--color-divider`.
- Vùng nội dung: fluid, padding 22px, không giới hạn max-width (Console chiếm trọn màn hình).
- Lưới: `repeat(auto-fit, minmax(…, 1fr))` cho KPI và thẻ; bảng rộng nằm trong vùng cuộn ngang riêng.

## Thành phần lặp lại

| Thành phần | Quy cách |
|---|---|
| Nút chính | `.btn.btn-primary` — viền 1px accent, nền trong suốt, chữ accent-300, cao 30px, padding 0 12px, 11.5px/500 |
| Nút phụ | `.btn.btn-secondary` — viền neutral-800, chữ neutral-300 |
| Nút ma | `.btn.btn-ghost` — không viền, cao 22–26px, 10–10.5px |
| Công tắc | 30×16px, pill; bật = nền accent, núm 10px màu bg ở `left:17px`; tắt = nền neutral-800, núm ở `left:3px`; khoá = opacity .55 |
| Chip trạng thái | 10–10.5px, padding 2px 8px, viền 1px cùng màu, bo 999px |
| Chip ưu tiên | 30–34×19–20px, bo 4px, viền 1px, 10px/600 |
| Chip loại | 9.5px/600, UPPERCASE, 0.09em, bo 4px |
| Thanh tiến độ | cao 4–5px, bo 2–3px, nền divider, phần đầy opacity .75–.85 |
| Tab | cao 34px, gạch dưới 2px accent khi chọn, 12.5px; nhãn đếm mono 10.5px neutral-500 |
| Segmented | viền neutral-800, padding 2px; mục chọn nền `#232639` |
| Thẻ | nền surface, viền divider, bo 8px, header 12px 16px có viền dưới |
| Đường kẻ trang trí | gradient mờ dần hai đầu: `linear-gradient(90deg, transparent, #2f3242 22%, #2f3242 78%, transparent)` |

## Trạng thái tương tác

- Hover hàng/thẻ: nền `#292b31` hoặc viền `#595d6c`.
- Hover mục danh mục: nền `#1d2031`, chữ `--color-text`.
- Focus bàn phím: `outline: 2px solid var(--color-accent); outline-offset: 2px`. Không bao giờ để viền xanh mặc định.
- Disabled: opacity .45.
- Nhấp nháy "LIVE": keyframe `opacity 1 → .35 → 1`, 2–2.2s, ease-in-out, lặp vô hạn. Tôn trọng `prefers-reduced-motion`.
- Link: màu `#b5abfc`, hover `#d2cefd`, không gạch chân.
