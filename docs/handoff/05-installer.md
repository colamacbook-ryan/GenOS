# 05 · Trình cài đặt một lệnh (TUI)

## Mục tiêu

- **Một lệnh duy nhất** trên mỗi hệ điều hành.
- **Không ỷ lại môi trường sẵn có.** Máy người dùng chỉ được giả định có shell mặc định (`sh` trên Linux/macOS, PowerShell 5.1 trên Windows 10/11) và kết nối mạng. Không cần có sẵn Docker, git, curl-plugin, Python, Node, psql, make hay bất kỳ công cụ nào khác.
- **TUI chuyên nghiệp:** một khung gọn, thanh tiến độ tổng có %, danh sách bước có trạng thái, không trút log thô ra màn hình.
- Kết thúc bằng việc **mở trình duyệt vào trình thiết lập Owner** (`docs/06`).

## Lệnh cài

```sh
# Linux · macOS
curl -fsSL https://github.com/<owner>/Gen-Harness/releases/latest/download/install.sh | sh
```

```powershell
# Windows (PowerShell)
irm https://github.com/<owner>/Gen-Harness/releases/latest/download/install.ps1 | iex
```

Nếu máy Linux tối giản không có `curl`, `install.sh` cũng chạy được bằng `wget -qO- … | sh`; README ghi cả hai.

## Hai tầng

**1. Bootstrap (`install.sh` / `install.ps1`, ≤ 150 dòng mỗi tệp)** — việc duy nhất là lấy về binary `genh` đúng nền tảng và chạy nó.

