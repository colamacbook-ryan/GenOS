# Báo cáo giai đoạn 6 — Trình cài một lệnh (genh)

Nhánh `claude/zen-lovelace-ph1qa2` · PR #3 · 25/09/2026

## Đã xong

| Phần | Nội dung | Kết quả |
|---|---|---|
| Bootstrap | `install.sh` (Linux/macOS, `sh` thuần), `install.ps1` (Windows PowerShell) | Dò OS/kiến trúc, tải `genh-<os>-<arch>` + `checksums.txt` từ GitHub Release mới nhất, kiểm SHA-256, thêm PATH, `exec genh install`. |
| `genh` — Bước 1 Kiểm tra máy (3%) | OS/kiến trúc, RAM, đĩa trống, cổng, mạng, đồng hồ | Thật, có test. |
| `genh` — Bước 2 Chuẩn bị runtime (17%) | Dò Docker Engine ≥24 + Compose v2 có sẵn; tự cài rootless/Colima/WSL2 khi thiếu (chỉ khi `--yes`) | Thật cho đường "đã có runtime hợp lệ"; đường "tự cài runtime" có code nhưng chưa kiểm được trên máy thật thiếu Docker (sandbox không có quyền cài Docker/WSL2 thật). |
| `genh` — Bước 3 Tải image (50%) | Pull song song image đã có `image:` cố định (hiện: `proxy`, `redis`, `objects`), % theo byte thật từng layer | Thật cho service đã publish; service dùng `build:` cục bộ (`api`/`web`/`bridge`/`db`) được báo rõ "chưa có bản phát hành", không âm thầm bỏ qua — xem giới hạn CI phát hành bên dưới. |
| `genh` — Bước 4 Sinh bí mật & cấu hình (3%) | Khoá master, mật khẩu DB, khoá MinIO, khoá backup, CA TLS nội bộ, setup token | Thật, idempotent, quyền 0600/0700. |
| `genh` — Bước 5 Khởi động dữ liệu (8%) | `docker compose up -d db redis objects` + chờ healthy | Thật. |
| `genh` — Bước 6 Tạo cấu trúc dữ liệu (9%) | `docker compose run migrate` (service `migrate` riêng, `alembic upgrade heads`) | Thật; tiến độ đếm theo dòng log "Running upgrade" thật, không suy đoán. Khác biệt với mô tả gốc trong tài liệu ("exec vào container api") đã ghi chú rõ trong code — compose.yaml thật dùng service `migrate` riêng vì `api`/`worker` phụ thuộc `migrate` xong trước. |
| `genh` — Bước 7 Khởi động dịch vụ (6%) | `docker compose up -d api worker bridge web proxy` + chờ `/api/v1/ready` | Thật. CA của Caddy (`tls internal`) khác CA của `secretgen` — dùng `InsecureSkipVerify` có chủ đích cho lượt gọi readiness nội bộ lúc cài, ghi chú rõ trong code. |
| `genh` — Bước 8 Hoàn tất (4%) | Tin cậy CA (khi `--yes`), tạo lối tắt, mở trình duyệt vào `/setup?token=…`, hiện màn "Hoàn tất" | Thật, khoan dung lỗi môi trường (không sudo/DISPLAY/NSS db → cảnh báo, không chặn cài đặt). Đường dẫn CA thật của Caddy (`/data/caddy/pki/authorities/local/root.crt`) suy luận từ hành vi mặc định `tls internal`, chưa xác nhận bằng chạy thật (không có Docker trong sandbox). |
| Lệnh vận hành | `status`, `open`, `logs`, `stop`, `start`, `backup [--to]`, `restore`, `doctor`, `reset-setup`, `update [--channel]`, `uninstall [--keep-data]` | Đủ cả 11 lệnh tài liệu liệt kê. `update` có rollback tự động thật khi bất kỳ bước nào sau backup thất bại (kể cả trường hợp rollback chính nó cũng thất bại — báo rõ lệnh tay). Giới hạn từng lệnh ghi trong code + mục dưới. |
| CI phát hành | `.github/workflows/release.yml` (trigger `tags: v*`, không tự kích hoạt) | Build `genh` 6 nền tảng, build+push image đa kiến trúc (api/web/bridge/db) lên GHCR, sinh `checksums.txt`, ký cosign keyless, tạo GitHub Release. |
| Ma trận test trình cài | `.github/workflows/installer-matrix.yml` | Build + smoke test (`genh version`/`help`) trên `ubuntu-22.04`/`24.04`/`macos-14`/`windows-2022`; thêm thử cài đặt best-effort trên Ubuntu (có Docker sẵn). |

