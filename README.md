# Gen-Harness

Gen-Harness là một hệ thống tự trị có kiểm soát cho một tổ chức bán hàng/dịch vụ nhỏ: lắng nghe các kênh nhắn
tin (Zalo, WhatsApp, Facebook…), sàng lọc thành dữ liệu sạch có nguồn gốc rõ ràng, và để agent AI soạn — nhưng không tự
ý gửi — phản hồi, cập nhật cơ hội bán hàng, cảnh báo sớm. Mọi việc "ra ngoài", vượt ngưỡng tiền, hoặc liên quan
nhân sự luôn dừng lại chờ người duyệt ở Bàn làm việc. **Gen** là trợ lý trong Console: trả lời, dẫn đường từng bước, nhớ sở thích
của Sếp, đọc Kho dữ liệu/lịch/mail qua **Gen-hub** — và chỉ *đề xuất*; Sếp bấm Xác nhận (kèm mã PIN khi nhạy cảm) thì mới làm.

Đọc theo vai: **[Dành cho Boss](#dành-cho-boss)** (cài, cập nhật, việc cần làm, nhờ giúp) ·
**[Dành cho dev](#dành-cho-dev)** (dựng từ mã nguồn, kiểm thử, phát hành). Tài liệu khác: [Nhật ký thay đổi](CHANGELOG.md) ·
[Lộ trình + mục Nợ](docs/ROADMAP.md) · [Sổ tay vận hành](docs/runbook.md) · [Kiến trúc](docs/ARCHITECTURE.md) · [Kế hoạch giai đoạn](docs/PLAN.md).

---

## Dành cho Boss

### Cài đặt (một lần, khoảng 10 phút)

Mở cửa sổ dòng lệnh trên máy sẽ làm máy chủ và dán **đúng một dòng**:

```bash
# Linux · macOS
curl -fsSL https://github.com/Genesis-ryan-84-0567536339/Gen-Harness/releases/latest/download/install.sh | sh
```

```powershell
# Windows (PowerShell)
irm https://github.com/Genesis-ryan-84-0567536339/Gen-Harness/releases/latest/download/install.ps1 | iex
```

Trình cài tự kiểm máy, tự lo phần Docker nếu thiếu, tự tạo khoá bí mật rồi mở trình duyệt vào **trang thiết lập**. Nhập mã thiết lập
do trình cài in ra (mất mã thì chạy `genh reset-setup`). Trình duyệt có thể cảnh báo "Not secure" — chạy `genh trust-ca` một lần là hết.

Trang thiết lập là **một màn 3 bước** (Tài khoản → Nguồn AI → Xong). Bắt buộc chỉ có mã thiết lập, tài khoản Sếp + **mã PIN 6 số** và tên tổ chức;
nguồn AI có thể bấm **Làm sau** (Gen chưa trả lời được cho tới khi có nguồn AI). Kênh, nhóm, sàng lọc, agent, đội ngũ, sao lưu làm dần sau — các việc dở hiện ở
danh sách **Việc nên làm tiếp** (Mặt tiền › **Thêm**) và ở **Hướng dẫn thiết lập** trong Console.

### Cập nhật

**Tự động mỗi đêm, khoảng 03:00 — Sếp không phải làm gì.** Máy chỉ nhận bản đã được kiểm kỹ và ra đủ 24 giờ; có lỗi giữa chừng thì tự quay về
bản cũ, dữ liệu không mất. Muốn cập nhật ngay: Console → **Cập nhật ngay**, hoặc chạy `genh update`. Có gì mới ở mỗi bản: [CHANGELOG.md](CHANGELOG.md).

### Việc Sếp cần làm

Console → **Hướng dẫn thiết lập** → **Việc Sếp cần làm**. Một trang gom mọi việc *chỉ Sếp làm được* (đăng nhập tài khoản của chính Sếp, dán mã, tick quyền),
mỗi dòng có hướng dẫn từng bước và nút **Kiểm tra**; dòng chuyển **Đạt** là xong. Hiện có 10 dòng, **chỉ 1 dòng bắt buộc** (dòng 0 — có ít nhất 1 nguồn AI chạy được); 9 dòng còn lại là tuỳ chọn:

0. **Nguồn AI** (bắt buộc) — Gen chạy được khi có ít nhất 1 nguồn AI: đăng nhập Google / Antigravity, Claude Code CLI hoặc dán khoá API; bấm **Kiểm tra** để gọi thử.
1. **Gen-hub** (tuỳ chọn) — nối Kho dữ liệu; tuỳ chọn thêm quyền đọc lịch/mail/việc/Drive và quyền ghi Kho.
2. **Facebook** (tuỳ chọn) — đăng nhập tài khoản Facebook của Sếp trong cửa sổ trình duyệt từ xa.
3. **Google / Antigravity** (tuỳ chọn) và 4. **Claude Code CLI** (tuỳ chọn) — kiểm riêng từng nguồn AI dùng gói của Sếp.
5. **Jev** (tuỳ chọn). 6. **Telegram** (tuỳ chọn) — bot báo tin tới điện thoại Sếp. 7. **Truy cập từ xa** (tuỳ chọn) — vào Console từ điện thoại; chỉ nên làm khi có người khác dùng Console.
8. **Facebook trả lời** (tuỳ chọn) — thử một lần trả lời bình luận thật. 9. **Gen ghi Kho** (tuỳ chọn) — duyệt lần Gen ghi Kho đầu tiên.

Việc mới của từng bản nằm ở mục "Boss phải làm" trong `docs/releases/vX.Y.Z.md`; tổng hợp việc đang treo: [HANDOFF](docs/reports/HANDOFF-v0.1.1.md).

### Dùng Gen

Gen nằm ở khung chat bên phải. Hỏi bằng tiếng Việt: "Sáng nay có gì gấp?", "Chỉ tôi cách thêm khoá Gemini", "VIEC-12 là gì?", "Hôm nay tôi có lịch gì?".
Gen **chỉ đề xuất**: nháp tin, nhắc việc, giao người, trả lời/nhắn Facebook, **Ghi nhớ** (dặn "nhớ giúp em …" — xem/sửa/xoá ở Cài đặt › Bộ não AI › **Gen nhớ**)
và **Ghi vào Kho dữ liệu**. Mỗi thẻ có **Xác nhận / Sửa / Huỷ**; việc nhạy cảm (gửi ra ngoài, ghi Kho, đổi cấu hình) hỏi thêm **mã PIN**. Mỗi bản phát hành mới, Gen
đề xuất sẵn một "Phiên" để Sếp duyệt ghi vào Kho.

### Sao lưu — việc duy nhất Sếp nên nhớ

Máy tự sao lưu hằng đêm. Nhưng **khoá giải mã chỉ nằm trên máy này**: máy hỏng mà không có bản sao khoá ở nơi khác thì mọi bản sao lưu đều mất. Vì vậy hãy bật **một** trong hai:

```bash
genh offsite set <thư mục ổ USB hoặc NAS đã cắm>   # tự xuất bản sao mã hoá mỗi Chủ nhật ~05:30, giữ 4 bản
genh export --to <tệp>                              # hoặc xuất tay ra ổ khác, định kỳ (hỏi mật khẩu gói — cất ở nơi khác)
```

Chuyển sang máy mới: `genh import <tệp>`. Quên mật khẩu đăng nhập: `genh reset-password`. Chi tiết và các tình huống khác: [Sổ tay vận hành](docs/runbook.md).

### Truy cập từ điện thoại, mời nhân viên

Cài mới chỉ mở cho **chính máy chủ** (an toàn mặc định). Để vào từ điện thoại hoặc mời nhân viên: chạy `genh remote tailscale` (khuyên dùng) rồi làm theo hướng dẫn; xem
tình trạng bằng `genh remote`. Console có thẻ **Truy cập từ xa** (Cài đặt) và dòng 7 (tuỳ chọn) ở Việc Sếp cần làm.

### Khi có chuyện — nhờ giúp ở đâu

- **Console → Trợ giúp**: phiên bản, **Cập nhật phần mềm**, **Tạo gói chẩn đoán** (nhập PIN, tải về, gửi tệp zip cho Claude — đã lọc mật khẩu/khoá/token).
- **Chuông** và dải **Cần Sếp xử lý** ở màn **Hôm nay** của Console; **Telegram** tự báo khi máy chủ hỏng hoặc có việc gấp (tuỳ chọn: bật ở dòng 6 của Việc Sếp cần làm).
- **Hỏi Gen**: "Chỉ tôi cách …" — Gen mở trang và làm sáng đúng nút.
- **Sổ tay vận hành**: [docs/runbook.md](docs/runbook.md) — cập nhật kẹt, khôi phục, ngắt mạch Gen-hub, báo động.
- Báo lỗi cho Claude: kèm mã lỗi (ERR-…, "Mã yêu cầu") và gói chẩn đoán.

### Rủi ro khi đăng nhập tài khoản cá nhân (Zalo/WhatsApp/Facebook)

Gen-Harness kết nối Zalo/WhatsApp **như một thiết bị đăng nhập thêm của chính tài khoản cá nhân**, không qua cổng API chính thức dành cho doanh nghiệp
(những nền tảng này chưa cấp API mở tương đương); Facebook cá nhân thì qua một trình duyệt riêng mà **Sếp tự đăng nhập**. Trước khi hiện mã QR ở bước 5, Console luôn bắt
xác nhận đã đọc cảnh báo này (không có đường tắt bỏ qua):

> Gen-Harness kết nối [kênh] như một thiết bị đăng nhập của chính Sếp, không qua cổng chính thức cho doanh
> nghiệp. [Kênh] có thể tạm khoá hoặc hạn chế tài khoản nếu thấy hoạt động bất thường.

Khuyến nghị đi kèm:

- Dùng tài khoản **do chính Owner/người phụ trách sở hữu**, không dùng tài khoản của nhân viên hay khách. Không bao giờ tạo tài khoản giả.
- Chỉ lắng nghe các nhóm đã bật rõ ràng — mọi nhóm mới mặc định "Không nghe".
- Tin gửi đi luôn qua hàng đợi và ranh giới tự trị đã cấu hình; trả lời/nhắn Facebook chỉ gửi khi Sếp **Xác nhận và nhập mã PIN**, có ảnh chụp bằng chứng và nút **Dừng tất cả**.
- Có thể đăng xuất kênh bất cứ lúc nào, từ Console hoặc ngay trên ứng dụng điện thoại.

Vì đây là rủi ro thật (không phải hình thức), **cân nhắc kỹ trước khi kết nối tài khoản chính đang dùng cho công việc khác** — nên dùng một số/tài khoản riêng nếu có thể.

---

## Dành cho dev

### Yêu cầu hệ thống

- Docker Engine + Docker Compose v2 (`docker compose`, không phải `docker-compose` cũ).
- ~4 GB RAM rảnh, vài GB đĩa trống (Postgres, Redis, ảnh container; ảnh trình duyệt ~1,5 GB).
- Một máy Linux/macOS (hoặc WSL2 trên Windows) có thể mở cổng cho `caddy` (mặc định `:8443`, chỉ nghe `127.0.0.1` ở bản cài mới).
- Để đăng nhập kênh thật: điện thoại đã cài Zalo/WhatsApp, quét được mã QR.

### Dựng từ mã nguồn

Người dùng cuối cài bằng `genh` (mục trên). Dev dựng bằng **`docker compose`** — không cần cài Python/Node/Postgres lên máy:

```bash
git clone <repo-này> Gen-Harness && cd Gen-Harness

# 1) Sinh khoá bí mật (secrets/gh_master_key, secrets/gh_bridge_key — KHÔNG commit) + tạo .env từ .env.example
make secrets
# → mở .env, đổi POSTGRES_PASSWORD và GH_APP_DB_PASSWORD (mặc định chỉ dùng được cho máy cá nhân/dev)

# 2) Dựng và khởi động toàn bộ hệ thống
make up
# tương đương: docker compose -f deploy/compose.yaml --env-file .env up -d --build
```

`make up` kéo lên **13 dịch vụ** (các khoá dưới `services:` của `deploy/compose.yaml`):

| Dịch vụ | Vai trò |
|---|---|
| `proxy` (Caddy) | Cổng vào duy nhất mở ra ngoài, HTTPS tự ký, mặc định `:8443` |
| `web` | Console (giao diện) |
| `migrate` | Chạy migration Postgres một lần rồi thoát, trước khi `api`/`worker` khởi động (không chạy thường trực) |
| `api` | FastAPI — mọi nghiệp vụ, xác thực, WebSocket realtime, Gen |
| `worker` | Tiến trình nền (arq): sàng lọc, hook nghiệp vụ, việc định kỳ (sao lưu, Bản tin Gen, kiểm phiên…) |
| `agy-box` | Hộp riêng Antigravity (v0.1.63): chạy `agy` cho việc có người ngoài (soạn trả lời khách, sàng lọc tin người lạ, Gen của nhân viên); không DB, không Redis, không khoá master, không volume — chỉ khoá ký `gh_agy_key` (phiên agy gửi theo từng lượt), chỉ ra Internet qua `agy-egress` |
| `agy-egress` | Proxy lối ra của `agy-box` (v0.1.63): chỉ cổng 443, chặn IP nội bộ / gateway / LAN |
| `bridge` | Kết nối kênh nhắn tin thật (Zalo/WhatsApp qua QR), chỉ giữ khoá bridge, không bao giờ có khoá master |
| `browser` | Trình duyệt riêng (Playwright/Chromium) cho Facebook cá nhân; không DB, không khoá master, không thấy Redis chính |
| `browser-redis` | Redis riêng của `browser` (không lưu đĩa, ACL hẹp) — kênh giao thức `gh:browser:*` |
| `browser-egress` | Proxy ra ngoài của `browser`, chỉ cho phép tên miền Facebook, chặn IP nội bộ |
| `db` | Postgres 16 (pgvector, pg_partman) |
| `redis` | Redis Streams (hàng đợi sự kiện) + cache |

Tài liệu và bản sao lưu lưu trên đĩa qua volume Docker `gh_objects` (`GH_OBJECTS_DIR`, gắn vào `api` + `worker` — xem [v0.1.1](docs/releases/v0.1.1.md)),
không còn dịch vụ MinIO riêng như bản trước v0.1.1.

Xem tiến trình dựng và log:

```bash
make ps            # trạng thái từng dịch vụ (đợi tới khi tất cả "healthy")
make logs           # log trực tiếp mọi dịch vụ
make logs-token      # in mã thiết lập một lần (nếu GH_SETUP_TOKEN để trống trong .env, api tự sinh)
```

Khi `api` và `web` đã "healthy", mở **`https://localhost:8443/setup`** (trình duyệt sẽ cảnh báo chứng chỉ tự
ký ở môi trường dev — chấp nhận tiếp tục) và nhập mã thiết lập từ `make logs-token`.

### Trình thiết lập Owner (một màn, 3 bước)

Trình thiết lập ở `/setup` (`?buoc=tai-khoan|nguon-ai|xong`) đưa máy mới tới trạng thái dùng được trong vài lần nhập. Phía sau vẫn là 12 bước dữ liệu (`PUT /setup/steps/<n>`);
**chỉ bước 1–3 và 12 bắt buộc**, bước 4–11 ghi "để sau" khi hoàn tất và làm lại được ở màn tương ứng của Console.

1. **Tài khoản** — gộp bước 1–3: mã thiết lập một lần; tên, email, mật khẩu, **PIN 6 số** (PIN xác nhận mọi thao tác nhạy cảm sau này); tên tổ chức (múi giờ, tiền tệ,
   cách agent xưng hô điền sẵn, sửa trong "Tuỳ chỉnh").
2. **Nguồn AI** — đăng nhập Google / Antigravity rồi dán mã xác thực, hoặc dán khoá API (Gemini, DeepSeek, tương thích OpenAI) và bấm **Kiểm tra**; **Làm sau** thì Gen chưa trả lời được.
   Đổi nguồn AI sau này: `/setup?buoc=nguon-ai`.
3. **Xong** — tóm tắt rồi vào Mặt tiền; có nút "Nối Zalo ngay".

Các việc còn lại làm dần ở Console, mỗi việc có mặc định an toàn:

- **Kết nối kênh** — đăng nhập Zalo/WhatsApp bằng **mã QR** (cảnh báo rủi ro bắt buộc ở mục "Rủi ro" phía trên).
- **Chọn nhóm lắng nghe** — mặc định MỌI nhóm mới ở chế độ "Không nghe"; Owner tự bật từng nhóm muốn theo dõi.
- **Sàng lọc dữ liệu** — chọn ngành; chu kỳ và ngưỡng số lượng tin có sẵn mặc định.
- **Agent đầu tiên** — chọn mẫu agent (không còn khung thử trò chuyện trong trình thiết lập).
- **Tự trị & ranh giới** — mức tự trị của agent (3 mức ở giao diện, 0–6 ở Nâng cao); các ranh giới cứng không tắt được (agent không tự ra quyết định nhân sự,
  tin gửi ra ngoài luôn chờ duyệt, v.v. — [Kiến trúc](docs/ARCHITECTURE.md) §1).
- **Mời đội ngũ** *(tuỳ chọn)* — tạo tài khoản + mật khẩu tạm cho nhân viên (mật khẩu tạm hiện ngay trên màn để Owner tự gửi; chưa có SMTP).
- **Sao lưu** *(tuỳ chọn)* — lịch tự bật 02:00 hằng ngày; đổi lịch/giờ ở Cài đặt › Sao lưu & cập nhật; cơ chế backup thật đọc đúng cấu hình này.

### Sao lưu / khôi phục (cơ chế)

Hệ thống tự sao lưu định kỳ theo lịch cấu hình ở Cài đặt › Sao lưu & cập nhật (worker quét cấu hình mỗi 15 phút): `pg_dump` toàn bộ
CSDL, **mã hoá** (khoá riêng `GH_BACKUP_KEY` nếu có, không thì khoá master), lưu vào kho đối tượng đĩa cục bộ (volume
`gh_objects`). Vòng đời: giữ **7 bản hằng ngày + 4 bản hằng tuần + 12 bản hằng tháng gần nhất**, tự dọn bản thừa.

Lệnh thủ công (chạy trong container `api`/`worker`, hoặc từ máy dev qua `make`):

```bash
make backup                       # sao lưu ngay
make backup-list                  # liệt kê các bản đang giữ
make restore BACKUP=<khoá-bản-backup>   # khôi phục một bản (mặc định vào CSDL đang cấu hình)
```

Trên máy cài bằng `genh`: `genh backup [--to path]`, `genh restore <khoá>`, `genh export --to <tệp>` / `genh import <tệp> [--yes]`, `genh offsite set|run|status|disable`
(xem [installer](docs/handoff/05-installer.md) mục "Lệnh vận hành" và [runbook](docs/runbook.md)). Cơ chế, thuật toán vòng đời và kết quả kiểm round-trip thật:
[phase-5-backup.md](docs/reports/phase-5-backup.md). Gói `.ghbundle` (CSDL + object + bí mật, mã hoá lại bằng khoá master máy đích): [v0.1.1](docs/releases/v0.1.1.md);
khoá backup riêng `GH_BACKUP_KEY`: [v0.1.2](docs/releases/v0.1.2.md).

**⚠️ Cảnh báo — khoá chỉ nằm trên máy này**: `GH_MASTER_KEY` (mã hoá bí mật ứng dụng) và `GH_BACKUP_KEY` chỉ tồn tại trên máy đang chạy, không tự sao lưu ra ngoài.
Máy hỏng/ổ đĩa hỏng **mà không có bản sao khoá ở nơi khác** → mọi bản backup tại chỗ đều **không giải mã lại được**. Tự bảo vệ bằng `genh export`/`genh offsite` ra ổ KHÁC;
gói `.ghbundle` mang theo cả khoá master (và khoá bridge nếu có), bảo vệ bằng mật khẩu riêng (`GH_BUNDLE_PASSWORD`, argon2id + AES-256-GCM). Giữ mật khẩu gói ở nơi khác — mất mật khẩu gói cũng coi như mất gói.

### Phát hành

Phát hành = tăng `VERSION` (ở gốc repo) trong PR, merge vào main — GitHub Actions (`release.yml`) tự chạy CI, build, tạo Release **bản thử** (prerelease), chạy E2E cài thật; xanh hết mới
nâng thành bản chính thức (latest) — không ai tạo tag/release tay. Máy người dùng chỉ thấy bản chính thức, lịch đêm đợi thêm 24 giờ. Mỗi bản ghi vào [CHANGELOG.md](CHANGELOG.md) (3–5 dòng)
và `docs/releases/vX.Y.Z.md`; [HANDOFF](docs/reports/HANDOFF-v0.1.1.md) chỉ giữ hiện trạng + việc dở (≤ 200 dòng). Chi tiết cổng phát hành: [installer](docs/handoff/05-installer.md) mục "Cổng phát hành".

### Phát triển

Monorepo npm workspaces + các app Python/Go riêng:

```
apps/
  api/       FastAPI + arq worker (Python, .venv riêng — apps/api/gh/)
  web/       Console (React/Vite — apps/web/src/)
  bridge/    Kết nối kênh nhắn tin thật (Node)
  browser/   Trình duyệt riêng cho mạng xã hội (Python + Playwright — apps/browser/ghb/)
  genh/      Trình cài đặt/vận hành một lệnh (Go)
packages/
  contracts/ Kiểu dữ liệu dùng chung giữa api/web
db/          Migration SQL (db/sql, đi cùng Alembic ở apps/api/migrations)
deploy/      Dockerfile từng dịch vụ + deploy/compose.yaml
docs/        ARCHITECTURE.md, PLAN.md, ROADMAP.md, runbook.md, thiết kế (docs/design/), đặc tả bàn giao (docs/handoff/),
             lịch sử từng bản (docs/releases/), báo cáo (docs/reports/), kiểm toán (docs/audit/)
```

Chạy test (không cần Docker — dùng Postgres/Redis local, xem `apps/api/tests/conftest.py`):

```bash
make api-lint                     # ruff + mypy (apps/api)
make api-test                     # pytest (apps/api) — gần 2.000 test
make web-test                     # test unit Console
make bridge-test                  # test bridge
make test                         # cả bốn lệnh trên
python3 -m unittest discover -s .github/scripts   # kiểm tra repo: cổng phát hành, link tài liệu, đồng bộ bản nhúng…
```

Dữ liệu mẫu để phát triển/demo (đi đúng luồng raw → sàng lọc → sạch, không chèn thẳng vào bảng):

```bash
make seed-demo                    # nạp 115 sự kiện mẫu theo docs/design/seed-data.json
make seed-demo-clean              # xoá kết luận đã sinh (giữ nguyên bản ghi thô bất biến)
```

`make seed-demo` chỉ dành cho cơ sở dữ liệu trống: chạy **trước** trình thiết lập; đã tạo Owner (hoặc đã có dữ liệu thật) thì lệnh từ chối
(thoát mã 2, kèm câu tiếng Việt). Gõ trực tiếp `python -m gh.seed_demo seed` thì phải thêm `--force` hoặc đặt `GH_ALLOW_SEED_DEMO=1`.

Chạy API không cần Docker để phát triển nhanh: `make api-dev` (cần `GH_DATABASE_URL`/`GH_REDIS_URL` trỏ tới
Postgres/Redis local, hoặc export các biến tương ứng trước khi chạy).

Lần đầu (và mỗi khi kéo về `uv.lock` mới): `make api-sync` — cài `apps/api/.venv` đúng theo `uv.lock`
(`uv sync --frozen --extra dev`); thiếu bước này thì `make api-dev`/`api-test`/`api-lint` báo không thấy `.venv`.
Đổi phụ thuộc trong `pyproject.toml` thì `make lock` rồi commit `uv.lock`. Cả hai lệnh chạy uv 0.12.23 qua `uvx`
(cùng bản với CI và ảnh Docker — khác bản thì `uv lock --check` của CI có thể đỏ); cần có `uv` trên máy
(https://docs.astral.sh/uv/).

### Tài liệu chi tiết hơn

- [Kiến trúc](docs/ARCHITECTURE.md) — kiến trúc hệ thống, ràng buộc bắt buộc (R1–R10), luồng dữ liệu. [Kế hoạch giai đoạn](docs/PLAN.md) — quyết định thiết kế (Q1–Q7).
- [Lộ trình + mục Nợ](docs/ROADMAP.md) · [Nhật ký thay đổi](CHANGELOG.md) · [Hiện trạng + việc dở](docs/reports/HANDOFF-v0.1.1.md) · [Sổ tay vận hành](docs/runbook.md).
- Thiết kế Gen: [gen-v1](docs/design/gen-v1.md) (Gen, đề xuất, Gen nhớ), [gen-hub-link](docs/design/gen-hub-link.md) (Gen-hub, đọc/ghi Kho),
  [gen-browser-agent](docs/design/gen-browser-agent.md) (mạng xã hội). Giao thức: [`docs/api/`](docs/api/phase-1.md).
- [Đặc tả trình cài đặt `genh`](docs/handoff/05-installer.md) — lệnh vận hành, cập nhật tự lành, cổng phát hành.
- Báo cáo từng giai đoạn đã hoàn thành: [phase-1](docs/reports/phase-1.md) … [phase-4](docs/reports/phase-4.md) (nền tảng → các cụm màn Console),
  [phase-5-visual](docs/reports/phase-5-visual.md) (so ảnh pixel 21 màn), [phase-5-e2e-live](docs/reports/phase-5-e2e-live.md) (luồng 2–8 trên hệ thống thật),
  [phase-5-resilience](docs/reports/phase-5-resilience.md) (chịu lỗi Postgres/Redis/bridge/provider), [phase-5-performance](docs/reports/phase-5-performance.md)
  (benchmark 10 triệu bản ghi), [phase-5-backup](docs/reports/phase-5-backup.md) (sao lưu/khôi phục).

## License

© 2026 Gen Labs — **All rights reserved.** Repo công khai để xem, không cấp quyền sử dụng. Xem [LICENSE](LICENSE).
Mã nguồn đến v0.1.74 đã được công bố trước đó theo Apache-2.0 tại repo Gen-Harness; mọi thay đổi từ khi chuyển về GenOS thuộc license trên.