- Phát hiện OS + kiến trúc: `linux-amd64`, `linux-arm64`, `darwin-amd64`, `darwin-arm64`, `windows-amd64`, `windows-arm64`.
- Tải `genh-<os>-<arch>` từ GitHub Releases vào `~/.gen-harness/bin/` (Windows: `%LOCALAPPDATA%\GenHarness\bin\`).
  Mặc định lấy **bản chính thức (latest)** qua `/releases/latest` — bản thử (prerelease) không bao giờ tới máy người dùng.
  Từ v0.1.33 `install.sh` nhận biến tuỳ chọn `GEN_HARNESS_RELEASE_TAG` (chỉ dùng cho CI/E2E) để cài đúng một tag, kể cả bản thử:
  tag phải đúng regex `^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$`; tag không tồn tại/thiếu asset → báo lỗi dễ hiểu (không
  phải dòng 404 trơ trọi của curl); máy **đã cài** thì chạy `genh update --no-self-update` (không để genh tự thay bản ghim
  bằng `releases/latest`). Test: `apps/genh/cmd/genh/installsh_unix_test.go` (chạy `install.sh` thật với curl/genh giả).
- **Kiểm SHA-256** theo `checksums.txt` của cùng bản phát hành. Chữ ký cosign có ĐÍNH KÈM trên Release nhưng
  `install.sh`/`install.ps1`/`genh` **CHƯA kiểm** (việc sau); sai SHA-256 → dừng, không chạy.
- Thêm thư mục vào PATH của người dùng (không cần quyền admin).
- `exec genh install` — từ đây TUI tiếp quản.

**2. `genh` (Go, binary tĩnh, `CGO_ENABLED=0`)** — nhúng sẵn `compose.yaml`, cấu hình Caddy, và mọi thứ cần để dựng hệ thống. Tự mang theo hoặc tự tải **có kiểm checksum** mọi công cụ nó cần, đặt trong `~/.gen-harness/` — không ghi đè công cụ sẵn có của người dùng.

## Container runtime — tự cung cấp

| Hệ điều hành | Có sẵn Docker hợp lệ | Không có |
|---|---|---|
| Linux | Dùng nếu Engine ≥ 24 và Compose v2 | Cài **Docker Engine rootless** vào `~/.gen-harness/runtime` (binary tĩnh từ download.docker.com + `rootlesskit`), systemd user service. Chỉ dùng `sudo` cho bước bắt buộc (`newuidmap`), hỏi trước, giải thích lý do |
| macOS | Dùng nếu Docker Desktop / OrbStack / Colima đang chạy | Tải **Colima + Lima + docker CLI + compose plugin** bản tĩnh vào `~/.gen-harness/runtime`, tạo VM `gen-harness` (4 CPU, 6 GB RAM, 60 GB đĩa — điều chỉnh theo máy) |
| Windows | Dùng nếu Docker Desktop đang chạy | Bật **WSL2** (cần UAC một lần, có thể cần khởi động lại — trình cài tự tiếp tục sau khi đăng nhập lại qua RunOnce), nhập bản phân phối tối giản `gen-harness` (rootfs Alpine đóng gói kèm bản phát hành) chứa Docker Engine; `genh.exe` điều khiển qua `wsl -d gen-harness` |

Compose luôn dùng **bản compose plugin do `genh` mang theo**, không phụ thuộc bản của người dùng. Mọi công cụ khác (psql, pg_dump, mc, openssl…) chạy **bên trong container**.

## Các bước và trọng số tiến độ

% tổng = tổng có trọng số của % từng bước. Bước tải image tính theo **byte thật của các layer** (đọc tiến độ từ Docker API), không đoán.

| # | Bước | Trọng số | Nội dung |
|---|---|---|---|
| 1 | Kiểm tra máy | 3% | OS, kiến trúc, RAM ≥ 4 GB (khuyến nghị 8), đĩa trống ≥ 5 GB (khuyến nghị 10), cổng 8443 rảnh, kết nối mạng, đồng hồ hệ thống |
| 2 | Chuẩn bị container runtime | 17% | Như bảng trên. Bỏ qua (tính xong ngay) nếu đã có runtime hợp lệ |
| 3 | Tải image | 50% | `db`, `redis`, `objects`, `proxy`, `api`, `web`, `bridge` — tải song song, hiển thị MB/s và thời gian còn lại |
| 4 | Sinh bí mật & cấu hình | 3% | Khoá master, mật khẩu DB, khoá MinIO, khoá backup, CA TLS nội bộ, setup token một lần. Ghi `~/.gen-harness/config/` quyền 600 |
| 5 | Khởi động dữ liệu | 8% | `db`, `redis`, `objects` → chờ healthy |
| 6 | Tạo cấu trúc dữ liệu | 9% | Chạy migration trong container `api` (tiến độ theo số migration) |
| 7 | Khởi động dịch vụ | 6% | `api`, `worker`, `bridge`, `web`, `proxy` → chờ `/api/ready` |
| 8 | Hoàn tất | 4% | Tin cậy CA (hỏi), tạo lối tắt, mở trình duyệt `https://localhost:8443/setup?token=…`, **v0.1.5:** bật `genh auto-update` (tắt bằng `--no-auto-update` lúc cài — xem README mục "Nâng cấp") |

## Giao diện TUI

Khung 76 cột, căn giữa, viền bo. Màu lấy từ token Nocturne (accent `#9184d9`, OK/WARN/BAD như `docs/02`), tự tắt màu khi `NO_COLOR` hoặc terminal không hỗ trợ.

```
╭──────────────────────────────────────────────────────────────────────────╮
│  GEN-HARNESS  ·  Genesis Harness OS  v2.2.0                              │
│  Đang cài đặt vào ~/.gen-harness                                         │
│                                                                          │
│  ██████████████████████████████░░░░░░░░░░░░░░░░░░░░░░   58%   ~2 phút   │
│                                                                          │
│  ✓  Kiểm tra máy                     8 GB RAM · 112 GB trống      0:02   │
│  ✓  Chuẩn bị container runtime       Docker Engine 27.1 (sẵn có)  0:01   │
│  ⠼  Tải image                        412 / 690 MB · 18.4 MB/s            │
│       api      ███████████████████░░░  86%                               │
│       bridge   ████████████░░░░░░░░░░  54%                               │
│       db       ██████████████████████  xong                              │
│  ·  Sinh bí mật & cấu hình                                               │
│  ·  Khởi động dữ liệu                                                    │
│  ·  Tạo cấu trúc dữ liệu                                                 │
│  ·  Khởi động dịch vụ                                                    │
│  ·  Hoàn tất                                                             │
│                                                                          │
│  l  xem log     q  huỷ an toàn                                           │
╰──────────────────────────────────────────────────────────────────────────╯
```

- Trạng thái bước: `·` chờ · spinner đang chạy · `✓` xong (OK) · `!` cảnh báo (WARN, vẫn tiếp tục) · `✕` lỗi (BAD).
- Dòng con chỉ hiện ở bước đang chạy, tối đa 4 dòng.
- `l` mở khung log cuộn (tail 200 dòng); log đầy đủ luôn ghi vào `~/.gen-harness/logs/install-<timestamp>.log`.
- `q` huỷ an toàn: dừng container đã tạo, giữ image đã tải để lần sau nhanh hơn.
- Không có TTY (CI, pipe): tự chuyển sang chế độ dòng — mỗi bước một dòng `[ 58%] Tải image 412/690 MB` — cùng mã thoát.

Màn kết thúc:

```
╭──────────────────────────────────────────────────────────────────────────╮
│  ✓  Gen-Harness đã sẵn sàng                                  4 phút 12s  │
│                                                                          │
│     Mở trình thiết lập:  https://localhost:8443/setup                    │
│     Mã thiết lập:        K7QF-2MXD-9PLA    (hết hạn sau 24 giờ)          │
│                                                                          │
│     Đã mở trình duyệt. Nếu chưa thấy, bấm vào đường dẫn trên.            │
│                                                                          │
│     genh status    trạng thái        genh logs     xem log               │
│     genh update    cập nhật          genh backup   sao lưu               │
╰──────────────────────────────────────────────────────────────────────────╯
```

## Lỗi

Mỗi lỗi hiện: **chuyện gì xảy ra · vì sao · làm gì tiếp**, kèm mã lỗi tra được (`GH-E0xx`) và nút `r` thử lại bước đó. Ví dụ:

```
✕  Khởi động dịch vụ
   Cổng 8443 đang bị tiến trình khác dùng (pid 4412, nginx).
   Chọn cổng khác:  genh install --port 9443     hoặc dừng tiến trình đó rồi bấm r.
   GH-E021 · log: ~/.gen-harness/logs/install-20260923-2103.log
```

Mọi bước **idempotent**: chạy lại `genh install` sau lỗi tiếp tục từ bước dở, không tạo lại bí mật đã có, không xoá dữ liệu.

## Lệnh vận hành

| Lệnh | Việc |
|---|---|
| `genh status` | Bảng dịch vụ + healthy + phiên bản + dung lượng dữ liệu. **v0.1.37 (F-73):** thêm dòng **"Tự chạy lại khi bật máy"** (xem mục "Tự chạy lại khi bật máy" dưới bảng) và ghi `run/autostart-status.json` |
| `genh open` | Mở Console |
| `genh logs [dịch vụ] [-f]` | Log gọn, có màu theo mức |
| `genh update [--channel stable\|beta] [--yes] [--quiet] [--no-self-update]` | **v0.1.5:** tự hỏi bản `genh` mới nhất trên GitHub Releases (`/repos/<owner>/<repo>/releases/latest`), kiểm SHA-256, thay binary rồi RE-EXEC bằng code mới (`internal/selfupdate`), rồi mới nâng cấp dịch vụ theo thứ tự an toàn (xem mục "`genh update` — thứ tự an toàn" ngay dưới bảng). `--yes`: không hỏi gì; không kèm `--if-requested` ⇒ chỉ cài bản genh đã là bản chính thức ≥ 24 giờ (lịch đêm — xem "Cổng phát hành"). Không có TTY mà không `--yes`: cũng không hỏi gì (`RunUpdate` vốn không có bước hỏi) và **không** bị chặn 24 giờ. `--quiet`: chỉ in dòng quan trọng; "genh: cập nhật xong." CHỈ in khi phần nâng cấp dịch vụ thật sự chạy xong (bị hoãn thì dòng kết chứa "đợi đủ 24 giờ", không phải "cập nhật xong."). `--no-self-update`: chỉ nâng cấp dịch vụ, không đụng binary — **v0.1.34:** bỏ qua nếu dịch vụ đã khớp bản genh này (không sao lưu, không tải; in "không cần cập nhật"). **v0.1.33:** `/releases/latest` chỉ trả **bản chính thức** (đã qua E2E) **v0.1.53 (F-93, F-96):** (1) `genh update` còn **tự lành lịch tự cập nhật đêm** — bật lại khi unit mất/tắt/không chạy, trừ khi Sếp đã chủ động tắt (mục "Lịch tự cập nhật đêm tự lành và trung thực"); (2) lịch đêm (`--yes` không kèm `--if-requested`, thời gian chín 24 giờ) hỏi `GET /releases?per_page=10` và cài bản **semver cao nhất đã đủ 24 giờ** (không bị bản mới nhất chưa chín chặn "đói"); chưa bản nào đủ chín mà có bản mới hơn ⇒ hoãn; lỗi lấy danh sách ⇒ rơi về `/releases/latest`; (3) `MinAge=0` — `genh update` không `--yes`, nút Cập nhật ngay — vẫn chỉ `/releases/latest`, KHÔNG chờ 24 giờ |
| `genh auto-update enable\|disable\|status` | **v0.1.5:** bật/tắt/kiểm lịch tự chạy `genh update --yes --quiet` mỗi đêm ~03:00 giờ máy (`internal/autoupdate`) — systemd `--user` timer (fallback crontab) trên Linux, LaunchAgent trên macOS, Task Scheduler trên Windows. `genh install` tự bật mặc định (tắt bằng `--no-auto-update`). **v0.1.53 (F-93, F-94, F-95, F-98):** `enable` bật lịch (unit mang `--install-dir`/`--port` và `Environment=GEN_HARNESS_HOME`), kiểm lại **linger** bằng `loginctl show-user` và in cảnh báo nổi bật nếu vẫn tắt, rồi **xoá dấu "Sếp đã chủ động tắt"**; `disable` gỡ lịch và **ghi dấu** `config/auto-update-disabled.json` (từ đó genh không tự lành lại); `status` **nói thật** (có opt-out, không còn đoán từ crontab): dòng đầu BẬT / BẬT NHƯNG KHÔNG CHẠY / TẮT, rồi cơ chế · enabled/active · lần chạy gần nhất/kế tiếp · linger, và CẢNH BÁO khi log im quá 36 giờ. Lịch dùng chung giữa các bản cài: bản cài phụ không đổi/gỡ lịch của bản chính (`enable` báo lỗi) |
| `genh backup [--to path]` / `genh restore <file>` | Chạy trong container |
| `genh export --to <file>` / `genh import <file> [--yes]` | Gói hồ sơ Owner `.ghbundle` (CSDL + object + bí mật, mã hoá) — chuyển sang máy khác (v0.1.1 §1b/2b, `docs/reports/HANDOFF-v0.1.1.md`) |
| `genh doctor` | Chẩn đoán: runtime, cổng, chứng chỉ, dung lượng, đồng hồ, kết nối kênh — xuất báo cáo zip để gửi hỗ trợ — **v0.1.44 (F-4b): ĐÃ LỌC BÍ MẬT**, thêm log genh, tệp trạng thái, phiên bản/digest (xem mục "Gói chẩn đoán"); `--if-requested` làm yêu cầu từ Console. **v0.1.37 (F-73):** kiểm thêm "Tự chạy lại khi bật máy" (linger + Docker bật cùng máy) kèm lệnh sửa, ghi `run/autostart-status.json` như `genh status` |
| `genh reset-setup` | Sinh mã thiết lập mới (cần xác nhận) |
| `genh doctor --notify [--quiet] [--test]` · `genh watchdog enable\|disable\|status` | **v0.1.44 (F-6b) — Trực canh máy chủ** mỗi 12 phút: đo dịch vụ, tự khởi động lại dịch vụ chết, báo Telegram chống spam (xem mục "Trực canh máy chủ" dưới bảng) |
| `genh stop` / `genh start` | **v0.1.44:** `genh stop` ghi `config/paused-by-owner.json` ⇒ trực canh máy chủ tạm nghỉ (không tự khởi động lại, không báo động); `genh start` (và update/install thành công) xoá |
| `genh uninstall [--delete-data] [--yes]` | Gỡ container, lối tắt, PATH, lịch tự cập nhật/watcher/lịch bản sao ngoài máy/lịch trực canh (v0.1.44). **v0.1.53 (F-98):** lịch đang thuộc một bản cài KHÁC còn sống (vd gỡ bản phụ bằng `--install-dir`) thì GIỮ NGUYÊN, in "Giữ nguyên lịch … của bản cài … (không phải bản đang gỡ)". **v0.1.40 (F-12): mặc định GIỮ dữ liệu** (volume Docker còn nguyên — cài lại là thấy). `--delete-data` mới xoá dữ liệu: không có `--yes` thì phải gõ đúng `XOÁ DỮ LIỆU`; có `--yes` thì xoá luôn (CI). Không có bản sao ngoài máy thành công trong 7 ngày thì in cảnh báo đỏ "Chưa có bản sao ngoài máy gần đây". `--keep-data` vẫn nhận (không làm gì thêm — giữ tương thích script cũ). Không gỡ Docker/runtime |
| `genh offsite set [--allow-same-disk] [--no-run] <thư mục>` | **v0.1.40 (F-12) — Bản sao ngoài máy.** Chọn thư mục trên ổ USB/NAS đã mount: phải tuyệt đối, ĐÃ tồn tại (genh không bao giờ tạo đích gốc), ghi được, không nằm trong thư mục cài, và **khác ổ** với thư mục cài (Unix: thiết bị; Windows: tên ổ, UNC `\\NAS\share` coi là khác). Cùng ổ chỉ được khi Owner tự gõ `--allow-same-disk` (in cảnh báo "Bản sao nằm cùng ổ với máy chủ — hỏng ổ là mất cả hai"; Console không bao giờ đặt được). Lưu `<gốc cài>/config/offsite.json` (0600), bật lịch mỗi Chủ nhật ~05:30 rồi xuất bản đầu tiên ngay (trừ `--no-run`) |
| `genh offsite run [--quiet]` | Xuất `<đích>/gen-harness-offsite/gen-harness-YYYYMMDDTHHMMSSZ.ghbundle` (mật khẩu = Khoá khôi phục `secrets/gh_offsite_key`, chỉ qua biến môi trường), **tự kiểm gói** bằng `gh.bundle verify` (giải mã + `pg_restore --list`) — lỗi thì xoá tệp, coi như CHƯA có bản sao (GH-EB03). Giữ 4 gói mới nhất đúng mẫu tên. Đích chưa mount/USB rút ra (thư mục rỗng nằm lại trên ổ chính) ⇒ GH-EB01 "Chưa thấy ổ USB/NAS", không ghi gì. Ghi `run/offsite-status.json` cho Console |
| `genh offsite status` / `genh offsite disable` | In nơi lưu, bản sao gần nhất, lịch đang bật bằng cơ chế nào, mã nhận diện khoá (`key_id`) / tắt lịch (giữ cấu hình, đánh dấu tắt) |

### Tự cập nhật binary genh — thời gian rảnh và thử lại (v0.1.37, F-72)

Trước v0.1.37, tải `genh-<os>-<arch>` có hạn **20 giây cho cả tệp** — mạng chậm (tệp ~15 MB) thì lần nào cũng hết giờ và tự cập nhật hỏng âm thầm. Từ v0.1.37 (`internal/selfupdate`):

- Hạn tính theo **thời gian rảnh (idle)**: chỉ huỷ khi **60 giây liền không nhận thêm byte nào**; tải chậm nhưng đều vẫn xong.
- **Thử 3 lần**; mỗi lần thử lại log dòng cố định "thử lại lần %d/%d" (vd "thử lại lần 2/3").
- **Không thử lại** khi lỗi chắc chắn lặp lại: 404 (asset không có) và **sai checksum** (SHA-256 không khớp `checksums.txt`) — dừng ngay, giữ binary cũ.
- `install.sh` cũng thử tải 3 lần (asset + `checksums.txt`) trước khi báo lỗi.

### Tự chạy lại khi bật máy (v0.1.37, F-73)

Máy chủ tắt/bật lại mà Gen-Harness không tự lên là lỗi Owner chỉ thấy khi đã muộn. `genh status` và `genh doctor` kiểm hai điều kiện và in dòng **"Tự chạy lại khi bật máy"**:

- **Linux:** (1) **linger** của người dùng chạy genh (`loginctl show-user <user> -p Linger`) — cần khi Docker chạy rootless hoặc lịch đêm là systemd `--user` timer; (2) **Docker bật cùng máy** — Docker hệ thống: `systemctl is-enabled docker.service`; Docker rootless: `systemctl --user is-enabled docker.service`. Thiếu ⇒ in **lệnh sửa** cố định (cùng chữ ở genh, API và Console): `sudo systemctl enable docker` (hoặc `systemctl --user enable docker` với rootless) / `sudo loginctl enable-linger $USER` (Docker rootless cũng cần linger để tự lên khi bật máy). **v0.1.53 (F-95):** sau `loginctl enable-linger` genh hỏi lại `loginctl show-user` thay vì tin mã thoát và cảnh báo nổi bật nếu linger vẫn tắt; `linger_required` tính cả timer trực canh 12 phút và bản sao ngoài máy (mục "Lịch tự cập nhật đêm tự lành và trung thực").
- **Linux, `docker info` lỗi/quá hạn** (`docker_mode` = `unknown`): chỉ tin `docker.service` hệ thống khi nó **enabled** (⇒ `yes`); `disabled` ⇒ `unknown` (máy chỉ dùng Docker rootless thường tắt `docker.service` hệ thống — không khuyên bật Docker rootful, không bật cảnh báo).
- **macOS/Windows:** `linger` = `not_applicable`. Docker chỉ coi là tự lo (`docker_enabled` = `not_applicable`, `docker_mode` = `desktop`) khi phát hiện **Docker Desktop** (`docker info` → OperatingSystem "Docker Desktop" — Desktop có "Start when you sign in"). Runtime genh tự cài (**Colima/Lima** trên macOS, distro **WSL** trên Windows) không có gì tự khởi động VM khi bật máy, và khi không đọc được Docker ⇒ `docker_enabled` = `unknown`, `docker_mode` = `unknown` (Console hiện "Chưa rõ", không báo sai "Có"; `genh status` gợi ý chạy `genh start` sau khi bật lại máy).

Kết quả ghi vào `run/autostart-status.json` (bảng "Tệp trạng thái trong `run/`" bên dưới) — **không chứa lệnh hay bí mật**; API tự ghép lệnh sửa từ chuỗi cố định và Console hiện cảnh báo **`host.autostart`** ("Máy chủ có thể không tự chạy lại Gen-Harness khi bật lại máy" + lệnh sửa + câu cuối "Chạy xong thì chạy genh status để cảnh báo tự hết" — không hứa "đợi tới đêm": thiếu linger thì lịch đêm không chạy, tắt tự cập nhật thì không có lần chạy đêm nào) khi `linger_required` mà `linger` = `no`, hoặc `docker_enabled` = `no`. Dòng trên dải "Cần Sếp xử lý" và chuông đều dẫn tới thẻ **"Sức khoẻ hệ thống"** (Dữ liệu & lưu trữ): dòng **"Tự chạy lại khi bật máy"** (Có / Chưa bật / Chưa rõ, kèm giờ kiểm) và hướng dẫn từng bước với lệnh dạng mã chép được — vai trò chỉ có `system.read` (Auditor) cũng thấy thẻ này. **Cảnh báo chỉ hết khi genh ghi lại tệp** — tức khi chạy `genh status`/`genh doctor`, hoặc lần `genh update`/`genh install` kế tiếp (lịch đêm) — chạy xong lệnh sửa thì chạy `genh status` để cảnh báo hết ngay. Giá trị `unknown` (không kiểm được, vd không có systemd) ⇒ không bật cảnh báo; thẻ Sức khoẻ hiện "Chưa rõ" (cả khi tệp chưa được ghi). Thẻ chỉ hiện "Có" khi Docker chắc chắn tự chạy (`docker_enabled` = `yes`/`not_applicable`) **và** linger ổn (`yes`/`not_applicable` hoặc không cần) — linger `yes` một mình không đủ.

### Lịch tự cập nhật đêm tự lành và trung thực (v0.1.53, F-93…F-100)

**Vì sao.** Một bản cài đứng ở v0.1.44 từ đêm 03/10 đến 09/10: lịch tự cập nhật đêm (`gen-harness-update.timer`/`.service`) bị tắt mà không có gì chữa lại, `genh auto-update status` in sai TẮT, Console chỉ nói "chưa nhận yêu cầu". Điều tra (chi tiết và bằng chứng ở [v0.1.53.md](../releases/v0.1.53.md)):
H-a tái hiện được (bản cài phụ gỡ/ghi đè lịch dùng chung), H-c tái hiện được (status đọc sai), H-b chưa tái hiện được bằng mã. Vì chưa chỉ ra nguyên nhân gốc duy nhất nên lịch đêm được làm **tự lành** và **nói thật** thay vì vá một nguyên nhân.
Thuật ngữ thống nhất ở genh, API, Console, tài liệu: **lịch tự cập nhật đêm** (timer ~03:00, `gen-harness-update.timer`/`.service`) · **trình nhận yêu cầu** (`gen-harness-update-request.path`/`.service` — nút Cập nhật ngay, Khôi phục…) · **linger** · **thời gian chín 24 giờ** · **Sếp đã chủ động tắt** · **tự lành**.

- **Tự lành (F-93)** — `autoupdate.EnsureNightly` chạy trong `publishHostInfo` (mọi `genh install`/`genh update`, kể cả lần chạy của chính lịch đêm), TRƯỚC khi ghi `run/genh.json` và lần nữa sau khi làm mới các lịch khác. Unit vắng, hoặc `UnitFileState` ≠ `enabled`, hoặc `ActiveState` ≠ `active` ⇒ ghi lại unit và `systemctl --user enable --now gen-harness-update.timer`, log "genh: lịch tự cập nhật đêm đã bị tắt/mất — đã bật lại (~03:00). Muốn tắt hẳn: genh auto-update disable".
  Timer `active` mà không có lần kế tiếp (và service đêm KHÔNG đang chạy — khi service đang chạy, timer "running" với `NextElapse` rỗng là bình thường) ⇒ `restart gen-harness-update.timer`. Khoẻ thì **không gọi lệnh ghi nào**; chạy lần hai không ghi gì (idempotent). crontab/LaunchAgent/Task Scheduler: còn dòng/tệp/Task là khoẻ.
  **Không tự lành khi Sếp đã chủ động tắt** (dấu bên dưới). Máy chưa có dấu mà lịch đang tắt (đúng ca đã gặp) sẽ được bật lại.
- **Dấu "Sếp đã chủ động tắt"** — `<gốc cài>/config/auto-update-disabled.json` (0600, `{"at": RFC3339}`; khuôn `watchdog-disabled.json`; trong `config/` chỉ genh ghi, **không** trong `run/` vì api ghi được `run/`). Ghi bởi `genh auto-update disable` và `genh install --no-auto-update`; xoá bởi `genh auto-update enable` và `genh install` không cờ. Ghi lặp lại giữ nguyên thời điểm tắt lần đầu.
  `install --no-auto-update` còn **tắt lịch đêm đang có của chính bản cài đó** (vd `--force` đè lên máy đã bật lịch; chưa có lịch nào thì không gọi lệnh ghi nào; lịch của bản cài khác còn sống thì không đụng). `disable` **hỏi lại** trạng thái sau khi tắt (lệnh tắt là best-effort): lịch vẫn bật ⇒ in "CHƯA tắt được …", thoát 1, `genh.json` ghi lịch còn bật.
  Có dấu ⇒ tự lành bỏ qua, `status` ghi "TẮT (Sếp đã chủ động tắt — bật lại: genh auto-update enable)", API/Console ghi `opted_out` và KHÔNG nhắc — trừ khi lịch **vẫn bật** (tắt hụt, bật tay lại): Console nói thật "Sếp đã tắt nhưng lịch vẫn bật" kèm hai lựa chọn.
- **`genh auto-update status` nói thật (F-94)** — hỏi `systemctl --user show` (đọc cả khi lệnh thoát ≠ 0; dự phòng `is-enabled`/`is-active`); chỉ xét crontab khi KHÔNG có unit (bản cũ coi `is-enabled` thoát ≠ 0 là "không có unit" rồi in "crontab: không có dòng tự cập nhật" dù stdout là `disabled`). In đủ 5 thông tin:
  (1) dòng đầu **BẬT** / **BẬT NHƯNG KHÔNG CHẠY** / **TẮT** / **TẮT (Sếp đã chủ động tắt …)** / TẮT khi lịch đang thuộc bản cài khác; (2) cơ chế (`systemd --user timer (gen-harness-update.timer)`, crontab, LaunchAgent, Task Scheduler hoặc "chưa có"); (3) "Đã bật (enabled)" và "Lịch đang chạy (active)"; (4) lần chạy gần nhất / lần kế tiếp (giờ máy); (5) linger.
  Thêm **CẢNH BÁO** khi `logs/auto-update.log` không có dòng mới hơn **36 giờ** (`nightlyStaleAfter`, cùng `NIGHTLY_STALE_HOURS` của Console): "lịch đêm có thể không chạy. Bật lại: genh auto-update enable". Lệnh sửa cố định: `genh auto-update status` (xem), `genh auto-update enable` (bật lại), `genh auto-update disable` (Sếp tắt hẳn), `genh update` (đồng bộ + tự lành).
  Lưu ý khi tìm timer bằng tay: tên unit là `gen-harness-*` nên `grep genh` KHÔNG khớp — dùng `systemctl --user list-timers --all | grep gen-harness`.
- **Linger (F-95)** — *linger* = tiến trình nền của người dùng (timer `systemd --user`, trình nhận yêu cầu) chạy cả khi không ai đăng nhập; thiếu linger thì lịch đêm và nút Cập nhật ngay chỉ chạy lúc có người đăng nhập. `genh auto-update enable` (và tự lành) chạy `loginctl enable-linger` rồi **hỏi lại** `loginctl show-user <uid> -p Linger --value` thay vì tin mã thoát (enable-linger có thể thoát 0 mà linger vẫn tắt, hoặc thoát ≠ 0 vì đã bật sẵn).
  Linger vẫn `no` (hoặc không hỏi được mà enable-linger cũng lỗi) ⇒ in **cảnh báo nổi bật** "CẢNH BÁO: linger đang TẮT — lịch tự cập nhật đêm và nút Cập nhật ngay chỉ chạy khi có người đăng nhập máy. Chạy một lần: sudo loginctl enable-linger $USER" — đỏ đậm khi có TTY, chữ thường "cảnh báo:" khi vào `logs/auto-update.log`.
  Mọi lối thoát của `genh update` và `genh auto-update enable` ghi lại `run/autostart-status.json`; `linger_required` nay tính cả timer **trực canh 12 phút** và **bản sao ngoài máy** (không chỉ lịch đêm/trình nhận yêu cầu/Docker rootless).
- **Unit systemd (F-98)** — trước đây `ExecStart` chỉ là `genh update --yes --quiet`, nên bản cài dùng `--port`/`--install-dir`/`GENH_COMPOSE_FILE` khác mặc định bị cập nhật sai chỗ. Nay `gen-harness-update.service` ghi `Environment=GEN_HARNESS_HOME=<gốc cài>` (cộng `GENH_COMPOSE_FILE` nếu phiên cài có) và
  `ExecStart=<genh> update --yes --quiet --install-dir <gốc cài> [--port N]` (`--port` chỉ khi khác 8443; GIỮ tiền tố `update --yes --quiet` vì cổng 24 giờ dựa vào `--yes` và E2E grep đúng chuỗi này); crontab, LaunchAgent, Task Scheduler mang cùng đối số/biến.
  **`RefreshUnits`** (mỗi `genh update`) sửa `ExecStart` và thêm `Environment=GEN_HARNESS_HOME` vào unit ĐÃ CÀI chỉ khi `ExecStart` trỏ **cùng binary genh** hoặc binary đó **không còn tồn tại**; binary khác còn tồn tại (Sếp cố ý chạy từ đường dẫn khác) ⇒ giữ nguyên; dòng `Environment=` có biến khác của Sếp thì không đụng; unit thuộc bản cài khác còn sống ⇒ không đụng. Sau khi sửa: `systemctl --user daemon-reload`.
  `gen-harness-update-request.service` có `StartLimitIntervalSec=300` + `StartLimitBurst=5`; `gen-harness-update-request.path` có `TriggerLimitIntervalSec=60` + `TriggerLimitBurst=10` — tệp yêu cầu không xoá được không kích lặp vô hạn. Trình nhận yêu cầu đã rơi vào start-limit (`failed`) thì `genh update` chạy `systemctl --user reset-failed gen-harness-update-request.path gen-harness-update-request.service` TRƯỚC `enable --now` để chữa.
- **Lịch dùng chung giữa các bản cài (F-98, ứng viên H-a)** — tên unit `gen-harness-update.*`, `gen-harness-update-request.*`, `gen-harness-watchdog.*`, `gen-harness-offsite.*`, Label LaunchAgent và marker crontab là CHUNG cho mọi bản cài của một người dùng. Trước v0.1.53 `genh uninstall --install-dir <bản phụ>` gỡ luôn lịch đêm + trình nhận yêu cầu của bản chính (xoá unit, `disable --now`), và `genh install|update --install-dir <bản phụ>` ghi đè `.path` sang hộp thư của bản phụ.
  Nay mỗi lịch ghi rõ **bản cài chủ** (`Environment=GEN_HARNESS_HOME=` hoặc `--install-dir` trong `ExecStart`; Task Scheduler: `--install-dir` trong lệnh của Task; lịch của bản genh cũ không ghi gì ⇒ coi là thư mục cài mặc định **theo HOME** — `~/.gen-harness`, Windows `%LOCALAPPDATA%\GenHarness` — KHÔNG đọc `GEN_HARNESS_HOME` của người gọi, vì lịch cũ chạy ngoài phiên shell nên luôn làm việc trên gốc đó) và bản cài chủ "còn sống" khi còn `<gốc>/config/secrets.json`. Bản cài **phụ** KHÔNG gỡ, KHÔNG ghi đè lịch đêm, trình nhận yêu cầu, trực canh, lịch bản sao ngoài máy của bản chủ còn sống:
  `genh uninstall` in "Giữ nguyên lịch … của bản cài … (không phải bản đang gỡ)"; `install`/`update`/`auto-update enable` in một dòng cảnh báo ("Máy này có bản cài khác đang giữ lịch đêm/nút Cập nhật ngay (…) — bản cài … không đổi lịch") và trả lỗi ở `enable`. Bản chủ đã gỡ (không còn `config/secrets.json`) thì bản khác nhận lịch được. Test hồi quy: `TestRegressionUninstallOtherInstallKeepsNightly`, `TestRegressionUpdateOtherInstallKeepsWatcher`.
- **Hộp thư yêu cầu không nuốt lỗi xoá (F-97)** — `hostlink.ConsumeRequest` trả `(consumed, err)`: không xoá được tệp trong `run/request` (quyền thư mục…) ⇒ genh **không làm yêu cầu** (làm tiếp sẽ khiến `.path` kích lặp), ghi trạng thái `failed` mã **GH-E94C** (idempotent theo `requested_at`: cùng yêu cầu không báo lại), thoát 0. Áp cho cập nhật, khôi phục, bản sao ngoài máy (`RunOffsiteRequest` dừng TRƯỚC set/run/disable, trả lỗi bọc `ErrRequestUndeletable`), gói chẩn đoán và Gửi thử trực canh. Lỗi gốc (có đường dẫn) chỉ vào stderr/log, không vào thông điệp Console; Console có câu riêng cho GH-E94C ở mọi thẻ (kiểm quyền `run/request`, không bảo thử lại/cắm ổ).
  Nhánh thoát sớm khi chờ khoá loại trừ quá 30 phút (trước đây để tệp nằm lại) nay xoá tệp + `failed` **GH-E94A**.
- **Trạng thái cho Console (F-99, phía genh)** — `run/nightly-status.json` (bảng "Tệp trạng thái trong `run/`") do `publishHostInfo`, lần chạy lịch đêm (đầu: `last_run_at`; cuối: `last_result` — chỉ khi stdout KHÔNG phải terminal: mọi lịch đêm ghi stdout vào `logs/auto-update.log`, còn `genh update --yes` gõ tay trong terminal không tính là lần chạy của lịch) và mỗi lượt trực canh 12 phút ghi; `owned_by_other` = lịch dùng chung thuộc bản cài khác còn sống (Console báo xám, không cảnh báo); thêm vào gói chẩn đoán (`host/nightly-status.json`).
- **API và Console (F-99)** — `GET /system/update` thêm `nightly_candidates` `[{tag, eligible_at}]` và `nightly` (`mechanism`, `enabled`, `active`, `opted_out`, `last_run_at`, `next_run_at`; `null` khi chưa có tệp); `GET /system/health` thêm khối `nightly` (`state` = `ok`|`warn`|`off`|`unknown`, `days_since`, `linger`…); sự cố **`host.nightly`** (nút "Xem cách bật lại" → thẻ Sức khoẻ hệ thống, hướng dẫn "Cách bật lại lịch tự cập nhật đêm") khi lịch đang tắt (không do Sếp tắt) hoặc im quá **`NIGHTLY_STALE_HOURS` = 36** kể từ mốc = max(lần chạy cuối, lúc bật) —
  "Lịch tự cập nhật đêm đang tắt" / "…đã hơn 1 ngày chưa chạy" / "…chưa chạy N ngày". Thân sự cố ghép từ chuỗi cố định (không lấy chữ từ tệp): "Máy chủ không tự lên bản mới. Trên máy chủ chạy: `genh auto-update status`", thêm `sudo loginctl enable-linger $USER` khi linger `no`, rồi `genh auto-update enable` (cố định, ghép từ chuỗi có sẵn); `stalled_reason` mới: xem mục "API — trạng thái 'stalled'" bên dưới.
- **E2E (F-100)** — job `e2e-nightly-real` chạy THẬT timer và `.path` dưới user manager có linger; xem "Cổng phát hành" mục 8.

### `genh update` — thứ tự an toàn (v0.1.34, F-10/F-11/F-33)

Trước khi đụng dịch vụ, `cmd/genh` (`decideServiceUpdate`) quyết định có chạy `ops.RunUpdate` không:

- **Bản bị chặn tự cập nhật** — lịch đêm (`--yes` không kèm `--if-requested`) mà `run/update-blocked.json` ghi **đúng** bản genh đang chạy (so khớp chính xác chuỗi version) ⇒ bỏ qua, thoát 0, log: "… lịch đêm không tự thử lại bản này …" (nếu `rollback_failed` thì log nói rõ "tự quay về bản cũ CŨNG THẤT BẠI — cần xử lý tay" + bản sao lưu cần khôi phục khi CSDL đã bị đụng, hoặc chỉ `docker compose up -d --remove-orphans` — KHÔNG khôi phục — khi CSDL chưa bị đụng). Lịch đêm chạy trùng lúc Owner vừa bấm "Cập nhật ngay" (đã nuốt yêu cầu) thì chạy như `--if-requested`, không bị chặn. `run/update-status.json` được trả về như lần lỗi để lại (parse rồi ghi lại đúng các trường genh ghi — không chép nguyên byte; tệp không phải tệp thường/symlink/quá 64 KiB thì bỏ) (không làm mới `finished_at` — thẻ đỏ Console tự hết sau 24 giờ; không ghi đè thông điệp gốc). Có bản genh mới hơn thì vẫn tự cài. Nút **Cập nhật ngay** và gõ tay `genh update` **không** bị chặn.
- **Đã mới nhất** — không vừa tự cập nhật binary, `compose.yaml` **genh quản lý** + `proxy/Caddyfile` đã trùng bản nhúng **và** không còn `run/update-inprogress.json` (`ops.UpdateNeeded` → `compose.InSyncWithEmbedded`) ⇒ không sao lưu, không tải ảnh, log: "… không cần cập nhật …", thoát 0. Compose ngoài (`GENH_COMPOSE_FILE`, checkout repo) **không bao giờ** coi là đã khớp — luôn chạy đủ (genh không biết tệp ngoài đã dựng lên chưa).

`ops.RunUpdate` chạy theo thứ tự:

1. **Kiểm đĩa** — đo chỗ trống ở gốc cài đặt và (Docker gốc trên Linux) `docker info --format {{.DockerRootDir}}`, lấy số nhỏ hơn. Dưới 5 GB (`machine.MinDiskBytes`) ⇒ **dọn ảnh cũ** rồi đo lại; vẫn thiếu ⇒ **GH-E948**, dừng, chưa đụng gì. Đo lỗi ⇒ chỉ cảnh báo. Mỗi lần đo ghi `run/disk-status.json`.
2. **Tải bản mới** TRƯỚC sao lưu — nếu compose sẽ đổi, tải bằng `deploy/compose.update-next.yaml` (bản nhúng, cùng thư mục, xoá ngay sau đó); `compose.yaml` thật chưa đổi. Thử 3 lần, mỗi lần tối đa 20 phút, chờ 20 giây rồi 60 giây giữa các lần. Hết lần ⇒ **GH-E941 "chưa đụng gì"**: không sao lưu, không khôi phục, không `up`; lịch đêm tự thử lại đêm sau.
3. **Dò thay đổi CSDL** — `run --rm --no-deps -T migrate alembic current` bằng ảnh mới; còn revision không phải `(head)`, hoặc tập revision hiện tại khác tập `alembic heads` của ảnh mới (bản mới thêm head riêng), hoặc `alembic current` lỗi — coi như có ⇒ có **migration chờ** ⇒ **tạm dừng worker và bridge** (nguồn ghi) để bản sao lưu không lỡ ghi chép. api **cố ý không dừng** (Console phải chạy để Owner thấy tiến trình): khoảng hở chấp nhận là ghi của api trong vài giây giữa lúc sao lưu xong và lúc migrate — chỉ mất nếu phải khôi phục bản sao lưu đó.
4. **Sao lưu** (`pre-update`). Lỗi ⇒ bật lại worker/bridge, **GH-E940**, chưa đụng gì.
5. Ghi **`run/update-inprogress.json`** rồi **đồng bộ compose.yaml** với bản nhúng (giữ `compose.yaml.bak`), rồi di trú `/tmp/gh-objects` của bản cài cũ nếu có. Lỗi ⇒ trả `compose.yaml` về bản cũ (từ bộ nhớ) + `up -d --remove-orphans`, **không khôi phục CSDL** (chưa bị đụng), giữ mã lỗi gốc (GH-E947/GH-E946).
6. **Migrate** → `up -d --remove-orphans` → chờ `/api/v1/ready` (sẵn sàng ⇒ xoá `update-inprogress.json`). Từ lúc bắt đầu migrate, lỗi nghĩa là **bản này hỏng** (chặn lịch đêm); CSDL chỉ coi là **đã bị đụng** khi bước 3 thấy có migration chờ.
7. **Thành công** ⇒ xoá `run/update-blocked.json`, **dọn ảnh cũ** (giữ ảnh bản hiện tại + bản liền trước), tin cậy lại CA.

**Dấu cập nhật dở (`run/update-inprogress.json`):** genh bị tắt / máy khởi động lại / Ctrl-C sau khi đã ghi compose.yaml mới mà chưa `up -d` xong ⇒ còn dấu ⇒ lần sau (đêm, "Cập nhật ngay", gõ tay) KHÔNG coi "đã khớp", chạy lại đủ. Xoá khi sẵn sàng, hoặc khi đã trả compose.yaml về bản cũ và khởi động lại được.

**Khoá loại trừ và tín hiệu dừng (v0.1.37, F-34):**

- **Khoá loại trừ** — `genh update`, `genh restore`, `genh import` và watcher nhận yêu cầu từ Console giữ `flock(2)` độc quyền trên **`<gốc cài đặt>/genh.lock`** (lấy ở **tiến trình genh ngoài cùng**; tiến trình con re-exec `--self-updated` kế thừa, không lấy lại). **Không** đặt trong `run/` như kế hoạch ban đầu (`run/genh.lock`): `run/` để 0777 và bind-mount vào container api — api (hoặc kẻ chiếm được api) có thể xoá/thay tệp khoá (hai genh cùng chạy trên một máy) hoặc giữ flock mãi (chặn mọi bản cập nhật, kể cả bản vá bảo mật). Nhịp sống (heartbeat) vẫn ghi ở `run/` để Console đọc.
- **Khoá đang bị giữ:** lịch đêm (`--yes --quiet`) ⇒ bỏ qua, **thoát 0**, log "đang có một lần cập nhật/khôi phục khác chạy" (đêm sau thử lại); nút **Cập nhật ngay**/**Khôi phục** trên Console ⇒ watcher **chờ khoá tối đa 30 phút** rồi mới làm (**v0.1.53:** hết 30 phút mà khoá vẫn bận ⇒ xoá tệp yêu cầu và ghi `failed` **GH-E94A** — không để tệp nằm lại cho `.path` kích lặp) — chỉ **một** tiến trình chờ cùng lúc (khoá người chờ `<gốc cài>/genh-wait.lock`, không chờ được ⇒ thoát 0 ngay, log "đã có một tiến trình khác đang chờ"; watcher crontab kích mỗi phút không chồng ~30 genh). Trong lúc chờ, yêu cầu nằm quá 15 phút mà nhịp sống `run/genh-heartbeat.json` của lần đang chạy còn tươi ⇒ API vẫn trả `requested` + `host_busy: true` (Console: "Máy chủ đang chạy một lần cập nhật/khôi phục khác — sẽ làm yêu cầu này ngay khi lần đó xong"), **không** phải "stalled"/`not_picked_up`; **gõ tay** ⇒ in cùng dòng đó kèm **GH-E94A**, không chờ. Không đụng `run/update-status.json` (Console vẫn hiện đúng lần đang chạy).
- **Tín hiệu dừng** — SIGINT/SIGTERM (Ctrl-C, `systemctl stop`, tắt máy) giữa chừng ⇒ ngữ cảnh chính bị huỷ, nhưng **quay về bản cũ (rollback) chạy trên ngữ cảnh không huỷ được** (`context.WithoutCancel`) — chỉ các **bước nhẹ** (`up -d`, `stop`, dọn ảnh) có **hạn riêng 10 phút** khi bị dừng; **khôi phục CSDL (pg_restore) và chép lại dữ liệu di trú không bao giờ có hạn**, kể cả sau Ctrl-C (Owner đang ngồi trước máy, không có SIGKILL sắp tới — cắt pg_restore giữa chừng sau `DROP DATABASE` là để CSDL trống/dở, container `run --rm` còn có thể restore ngầm). `update-status.json` = failed với mã **GH-E94B**. Quay về **ổn** ⇒ không ghi `update-blocked.json` (bản mới chưa chắc hỏng — lịch đêm được thử lại). Quay về **chưa trọn** (vd pg_restore bị cắt sau `DROP DATABASE`) ⇒ **vẫn** ghi `update-blocked.json` (`rollback_failed: true` + `backup_key` nếu đã đụng CSDL) — lịch đêm không được chạy `genh update` đè lên CSDL trống/dở (nó sẽ sao lưu chính CSDL hỏng đó rồi migrate). Tiến trình ngoài chuyển tiếp **đúng loại** tín hiệu cho tiến trình con sau tự cập nhật; tín hiệu tới trước khi kịp chạy con ⇒ không chạy con.
- **SIGTERM (máy tắt/khởi động lại) sau khi đã đụng CSDL** ⇒ genh **không khôi phục** CSDL và **không trả `compose.yaml` về bản cũ**: CSDL đã migrate (và container bản mới nếu đã lên) chỉ khớp `compose.yaml` mới — để compose cũ thì một lần `docker compose up -d` bất kỳ sẽ dựng ảnh cũ trên CSDL mới. **Không ghi** `update-blocked.json` (khuyên khôi phục bản sao lưu trước cập nhật sẽ xoá mọi dữ liệu ghi từ lúc bật lại máy); **giữ** `update-inprogress.json` để lần chạy sau coi "chưa khớp". Hướng dẫn (GH-E94B, "CSDL đã sang bản mới, cần chạy tiếp"): sau khi bật lại máy chạy `genh update` để **đi tiếp** lên bản mới (genh tự sao lưu lại CSDL hiện tại trước) — lịch đêm (nếu bật) cũng tự làm; Console hiện "dữ liệu đã chuyển sang bản mới, cần chạy lại để hoàn tất" + Thử lại. Lý do không khôi phục: lúc tắt máy không đủ thời gian (dòng dưới) và Docker có thể đang dừng — `DROP DATABASE` + pg_restore bị cắt giữa chừng tệ hơn để nguyên CSDL đã migrate (dữ liệu vẫn đủ). Ctrl-C (SIGINT) ⇒ khôi phục như thường (không hạn).
- **Tín hiệu dừng ngay sau khi tự tải genh mới** (chưa kịp chạy tiến trình con) ⇒ chưa đụng gì tới dịch vụ: `update-status.json` = failed với thông điệp GH-E94B "chưa đụng gì" (Console: thẻ vàng "dừng giữa chừng — chưa đụng gì", không phải thẻ lỗi đỏ).
- **Unit systemd** của lịch đêm/watcher có `KillMode=mixed` (SIGTERM chỉ tới genh, Docker con không bị giết ngang) và `TimeoutStopSec=900`. **Thật thà:** `TimeoutStopSec=900` chỉ có tác dụng khi dừng unit lúc máy vẫn chạy (`systemctl --user stop`). **Lúc tắt máy giới hạn thật là ~120 giây**: `user@.service` (TimeoutStopSec=120s) SIGKILL cả user manager cùng cgroup, và `docker.service` (unit hệ thống, không xếp thứ tự với `user@`) có thể đang dừng song song — vì vậy genh không khôi phục CSDL khi nhận SIGTERM (dòng trên). `genh update`/`install` chỉ **thêm dòng còn thiếu** (`KillMode=mixed`, `TimeoutStopSec=900`) vào unit lịch đêm đã cài — giữ nguyên mọi dòng Owner sửa tay — và in một dòng khi có thêm. **Từ v0.1.53 (F-98)** `RefreshUnits` còn thay `ExecStart` và thêm `Environment=GEN_HARNESS_HOME` khi unit trỏ cùng binary (hoặc binary cũ đã mất); unit đêm có `--install-dir`/`--port`, trình nhận yêu cầu có `StartLimit*`/`TriggerLimit*`, `genh update` chạy `reset-failed` trước `enable --now` — chi tiết ở mục "Lịch tự cập nhật đêm tự lành và trung thực".
- **Nhịp sống** — tiến trình giữ khoá ghi `run/genh-heartbeat.json` mỗi 30 giây, xoá khi nhả khoá; `update-status.json` lúc Start thêm `pid` (PID genh ngoài cùng) và `boot_id`.

**Khi nào khôi phục CSDL:** chỉ khi lỗi xảy ra ở bước 6 (migrate/up/ready lỗi) **và có migration chờ**. Quay về bản cũ: ghi lại `compose.yaml` cũ (từ bộ nhớ — không dùng `.bak`, vì `.bak` có thể cũ từ lần trước), dừng api/worker/bridge/web, dựng lại db bằng ảnh **cũ** (`up -d --wait --no-deps db`, tối đa 3 phút — để bản sao lưu được khôi phục bởi đúng ảnh db sẽ chạy tiếp), khôi phục bản sao lưu bằng **container tạm từ ảnh cũ** (`run --rm --no-deps -T api python -m gh.backup restore --key …` — không `exec` vào api ảnh mới đang lỗi), `up -d --remove-orphans`, rồi ghi `run/update-blocked.json` (kể cả khi quay về bản cũ thất bại — kèm `rollback_failed: true`) ⇒ **GH-E945**, thoát 1, log có "rollback". **Không có migration chờ** (worker/bridge/api vẫn ghi suốt từ lúc sao lưu): KHÔNG khôi phục (sẽ mất các ghi đó) — chỉ trả `compose.yaml` cũ + `up -d --remove-orphans`, vẫn ghi `update-blocked.json` ⇒ **GH-E945**.

**Hộp thư Console khi lỗi:** `update-status.json` `message` = `<việc> — <cách xử lý> (GH-E9xx)` (ổ đĩa đầy kèm số GB còn trống; bỏ dấu `` ` ``). Console chọn lời dẫn theo mã: GH-E948 "Ổ đĩa máy chủ sắp đầy — chưa đụng gì" (Owner phải dọn đĩa), GH-E941/GH-E940/GH-E900/GH-E901 "Chưa đụng gì", quay về thất bại "Cần xử lý tay" (ưu tiên trường `blocked_rollback_failed`), GH-E946 sau khi cập nhật xong "Bản mới đã chạy — còn bước chép dữ liệu cũ", "đã tự quay về bản đang dùng" chỉ cho GH-E945/E949/E946/E947 khi thông điệp nói đã quay về, còn lại "Cập nhật chưa xong — xem Chi tiết kỹ thuật"; nguyên văn trong "Chi tiết kỹ thuật". Máy chủ chưa nhận yêu cầu từ nút bấm (`can_request=false`) ⇒ thẻ lỗi hiện lệnh chạy tay thay cho "bấm Thử lại". `GET /system/update` có thêm `blocked_version` và `blocked_rollback_failed` (đọc `update-blocked.json`) — Console không hứa "Tự cài đêm" cho bản đang bị chặn.

**Dọn ảnh cũ:** chỉ repo `ghcr.io/<owner>/gen-harness-*` **có mặt trong các compose được giữ** (không đụng ảnh gen-harness của owner khác dùng chung Docker); giữ mọi ảnh có trong compose hiện tại/bản đích và compose cũ/`.bak`; `docker rmi` từng ảnh (không `-f`, không `docker image prune`); ảnh đang dùng thì bỏ qua. Compose không có ảnh gen-harness (dev/build cục bộ) ⇒ không dọn gì.

**Tệp trạng thái trong `run/`** (không chứa bí mật; API coi mọi giá trị đọc từ `run/` là không tin cậy):

| Tệp | Nội dung | Ghi / xoá |
|---|---|---|
| `update-blocked.json` | `{version, blocked_at, code, backup_key?, db_touched, message, rollback_failed?}` | genh ghi khi bản mới lỗi từ bước migrate trở đi (đã quay về bản cũ, hoặc quay về thất bại ⇒ `rollback_failed: true`); `backup_key` chỉ có khi CSDL đã bị đụng (`db_touched`); genh chỉ tin tệp thường của chính uid chạy genh; xoá khi cập nhật thành công |
| `update-inprogress.json` | `{version, backup_key, started_at}` | genh ghi ngay trước khi đổi compose.yaml; xoá khi sẵn sàng / đã trả compose.yaml về bản cũ |
| `disk-status.json` | `{state: ok\|low, free_bytes, min_bytes, path, pruned_images, checked_at}` | genh ghi mỗi lần `genh update` kiểm đĩa (chuông "đĩa sắp đầy" v0.1.36 đọc — giữ tên khoá) |
| `update-status.json` | `{state, started_at, finished_at?, message?, …}` — **v0.1.37:** thêm `pid` (int, PID tiến trình genh **ngoài cùng**) và `boot_id` (`/proc/sys/kernel/random/boot_id`, `""` nếu không phải Linux) | genh ghi lúc Start (kèm `pid`/`boot_id`) và lúc Finish (giữ nguyên như trước) |
| `genh-heartbeat.json` | `{pid, op: update\|restore\|import, boot_id, started_at, at}` (RFC3339) | **v0.1.37:** tiến trình giữ khoá loại trừ ghi mỗi 30 giây; xoá khi nhả khoá |
| `autostart-status.json` | `{os: linux\|darwin\|windows, linger: yes\|no\|unknown\|not_applicable, linger_required: bool, docker_enabled: yes\|no\|unknown\|not_applicable, docker_mode: system\|rootless\|desktop\|unknown, checked_at}` | **v0.1.37:** `genh status`/`genh doctor` ghi mỗi lần chạy, `genh update`/`genh install` ghi khi xong (`publishHostInfo` — kể cả lịch đêm); không chứa lệnh — API tự ghép lệnh sửa. Cảnh báo `host.autostart` chỉ hết khi tệp được ghi lại. **v0.1.53 (F-95):** mọi lối thoát của `genh update`/`auto-update enable` ghi tệp; `linger_required` tính cả timer trực canh và bản sao ngoài máy |
| `nightly-status.json` | `{schema: 1, mechanism: systemd\|cron\|launchd\|schtasks\|"", enabled: bool, active: bool\|null, unit_present: bool, opted_out: bool, owned_by_other: bool, since: RFC3339\|"", last_run_at: RFC3339\|"", last_result: done\|failed\|deferred\|up_to_date\|blocked\|"", next_run_at: RFC3339\|"", linger: yes\|no\|unknown\|not_applicable, request_watcher: active\|failed\|inactive\|unknown, checked_at: RFC3339}` | **v0.1.53 (F-99):** genh ghi nguyên tử (0644) ở `publishHostInfo` (install/update), đầu và cuối mỗi lần lịch đêm chạy, và mỗi lượt trực canh 12 phút (làm mới trạng thái; `since`, `last_run_at`, `last_result` được giữ qua các lần ghi). Không chứa lệnh hay bí mật; api chỉ nhận đúng kiểu/tập giá trị (ngoài tập ⇒ `unknown`) và tự ghép lệnh sửa từ chuỗi cố định. Dấu **Sếp đã chủ động tắt** là `config/auto-update-disabled.json` (0600), **không** nằm trong `run/` |

**API — trạng thái 'stalled' (v0.1.37, F-34):** `GET /system/update` trả `state: "stalled"` + `stalled_reason` thay cho `'running'` khi: nhịp sống `genh-heartbeat.json` là nguồn **chính** — tươi (≤ 5 phút) và đúng `pid` ⇒ còn chạy, **không** xét `boot_id` của container (Docker Desktop for Linux chạy container trong VM: `boot_id` container khác máy chủ dù genh vẫn chạy). Ngoài ra: (1) `boot_id` trong `update-status.json` khác `boot_id` của nhịp sống (cả hai do genh ghi), hoặc nhịp sống cũ/thiếu mà `boot_id` lúc bắt đầu khác `boot_id` container (máy đã khởi động lại), hoặc `started_at` quá 60 phút mà không có nhịp sống tươi đúng `pid` ⇒ `stalled_reason: "process_gone"`; (2) yêu cầu trong `run/request/` nằm quá 15 phút chưa ai nhận ⇒ `stalled_reason: "not_picked_up"` — trừ khi nhịp sống tươi (một lần genh khác đang giữ khoá, vd lịch đêm) ⇒ vẫn `requested` + `host_busy: true`. **v0.1.53 (F-99):** `not_picked_up` được chia theo nguyên nhân để Console nói rõ — `stalled_reason: "linger_off"` (`autostart-status.json` ghi `linger` = `no` VÀ trình nhận yêu cầu là systemd `--user` — `genh.json` `updater` = `systemd`, genh cũ không ghi `updater` thì theo `linger_required`: tiến trình nền chỉ chạy khi có người đăng nhập; crontab/launchd chạy cả khi không ai đăng nhập nên linger không phải nguyên nhân), `stalled_reason: "watcher_failed"` (`nightly-status.json` ghi `request_watcher` = `failed`: trình nhận yêu cầu `gen-harness-update-request.path`/`.service` đang lỗi, vd start-limit), còn lại `not_picked_up`; yêu cầu còn nằm lại mà `update-status.json` là `failed` **GH-E94C** (`finished_at` ≥ `requested_at`) thì API giữ `state: "failed"` để hiện thông điệp của genh thay vì "chưa nhận". Container api **không thấy PID máy chủ** — "PID còn sống" được suy từ nhịp sống + `boot_id`. `GET /system/update` còn có `nightly_candidates` và `nightly`, `GET /system/health` có khối `nightly` và sự cố **`host.nightly`** (ngưỡng `NIGHTLY_STALE_HOURS` = 36 giờ; nút "Xem cách bật lại") — mục "Lịch tự cập nhật đêm tự lành và trung thực".

**Mã lỗi mới:** **GH-E94A** (v0.1.37) — đang có lần cập nhật/khôi phục khác giữ khoá loại trừ (gõ tay). **GH-E94B** (v0.1.37) — bị dừng giữa chừng do tín hiệu (SIGINT/SIGTERM); không ghi `update-blocked.json` (trừ khi quay về chưa trọn), lịch đêm được thử lại. API đưa ra `interrupted` (`rolled_back` = chưa đụng gì/đã tự quay về; `resume` = máy tắt sau khi đổi CSDL, cần chạy tiếp) ⇒ chuông/dải/thẻ Sức khoẻ báo **vàng** "Cập nhật lên vX bị dừng giữa chừng", không phải "chưa thành công" đỏ. **GH-E948** — ổ đĩa không đủ chỗ (sau khi đã dọn ảnh cũ), dừng trước khi tải, chưa đụng gì. **GH-E949** — bản đã quay về bản cũ, lịch đêm không thử lại (chỉ dùng cho thông điệp/log, không phải lỗi thoát). GH-E941 nay nghĩa là "tải thất bại, chưa đụng gì"; GH-E945 = bản mới lỗi từ bước migrate trở đi và genh đã tự quay về bản cũ (khôi phục CSDL chỉ khi có migration chờ). **GH-E94C** (v0.1.53, F-97) — không xoá được tệp yêu cầu trong `run/request` (quyền thư mục): genh KHÔNG làm yêu cầu (tránh `.path` kích lặp), ghi trạng thái `failed`, thoát 0, idempotent theo `requested_at`; Console: "Máy chủ không xoá được tệp yêu cầu — chưa đụng gì" (kiểm quyền `run/request` rồi Thử lại). **GH-E94A** dùng thêm khi trình nhận yêu cầu chờ khoá quá 30 phút (xoá tệp + `failed`).

**Ghi tệp trong `run/`:** qua tệp tạm tên ngẫu nhiên (`os.CreateTemp`, O_EXCL) + rename — `run/` để 0777 và bind-mount vào api, tên tạm cố định sẽ cho phép cài sẵn symlink để genh ghi đè tệp ngoài.

### Trực canh máy chủ (v0.1.44, F-6b)

Owner chỉ biết máy chủ "chết" khi tự mở Console. Từ v0.1.44 genh **tự trực canh** mỗi 12 phút và báo qua Telegram
("Báo động & bản tin" trong Console) — chạy được cả khi api đã chết, vì genh đo trên máy chủ chứ không gọi api.

- **Lịch** (`internal/autoupdate/watchdog*.go`, tên `gen-harness-watchdog`): systemd `--user` `.service` (Type=oneshot,
  `Nice=10`, `TimeoutStartSec=300`, log nối `logs/watchdog.log`) + `.timer` (`OnBootSec=5min`, `OnUnitActiveSec=12min`,
  `AccuracySec=1min`); fallback crontab `*/12 * * * *` với marker riêng `# gen-harness-watchdog (genh) — KHONG sua tay`;
  macOS LaunchAgent `com.gen-harness.watchdog` `StartInterval=720`; Windows `schtasks /SC MINUTE /MO 12 /RL LIMITED`.
  `genh install`/`genh update` bật **mặc định, idempotent, KHÔNG phụ thuộc `--no-auto-update`** và ghi cơ chế vào
  `run/watchdog-status.json` ("schedule"); lỗi chỉ cảnh báo. `genh watchdog enable|disable|status` bật/tắt/xem (status in
  cơ chế, lần chạy gần nhất, sự cố đang mở). `genh watchdog disable` ghi `config/watchdog-disabled.json` (Owner chủ động
  tắt) ⇒ install/update (kể cả lịch đêm) **không** bật lại, status ghi "Owner đã tắt"; `genh watchdog enable` xoá tệp đó.
  `genh uninstall` gỡ lịch.
- **Một lượt** = `genh doctor --notify --quiet` (`ops.RunWatchdog`, toàn lượt ≤ 4 phút, mỗi lệnh docker ≤ 30 giây; mã
  thoát luôn 0 trừ lỗi cấu hình nghiêm trọng GH-E961 — timer không "đỏ" vì sự cố của máy chủ):
  1. Khoá riêng `<gốc cài>/watchdog.lock` (lượt khác đang chạy ⇒ thoát 0). Khoá loại trừ `genh.lock` đang bị
     update/restore/import giữ ⇒ `state=skipped_busy`, không đo, không gửi. Có `config/paused-by-owner.json`
     (`genh stop` ghi, `genh start` và update/install thành công xoá) ⇒ `state=paused`, không tự khởi động lại, không báo.
  2. **Đo** (chỉ docker + tệp, `/api/v1/ready` là phép thử sống duy nhất): `docker compose ps --all --format json` lỗi ⇒
     `docker.down`; api không chạy, hoặc `/ready` không 200 ở 2 lần thử cách 10 giây ⇒ `api.down`; dịch vụ dài hạn
     (trừ `migrate`) `exited/dead` hoặc `unhealthy` ⇒ `service.unhealthy:<svc>`; `update-status.json` `failed` trong 24 giờ
     ⇒ `update.failed`; chỗ trống đĩa (gốc cài + DockerRootDir, như `genh update`) < ngưỡng tối thiểu ⇒ `disk.low` (ghi lại
     `disk-status.json`); `redis-cli MGET gh:worker:heartbeat gh:bridge:heartbeat`: nhịp worker cũ > 10 phút ⇒
     `worker.silent`, bridge chạy > 2 phút mà mất khoá ⇒ `bridge.silent`; bản sao lưu mới nhất (từ `api-health.json` còn
     tươi, nếu không thì `docker compose exec -T worker python -m gh.backup list`) cũ hơn `backup_stale_limit_hours`
     (mặc định 36 giờ) ⇒ `backup.stale`; `offsite-status.json` `configured` mà thành công gần nhất cũ hơn 7 ngày 12 giờ ⇒
     `offsite.stale`, `state=failed` ⇒ `offsite.failed`. Không đo được ⇒ **không mở cũng không đóng** khoá đó.
  3. **Tự khởi động lại**: `exited` ⇒ `docker compose up -d --no-deps <svc>`, `unhealthy` ⇒ `docker compose restart <svc>`;
     tối đa **1 lần/dịch vụ/60 phút** (ghi trong state); tin nói rõ "Đã tự khởi động lại <svc> lúc HH:MM".
  4. **Gộp sự cố phía api** từ `run/api-health.json` còn tươi (≤ 10 phút; cùng không gian khoá: `channel.down:zalo`,
     `model.auth_expired:<id>`, …). Khoá trùng ⇒ **số đo của genh thắng**. `api-health.json` không tươi (api chết) ⇒ khoá
     chỉ đến từ api **giữ nguyên** trạng thái cũ (không "đã ổn" giả).
- **Chống spam** — state ở `<gốc cài>/config/watchdog-state.json` (0600, **không** trong `run/`):
  `{schema, boot_id, last_run_at, incidents{key:{fingerprint,title,body,severity,first_seen,notified_at}}, resolved_pending[], restarts{svc:ts}}`.
  Mỗi lượt tối đa **1 tin CẢNH BÁO** (gộp mọi sự cố mới hoặc đổi fingerprint) + **1 tin ĐÃ ỔN** (gộp sự cố đã báo mà nay
  hết); sự cố còn mở không báo lại. Gửi lỗi/429 ⇒ không đánh dấu đã báo, lượt sau thử lại. `boot_id` đổi và lần chạy trước
  cách > 30 phút ⇒ thêm tin "Máy chủ vừa khởi động lại (tắt khoảng X)".
- **Tin Telegram**: văn bản thường, không emoji, không `parse_mode`, không bí mật: dòng 1 `Gen-Harness · CẢNH BÁO` /
  `Gen-Harness · ĐÃ ỔN`, dòng 2 tên máy, mỗi sự cố `• <tiêu đề>: <nội dung>`, cuối `Mở Console: <public_url hoặc
  https://localhost:<cổng>>/connections#telegram` + "(Tin tự động từ trực canh máy chủ — mọi thao tác Sếp xác
  nhận trong Console.)". Không có nút/hành động trong Telegram, không nhận tin từ Owner, không đi qua bridge Zalo.