**Tự kiểm tra độc lập** (xác minh lại toàn bộ sau mỗi lượt bàn giao, không chỉ tin báo cáo của agent thực hiện):
```
cd apps/genh
go build ./... && go vet ./...
go test ./... -count=1          # 171 test, 0 fail
go test -race ./... -count=1    # sạch, không phát hiện race
golangci-lint run ./...         # 0 issue trong mọi file mới/sửa của giai đoạn 6
```
Backend Python không đổi hành vi ở giai đoạn này ngoài một sửa CI (xem mục riêng bên dưới).

## Sửa ngoài phạm vi trực tiếp giai đoạn 6

Trước khi tiếp tục giai đoạn 6, đã xử lý theo yêu cầu gộp PR #2 (giai đoạn 1+2) vào `main` trước:

- **PR #2 CI đỏ**: `ruff` báo E501 ở `tests/conftest.py` (sửa xuống dòng), và vì CI chạy `ruff check && mypy` (toán tử `&&`), lỗi ruff đó đã âm thầm che 49 lỗi mypy thật trong 15 tệp suốt từ đầu — nguyên nhân gốc: `sqlalchemy[asyncio]` không ghim trần trên, venv mới resolve ra 2.1.1 (đòi type annotation tường minh mà 2.0.x không đòi). Ghim `<2.1`, xác minh lại toàn bộ (686 test, ruff+mypy sạch), merge PR #2 vào `main`.
- Áp cùng bản ghim phòng ngừa vào nhánh `claude/zen-lovelace-ph1qa2` (venv nhánh này đang dùng đúng 2.0.54 nên không có lỗi mới, chỉ phòng trường hợp venv bị tạo lại sau này).

## Còn lại / giới hạn đã biết (trung thực, không bịa)