- **Cấu hình Telegram** do api ghi `run/telegram.json` (`{schema:1, enabled, enc, briefing, reminders, updated_at}`), token
  **mã hoá phong bì GH1** (`gh.crypto.encrypt`, AAD `telegram_notify`) — genh giải bằng `secrets/gh_master_key`
  (`internal/notify`, vector cố định dùng chung pytest/go test). Chưa cấu hình/tắt ⇒ vẫn đo + ghi status
  (`telegram=not_configured|disabled`); giải mã lỗi (khoá master đổi) ⇒ `telegram=key_mismatch`. Lỗi gửi (`*SendError`)
  mang mã `TELEGRAM_TOKEN_REJECTED|CHAT_NOT_FOUND|BOT_BLOCKED|RATE_LIMITED|UNREACHABLE` và **không bao giờ chứa token**
  (lỗi mạng `url.Error` mang URL có token ⇒ bọc lại + che). Biến `GENH_TELEGRAM_API_BASE` chỉ cho test/e2e.
- **"Gửi thử"**: api ghi `run/request/watchdog.json` `{schema:1, action:"test"}` → watcher `genh handle-requests` (xoá tệp
  trước) → `genh doctor --notify --test --quiet` gửi "Gen-Harness · Tin thử từ trực canh máy chủ", ghi `test:{at,ok,error_code}`.
- **`run/watchdog-status.json`** (genh ghi nguyên tử mỗi lượt): `{schema:1, last_run_at, state: ok|issues|paused|skipped_busy|error,
  incidents:[{key,severity,title,since}], telegram: ok|not_configured|disabled|failed|key_mismatch, telegram_error_code,
  last_sent_at, schedule, test:{at,ok,error_code}|null}`. `logs/watchdog.log` xoay sang `.1` khi > 5 MB.
- **Giới hạn**: máy chủ **tắt hẳn** (mất điện, treo cứng) thì không có gì để chạy trực canh — Owner chỉ nhận tin khi máy bật
  lại (kèm "Máy chủ vừa khởi động lại"). Thiếu linger (Linux) ⇒ timer `--user` chỉ chạy khi đang đăng nhập (genh in cảnh
  báo kèm lệnh `sudo loginctl enable-linger $USER`). Trực canh không giữ khoá loại trừ khi đo (chỉ thử rồi nhả ngay) để
  không chặn lịch đêm; riêng lệnh tự khởi động lại (`restart`/`up -d --no-deps`) lấy `genh.lock` không chờ quanh đúng
  lệnh docker đó — update/restore/import đã lấy khoá giữa lượt ⇒ bỏ qua, không dựng lại service bằng compose/env cũ. `genh stop`/`genh uninstall` ghi `config/paused-by-owner.json` **trước** khi dừng/gỡ container và chờ lượt trực canh
  đang chạy xong (`watchdog.lock`); trực canh **không** tự `up -d` khi không có container api (bản cài đã gỡ), chỉ báo. Trạng
  thái `restarting` (docker đang tự thử lại) / `created` / `paused` cũng là sự cố (không restart chồng). "Gửi thử" chờ lượt
  định kỳ đang chạy xong thay vì bỏ qua.