1. **Ảnh `api`/`web`/`bridge`/`db` chưa publish sẵn** — `deploy/compose.yaml` vẫn dùng `build:` cục bộ cho 4 service này. `release.yml` đã viết đủ job build+push đa kiến trúc lên GHCR, nhưng **chưa từng được kích hoạt** (không tạo/push tag `v*` — xác nhận `git tag` và `git ls-remote --tags` đều rỗng). Vì vậy Bước 3 hiện tại vẫn báo "chưa có bản phát hành" cho 4 service này trên một lượt cài thật — đây KHÔNG phải lỗi, mà đúng trạng thái "chưa phát hành lần nào".
2. **`compose.yaml` ghim digest — ĐÃ nhúng vào binary `genh` qua `go:embed`** (bổ sung sau lượt bàn giao đầu của giai đoạn 6): `apps/genh/internal/compose/embed.go` nhúng `embedded_compose.yaml`; `Locate()` rơi về ghi bản nhúng ra `<installDir>/deploy/compose.yaml` khi không tìm thấy gì trên đĩa (idempotent — không đè compose.yaml Owner đã có). `release.yml` job `build-genh` giờ phụ thuộc job `pin-compose`, tải `compose.release.yaml` đã ghim digest thật và ghi đè lên `embedded_compose.yaml` TRƯỚC khi build từng nền tảng — có test riêng (`TestLocate_FallsBackToEmbeddedComposeUnderInstallDir`) xác nhận hành vi rơi về đúng và không đè tuỳ chỉnh của Owner. Vẫn phụ thuộc mục 1 (image thật) để có ý nghĩa trên một bản phát hành thật.
3. **Rootfs WSL** — chỉ tải Alpine minirootfs chính thức, CHƯA đóng gói Docker Engine bên trong (việc này cần chroot/binfmt và kiểm `wsl --import` thật trên Windows, không khả thi làm chắc chắn đúng trong sandbox Linux này). TODO rõ ràng để lại trong `release.yml`.
4. **`installer-matrix.yml` chỉ build + smoke test**, chưa chạy được một lượt "cài sạch → `/api/ready` xanh → update → uninstall" thật trên ma trận OS như tài liệu mô tả — cần ảnh đã publish thật (mục 1) trước.
5. **`genh` Bước 2 (tự cài container runtime khi thiếu)** và **Bước 8 (tin cậy CA vào OS)** có code đầy đủ cho cả 3 nền tảng nhưng chỉ kiểm được qua test đơn vị với runner/HTTP client giả — sandbox này không có Docker daemon, không có quyền sudo tương tác, không có macOS/Windows thật để chạy đầu-cuối.
6. **`genh restore <khoá>`** chỉ nhận khoá ObjectStore có sẵn (giới hạn của `restore_backup()` trong `apps/api/gh/backup.py`, không nhận file host tuỳ ý). **`genh backup --to path`** ghi ra host ở dạng vẫn mã hoá (khoá giải mã chỉ container biết).
7. **`genh update --channel`** được validate và ghi log nhưng chưa đổi hành vi tải thật (chưa có pipeline phát hành theo kênh — phụ thuộc mục 1).
8. **`genh uninstall`** không tự gỡ Docker Engine/Colima/WSL2 do genh tự cài ở Bước 2 (Bước 2 hiện không đánh dấu được nguồn gốc runtime để phân biệt "genh cài" với "đã có sẵn" — tự gỡ nhầm rủi ro cao hơn để lại); gỡ PATH chỉ xử lý rc file Linux/macOS, chưa tự sửa registry User Path trên Windows.

## Cách dựng và bài học vận hành phiên làm việc

Toàn bộ giai đoạn 6 (8 bước cài đặt + 11 lệnh vận hành + CI phát hành + ma trận test) được chia thành nhiều lượt giao việc cho agent nền, mỗi lượt bám sát một phần rõ ràng của `docs/handoff/05-installer.md`, xác minh lại độc lập (build/vet/test/race/lint tự chạy lại, không chỉ tin báo cáo) trước khi giao lượt tiếp theo — đúng khuôn mẫu các giai đoạn trước. Điểm mới đáng ghi lại:

- **Hai agent chạy song song trên cùng một cây làm việc** (không dùng worktree riêng) khi phạm vi không giao nhau — lệnh vận hành (`apps/genh/internal/ops`, `cmd/genh`) và CI phát hành (`.github/workflows/`, `deploy/`) được giao đồng thời với chỉ dẫn rõ ràng "tuyệt đối không đụng thư mục của nhau", xác nhận bằng `git status`/`git add` chỉ đúng file của mình. Hoạt động tốt vì ranh giới file được vạch rõ trước khi giao việc.
- **Không kích hoạt CI phát hành thật** — mọi agent liên quan đều được nhắc rõ ràng, nhiều lần, không được tạo/push git tag (hành động sẽ publish image công khai lên GHCR và tạo GitHub Release thật, không dễ hoàn tác) — đã xác nhận bằng `git tag`/`git ls-remote --tags` rỗng sau mỗi lượt.
- **Phát hiện khác biệt tài liệu vs. thực tế qua đọc code thật, không đoán**: Bước 6 dùng service `migrate` riêng (không phải "exec vào api" như tài liệu tóm tắt), CA thật là của Caddy (`tls internal`) chứ không phải CA của `secretgen` — cả hai đều được phát hiện bằng cách đọc `deploy/compose.yaml`/`Caddyfile` thật thay vì làm theo tài liệu một cách máy móc, và ghi chú lại trong code cho phiên sau.

## Tự kiểm tra

```
cd apps/genh
go build ./... && go vet ./...
go test ./... -count=1
go test -race ./... -count=1
golangci-lint run ./...
```