### Gói chẩn đoán (v0.1.44, F-4b)

`genh doctor [--out f.zip]` (gõ tay) và nút **"Gói chẩn đoán"** trong Console (api ghi `run/request/doctor.json`
`{schema:1, request_id:"<16 hex>", requested_at}` → watcher → `genh doctor --if-requested`) tạo cùng một gói zip
(`internal/ops/doctor_bundle.go`):

- **Nội dung**: `report.txt` (các dòng chẩn đoán như cũ), `logs.txt` (`docker compose logs -t --tail=2000`),
  `genh-logs/{auto-update,offsite,watchdog}.log` (đuôi ≤ 1 MiB), `host/{update-status,restore-status,disk-status,
  autostart-status,offsite-status,genh,update-blocked,watchdog-status,doctor-status}.json` (đọc an toàn — không theo symlink,
  ≤ 64 KiB), `versions.txt` (genh, `docker version`, `docker compose version`, revision alembic qua `psql` trong `db` — lỗi
  thì ghi lý do, digest ảnh: ảnh khai trong compose.yaml + `docker compose ps` → `docker image inspect --format
  '{{json .RepoDigests}}'`), `manifest.json` (danh sách tệp + **số chỗ đã che**, không kèm giá trị).
- **Lọc bí mật** (`internal/redact`): MỌI mục văn bản đi qua Redactor — literal từ `config/secrets.json` (master key, mật khẩu
  CSDL, khoá sao lưu, mã thiết lập), `secrets/{gh_master_key,gh_bridge_key,gh_browser_key,gh_offsite_key}` (Khoá khôi phục
  che cả dạng bỏ dấu `-`), token Telegram giải mã được; rồi mẫu: token bot `\d{5,12}:[A-Za-z0-9_-]{30,}` (cả `/bot<token>/`),
  `Bearer …`, `scheme://user:pass@`, `password=|token=|secret=|api_key=…`, `sk-…`, `AIza…` ⇒ `***`. **Không bao giờ** đưa vào:
  `secrets/`, `config/secrets.json`, `.env`, `run/telegram.json`, `config/offsite.json`.
- **Qua hộp thư**: genh đọc an toàn rồi **xoá** yêu cầu (request_id sai dạng `^[a-f0-9]{16}$` ⇒ bỏ, không ghi gì), ghi
  `run/doctor-status.json` `{schema:1, request_id, state: running|done|failed, started_at, finished_at, file, size_bytes,
  sha256, error_code, message}`, zip vào `run/diagnostics/genh-doctor-<UTC yyyymmddThhmmssZ>.zip` (thư mục 0755 phải là thư
  mục thật thuộc người chạy genh — symlink ⇒ failed; tệp 0644 ghi qua tệp tạm O_EXCL + rename để api đọc), **giữ 3 zip mới
  nhất, xoá zip quá 24 giờ** (lần tạo sau và mỗi lượt trực canh 12 phút đều dọn). **Rủi ro còn lại:** zip chứa log đầy đủ
  mọi dịch vụ (có thể có dữ liệu khách) và phải 0644 để api (uid khác) đọc ⇒ người dùng khác trên CÙNG máy chủ đọc được
  trong tối đa 24 giờ — máy chủ nhiều người dùng thì tải về xong nên xoá tay `run/diagnostics/*.zip`. Lỗi ⇒ `failed` + `error_code` **GH-E962** + câu thân thiện. Doctor **không** lấy khoá loại trừ (chỉ đọc).

## Phát hành

**Từ v0.1.4: phát hành = tăng `VERSION` trong PR, merge vào main.** Không còn bước tay nào khác (không tự tạo tag, không tự bấm "Draft a release" trên web) — agent code không tạo được tag qua proxy, nên `.github/workflows/release.yml` (job `meta`) đọc thẳng tệp `VERSION` ở gốc repo mỗi lần có push vào `main`:
- Nếu tag ứng với version đó **chưa tồn tại** trên remote → chạy toàn bộ pipeline phát hành (build 6 nền tảng, build+push image, sinh Release, tự tạo tag `vX.Y.Z` trỏ đúng commit vừa merge).
- Nếu tag **đã tồn tại** (PR merge không đổi `VERSION`) → job `meta` trả `skip=true`, mọi job khác bỏ qua — không publish lại, không tạo Release trùng.
- Tag do workflow tự tạo (`softprops/action-gh-release`, ký bằng `GITHUB_TOKEN`) sẽ **không** tự kích hoạt lại `release.yml` — đúng ý, tránh chạy 2 lần vì chính tag mình vừa tạo.
- Đẩy tag `v*` bằng tay (hiếm dùng, vd. khôi phục sau sự cố) vẫn hoạt động như trước — job `meta` nhận version từ tag đó thay vì đọc `VERSION`.
- CI (`ci.yml`, job `version`) kiểm định dạng `VERSION` (`vMAJOR.MINOR.PATCH[-PRERELEASE]`) ngay từ PR, không đợi tới lúc chạy trên main mới phát hiện sai. Từ v0.1.33 job này còn chạy `.github/scripts/check_release_gate.py` (kiểm các bất biến của cổng phát hành bên dưới — PR lỡ tay gỡ cổng sẽ đỏ ngay).
- GitHub Actions: build `genh` cho 6 nền tảng, build + push image đa kiến trúc (`linux/amd64`, `linux/arm64`) lên GHCR, sinh `checksums.txt`, ký bằng cosign keyless (tệp `.sig`/`.pem` đính kèm — **chưa nơi nào kiểm chữ ký**, việc sau), đính `install.sh`, `install.ps1`, rootfs WSL vào Release.
- Từ v0.1.48 ảnh chỉ gắn `:<version>` và `:sha-<commit>`, **không còn** `:latest` (promote không gắn ảnh nữa); `compose.yaml` nhúng trong `genh` ghim đúng digest của bản phát hành đó. Tag `ghcr.io/<owner>/gen-harness-*:latest` **cũ vẫn còn trên GHCR và đứng yên mãi** ở bản cuối cùng được promote bằng `e2e-install.yml` trước v0.1.48 (v0.1.46; hoặc v0.1.47 nếu bản đó được promote trước khi v0.1.48 merge — job promote cũ còn gắn `:latest`) — không nhận bản vá nào, **không dùng** (kéo tay thì dùng `:<version>` hoặc digest trong `compose.release.yaml`). Không xoá được riêng tag đó qua API mà không xoá luôn phiên bản ảnh bản đó (cần cho máy cũ/rollback), nên để nguyên. Build ảnh thử lại 1 lần + cache GitHub Actions; tầng apt (api, db) dựng lại ít nhất mỗi tuần (`ARG APT_REFRESH` = tuần ISO) để cache không giữ gói apt cũ. Ảnh nền và caddy/redis ghim digest; `genh update` dọn cả digest caddy/redis cũ (chỉ ảnh kéo theo digest, không đụng ảnh có tag — `e2e-upgrade` kéo sẵn 1 digest caddy + 1 digest redis cũ không tag và đòi genh xoá). Hệ quả: `caddy:2-alpine`/`redis:7-alpine` do genh ≤ v0.1.47 kéo **theo tag** còn lại mãi trên máy cũ (2 ảnh, ~50–70MB, một lần); muốn lấy lại chỗ thì sau lần cập nhật kế tiếp v0.1.48 chạy `docker image rm caddy:2-alpine redis:7-alpine` (báo đang dùng thì bỏ qua) — **không** dùng `docker image prune -a` (xoá cả ảnh bản liền trước để lùi bản). Bản nâng chỉ tới máy Owner ở lần tăng `VERSION` kế tiếp. CI có quét bảo mật dạng báo cáo (không chặn; `.github/scripts/scan_summary.py`), kiểm bản nhúng genh khớp `deploy/` (`.github/scripts/check_embedded_sync.py`) và kiểm ảnh api/browser có sẵn `.pyc` (`.github/scripts/check_bytecode.py`).
- **Renovate** (`renovate.json`, tuỳ chọn — cần cài GitHub App): mỗi sáng thứ Hai mở PR nhãn `phụ thuộc`. Chỉ **thư viện** (pip/uv, npm, Go) bản nhỏ/vá đã ra **≥ 3 ngày** mới tự merge squash khi mọi check xanh (gồm E2E). **Không tự merge, chờ người (Claude) review:** ảnh Docker (gom 1 PR "ảnh Docker"), GitHub Actions (1 PR), bản lớn (major), nhóm playwright, nhóm uv (ảnh build + setup-uv + `make lock`), làm mới `uv.lock` (lockFileMaintenance). Renovate còn tạo 1 issue **Dependency Dashboard** liệt kê mọi bản chờ. Cài: https://github.com/apps/renovate → **Install** → **Only select repositories** → Gen-Harness → **Install** (Mend có thể đòi đăng nhập developer.mend.io bằng GitHub — chấp nhận). Cài xong: các PR không tự merge **nằm chờ** — chưa có lịch tự động (Routine) nào gọi Claude; khi Boss nhắn Claude "xử lý PR phụ thuộc" (một câu, không cần bấm trên GitHub) thì Claude review rồi merge. Trong lúc chờ, bản vá ảnh nền/action chưa tới máy Owner. `renovate.json` được kiểm ở job CI riêng `renovate-config` (chỉ khi tệp đổi, `--no-global`; không chạy trên đường phát hành).
- Job `verify-docker-pins` tải lại 4 tệp Docker Engine tĩnh đã ghim SHA-256 trong `apps/genh/internal/runtime/bootstrap_linux.go` (xem mục "Docker Engine tĩnh trên Linux" bên dưới) và so sha256 mỗi lần release — fail sớm nếu Docker thay nội dung tệp mà không đổi tên.

### Cổng phát hành (từ v0.1.33)

Máy người dùng chỉ nhận bản đã qua CI + E2E cài thật. Tự động hoàn toàn, không cần người duyệt (F-9, `docs/audit/2026-10-01/0-ke-hoach-tong.md`).

1. **CI trước Release.** `release.yml` gọi `ci.yml` thành job `ci` (`uses: ./.github/workflows/ci.yml`, `workflow_call`, input `from_release: true`); job `release` có `ci` trong `needs` — CI đỏ thì không có Release. Khi `from_release` bật, `ci.yml` dùng **nhóm concurrency riêng theo `run_id`** (`cancel-in-progress: false`), nên không huỷ lẫn nhau với lượt CI do chính push vào `main` kích hoạt.
2. **Bản thử (prerelease).** Job `release` tạo Release + tag `vX.Y.Z` ở dạng **prerelease** (`make_latest: false`). `/releases/latest` của GitHub bỏ qua prerelease, nên `genh`, `install.sh` và Console (`apps/api/gh/system_api/update.py`) **chỉ thấy bản đã qua E2E** — bản thử chưa tới máy nào.
3. **E2E đúng tag.** `e2e-install.yml` (`workflow_run` sau Release) tìm **đúng tag** từ `workflow_run.head_sha` (không lấy "bản mới nhất"), cài bằng `install.sh` với `GEN_HARNESS_RELEASE_TAG=<tag>`; job `e2e-upgrade` (ma trận ô `tags[1]` + `tags[3]`, v0.1.37) cài **bản chính thức cũ** có dữ liệu mẫu rồi nâng cấp lên đúng tag đó. Concurrency theo `head_sha`: lượt Release không tạo tag (VERSION không đổi) không huỷ E2E của bản có tag.
4. **Nâng thành bản chính thức (promote).** `e2e-install` + **mọi ô** `e2e-upgrade` + `e2e-rollback` + **`e2e-nightly-real`** (v0.1.53, F-100) xanh → job `promote` chạy **một** lệnh `gh release edit <tag> --prerelease=false --latest --notes-file <ghi chú>` — ghi chú thêm dòng `<!-- genh:promoted_at=<UTC> -->` (dấu thời điểm promote, GitHub không hiện; định dạng = `selfupdate.PromotedMarker`). In `releases/latest` **trước/sau** vào log, `exit 1` nếu sau đó latest không phải tag này hoặc thiếu dấu. Từ v0.1.48 promote không gắn ảnh GHCR nữa (genh ghim digest, không dùng `:latest`). Bản có hậu tố `-` (vd `v0.2.0-rc.1`) **không bao giờ** được promote; tag có/không hậu tố cùng trỏ một commit thì `resolve` chọn tag không hậu tố.
5. **Promote tay** — chỉ khi E2E lỗi vì lý do **ngoài mã** (mạng, GitHub/GHCR chập chờn): **Actions → E2E cài đặt thật → Run workflow**, nhập `tag` = `vX.Y.Z`, chọn `promote` = true và `skip_e2e` = true. E2E đỏ vì lỗi mã thật → sửa mã, tăng `VERSION`, phát hành bản mới; **không** promote tay.
6. **Thời gian chín 24 giờ** (chỉ lịch đêm). `genh update --yes` **không** kèm `--if-requested` (timer đêm ~03:00) chỉ cài bản đã là bản chính thức đủ 24 giờ (`selfupdate.NightlyMinAge`); bản chưa chín thì để đêm sau. 24 giờ tính từ **dấu `promoted_at`** trong ghi chú Release (mục 4); không có dấu (bản trước v0.1.33) thì từ `published_at` — lấy mốc muộn hơn. Lý do: `published_at` là lúc tạo **bản thử**, promote không đổi nó — bản thử promote muộn (vd promote tay `skip_e2e` sau vài ngày) mà tính từ `published_at` thì lọt cổng ngay đêm đó. **Cách chọn bản (v0.1.53, F-96):** trước đây lịch đêm chỉ hỏi `/releases/latest` nên khi bản ra dồn dập (vd v0.1.54 vừa lên 1 giờ trước, v0.1.53 đã chín 25 giờ) nó cứ bị bản mới nhất chưa chín hoãn mãi — "đói", không cài được bản nào. Nay chế độ lịch đêm (`MinAge` > 0) hỏi **danh sách** `GET /releases?per_page=10` (10 bản gần nhất) và cài bản **semver cao nhất** thoả: không phải bản nháp/bản thử, có dấu promote hợp lệ, mới hơn bản đang chạy, và `bây giờ − mốc chính thức ≥ 24 giờ` (đúng biên thì cho cài). Chưa bản nào đủ chín mà có bản mới hơn ⇒ hoãn (thoát 0, log nêu bản mới nhất và tuổi, chứa "đợi đủ 24 giờ"); bản mới nhất không có dấu promote ⇒ KHÔNG hứa "đợi" (có đợi cũng không tự cài) mà nói bỏ qua cho an toàn. Không hỏi được danh sách ⇒ rơi về `/releases/latest` (một dòng log). Nút **Cập nhật ngay** trong Console (`--yes --if-requested`) và `genh update` gõ tay không kèm `--yes` (`MinAge` = 0) **không** bị chặn và vẫn chỉ hỏi `/releases/latest`. `--yes` (chứ không phải một cờ riêng) là cờ kích hoạt cổng vì unit lịch đêm chỉ được ghi lúc `genh install`/`genh auto-update enable` — mọi máy đã cài đều đang chạy `update --yes --quiet`; ai gõ tay `genh update --yes` cũng bị đợi, dòng log nói rõ "chế độ --yes (lịch đêm)" và cách cài ngay. Console (`GET /system/update`: `published_at` = mốc chính thức của bản mới nhất, `nightly_candidates` = các bản chính thức có dấu promote mới hơn bản đang chạy kèm `eligible_at` = mốc + 24 giờ; api cũng chọn `latest` = semver cao nhất) ghi trên thẻ "Có bản mới": "Tự cài lúc ~03:00 sau <ngày giờ> — hoặc bấm Cập nhật ngay", và khi bản mới nhất chưa chín mà bản liền trước đã chín: "Tự cài v0.1.53 đêm 11/10 (~03:00) — v0.1.54 tự cài sau khi đủ 24 giờ (đêm 12/10)".
7. **E2E đường tự cập nhật thật (sau promote).** Job `e2e-selfupdate` (needs `promote`, chỉ chạy khi chính lượt đó vừa promote): cài bản chính thức liền trước, (genh cũ ≥ v0.1.33) `genh update --yes --quiet` phải **hoãn** bản vừa promote (log có "đợi đủ 24 giờ", version không đổi, không in "cập nhật xong."), rồi `genh update` (không `--yes`) → genh cũ **tự** tải genh mới qua `releases/latest`, re-exec `--self-updated`, nâng cấp dịch vụ bằng compose nhúng mới → `genh version` = tag, `/api/v1/ready` xanh, ảnh trong `compose.yaml` = `compose.release.yaml` của tag, `app_db_password` không đổi. Đỏ ⇒ `::error` + tóm tắt job ghi cách lùi bản chính thức (`gh release edit <tag> --prerelease=true`, `gh release edit <bản trước> --latest`) — còn trong 24 giờ chín nên chưa máy nào tự cài.
8. **E2E timer thật (trước promote, v0.1.53, F-100).** Job **`e2e-nightly-real`** chạy THẬT `gen-harness-update.timer` và `gen-harness-update-request.path` dưới user manager có linger — trước đó E2E không chạy timer/`.path` thật nên cả nhóm lỗi "lịch đêm tắt/mất, nút Cập nhật ngay không nhận" (F-93…F-99) không có test nào bắt được (go test dùng Runner giả). Như `e2e-install`, mọi ô `e2e-upgrade` và `e2e-rollback`, job này **bắt buộc xanh trước `promote`** (đỏ ⇒ không promote; không promote tay chỉ vì job này đỏ do lỗi mã). Các job E2E cũ cài bằng `--no-auto-update` nên có dấu opt-out, không bị tự lành đụng; riêng `e2e-upgrade` (bản cũ không ghi dấu) có thể được genh mới bật lịch bằng crontab trên runner — vô hại, nhưng các ô đó vẫn phải xanh.

Người bảo trì: merge xong, Release xanh mới chỉ là bản thử. Kiểm `gh api repos/<owner>/Gen-Harness/releases/latest` (hoặc log job `promote`) thấy đúng tag mới rồi mới báo "đã phát hành". Bảo vệ nhánh `main` + tag `v*` (required check `ci-ok`, `installer-ok`) là cài đặt trên GitHub, cần quyền admin — cách bật xem `docs/reports/HANDOFF-v0.1.1.md` mục v0.1.33; mã không phụ thuộc vào nó.

## Docker Engine tĩnh trên Linux (bootstrap tự cài)

`apps/genh/internal/runtime/bootstrap_linux.go` tự tải Docker Engine tĩnh (`docker-<version>.tgz` + `docker-rootless-extras-<version>.tgz`) từ `download.docker.com` khi máy Linux chưa có Docker hợp lệ. Docker **không** phát hành tệp `SHA256SUMS` trong thư mục `linux/static/stable/<arch>/` (đã xác minh: trả 404, thư mục chỉ có các `*.tgz`) — nên thay vì tải tệp checksum đó (bug GH-E021, khiến `genh install` luôn lỗi trên máy Linux sạch), SHA-256 của từng gói/kiến trúc được **ghim cứng** trong biến `dockerStaticChecksums`.

Cập nhật khi nâng `dockerStaticVersion`: tải cả 4 tệp (2 kiến trúc × 2 gói) từ `https://download.docker.com/linux/static/stable/<arch>/`, tính `sha256sum`, dán vào bảng — rồi để job `verify-docker-pins` (release.yml) xác nhận lại trên CI.

## Kiểm thử trình cài

Ma trận CI **thực tế** (sửa v0.1.37 — bản cũ ghi cả Debian/Fedora/macOS/Windows cài thật, không đúng):

- **`installer-matrix.yml`** chỉ **smoke-test binary** (`genh version`/`help`) + `go vet ./...`/`go test ./...` của `apps/genh` trên **ubuntu-22.04, ubuntu-24.04, macos-14, windows-2022** — không cài dịch vụ thật.
- **Cài thật** chỉ ở **`e2e-install.yml`**, runner **ubuntu-24.04** (Docker sẵn), gồm: `e2e-install` (chế độ pr + release), `e2e-rollback` (bản hỏng cố ý — tự quay về bản cũ), `e2e-upgrade` có dữ liệu mẫu từ **`tags[1]`** (bản chính thức liền trước) và **`tags[3]`** (máy tắt vài ngày nhảy nhiều bản — bỏ qua khi chưa đủ 3 bản chính thức cũ hơn), `e2e-selfupdate` sau promote, `e2e-nightly-real` (v0.1.53 — timer và `.path` thật dưới user manager có linger).
- **Debian, Fedora, macOS, Windows KHÔNG cài thật** trong CI. Ô Fedora chạy trong container đã **bỏ** (không kiểm được SELinux, systemd `--user`, firewalld — kế hoạch tổng `docs/audit/2026-10-01/0-ke-hoach-tong.md` mục (d)); chạy VM Fedora khi có runner.
- Chưa tự động hoá: mất mạng giữa chừng rồi chạy lại, cổng bận, đĩa đầy.

`.github/workflows/installer-matrix.yml` ở trên smoke-test binary (`genh version`/`help`, cài đặt best-effort không có image publish sẵn) và **từ v0.1.33 chạy `go vet ./...` + `go test ./...` của `apps/genh` trên cả 4 hệ điều hành** (ubuntu-22.04, ubuntu-24.04, macos-14, windows-2022); job tổng `installer-ok` luôn chạy (không lọc đường dẫn) và là required check. **`.github/workflows/e2e-install.yml`** mới là lần cài THẬT bằng Docker (runner `ubuntu-24.04` có Docker sẵn):
cài sạch (`install.sh` thật ở chế độ bản phát hành, hoặc build từ source + ghim `compose.release.yaml` đã publish ở chế độ PR) → `/api/v1/ready` xanh → `genh status` → thử tạo dữ liệu mẫu qua `POST /setup/steps/1` (best-effort, xem ghi chú đầu file) → `genh backup` → `genh export`/`genh import` → `genh update --yes` → `genh auto-update enable/status/disable` → `genh uninstall --yes --delete-data` (v0.1.40: uninstall mặc định giữ dữ liệu, dọn sạch phải kèm `--delete-data`). Job **`e2e-offsite`** (v0.1.40, F-12 — chế độ pr + release, promote đòi xanh): cài sạch + dữ liệu mẫu → `genh offsite set` vào thư mục không tồn tại phải GH-EB01 và không tạo thư mục → chọn `$RUNNER_TEMP/usb` (`--allow-same-disk --no-run`) → chạy ĐÚNG lệnh lịch tuần (dòng crontab sau marker, hoặc unit systemd `--user`) → `run/offsite-status.json` ok + verified, đúng 1 gói → chép Khoá khôi phục ra `kit.txt` → `genh uninstall --yes --delete-data` → cài sạch vào `GEN_HARNESS_HOME` mới → `genh import` gói bằng `kit.txt` → số dòng trùng khớp (thử khôi phục thật). Kích hoạt:
- tự động sau khi workflow **Release** thành công (`workflow_run`) — kiểm **đúng tag** của commit vừa phát hành (tìm từ `head_sha`, cài bằng `install.sh` với `GEN_HARNESS_RELEASE_TAG`), xanh thì job `promote` nâng thành bản chính thức (xem "Cổng phát hành");
- tay qua **Actions → E2E cài đặt thật → Run workflow**: input `tag` (bỏ trống = bản chính thức hiện tại), `promote` (nâng tag đó thành bản chính thức nếu xanh), `skip_e2e` (chỉ dùng kèm `promote` khi E2E lỗi vì lý do ngoài mã);
- tự động trên PR/push (chế độ pr) đổi `apps/genh/**`, `deploy/**` (gồm `deploy/images/**`), `install.sh`, `apps/api/**`, `apps/web/Dockerfile`, `VERSION` (danh sách đầy đủ ở khối `paths:` đầu tệp). Chế độ pr ghim `compose.release.yaml` của **bản chính thức** qua `releases/latest` (không dùng `gh release list` — tránh lấy nhầm bản thử).

Từ v0.1.37 `e2e-install` còn kiểm (genh ≥ v0.1.37; chế độ pr luôn kiểm): **khoá loại trừ** — giữ `<gốc cài>/genh.lock` bằng `flock` rồi `genh update --yes --no-self-update` phải thoát 0, in "đang có một lần cập nhật/khôi phục khác chạy", `run/update-status.json` không đổi; **tự chạy lại khi bật máy** — `genh status` ghi `run/autostart-status.json` đủ khoá, `docker_enabled` ∈ {yes, no, unknown} (giá trị chỉ in ra, không đỏ vì cấu hình runner).

Có thêm job `e2e-upgrade` (chỉ chạy ở 2 cách kích hoạt đầu) — **ma trận** theo output `upgrade_from` của `resolve` (v0.1.37): ô **`tags[1]`** = bản chính thức liền trước tag (máy Owner cập nhật đều), ô **`tags[3]`** = bản chính thức cũ hơn 3 bậc (máy tắt vài ngày, nhảy nhiều bản một lần; chưa đủ 3 bản cũ hơn ⇒ ô vắng, tóm tắt `resolve` ghi "tags[3]: bỏ qua", không đỏ). `fail-fast: false`, không `continue-on-error`: ô nào đỏ ⇒ không promote (`check_release_gate.py` giữ). Mỗi ô: cài **bản chính thức cũ** + nạp dữ liệu mẫu, rồi nâng cấp LÊN **đúng tag đang xét** — binary `genh` của tag đó được đặt bằng tay vì bản thử bị ẩn khỏi tự cập nhật — rồi `genh update`, xác nhận `app_db_password` không đổi (dữ liệu không bị tạo lại). Đường "genh cũ tự tải binary mới" (`internal/selfupdate`) không đi qua job này; nó do job `e2e-selfupdate` kiểm **sau promote** (xem "Cổng phát hành" mục 7) cùng `go test` của `selfupdate`.

**Đọc kết quả:** vào tab Actions → chọn lần chạy → mở job `e2e-install` (và `e2e-upgrade` nếu có). Mỗi bước đặt tên tiếng Việt đúng việc nó làm; hai bước cuối luôn chạy (`if: always()`) in log `docker compose logs --tail 200` + log `~/.gen-harness/logs` — bước nào đỏ, mở log của CHÍNH bước đó trước, rồi tới 2 bước log cuối để xem dịch vụ nào bên trong container lỗi. `genh uninstall --yes --delete-data` (v0.1.40; genh cũ hơn chỉ `--yes`) cần cờ `--yes` mới CLI (thêm ở phiên e2e-install — trước đó `ops.UninstallOptions.AutoApprove` không có cờ CLI, xem `apps/genh/internal/ops/uninstall.go`).

Giới hạn đã biết (ghi rõ, không bịa đã làm): dữ liệu mẫu chỉ gọi được bước 1/12 của `/setup` (`mode=sample`, không cần tài khoản Owner) — các bước 2-12 (owner/PIN/tổ chức/kết nối Zalo QR/agent...) cần thao tác không tự động hoá được trong CI, KHÔNG chạy ở đây.
