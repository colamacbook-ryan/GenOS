# Rà soát 4 — Vận hành, phát hành, tự bảo trì (Gen-Harness v0.1.31)

Phạm vi: snapshot `origin/main` = `eb5a71b` (v0.1.31), các workflow `.github/workflows/*`, lịch sử chạy GitHub Actions
(34 lần Release, 98 lần E2E, 100 lần CI gần nhất), mã `apps/genh` (Go), `apps/api/gh/{backup,worker,system_api,…}`.
Chỉ đọc; đã chạy thử `go vet ./...` + `go test ./...` của `apps/genh` trên **bản sao** trong scratchpad (tất cả xanh).

Ký hiệu: 🔴 nghiêm trọng (có thể mất dữ liệu / sập âm thầm trên máy Owner) · 🟠 nên sửa sớm · 🟡 vệ sinh/nhỏ.
Effort: S ≤ ½ ngày · M 1–2 ngày · L > 2 ngày.

---

## (a) Sơ đồ quy trình phát hành → cập nhật

```
 Claude (nhánh claude/*) ──PR──► CI ci.yml (~8 phút)                     Installer matrix (genh build + version/help,
                                  ├ version  (định dạng VERSION)           ubuntu22/24, macos14, win2022 — chỉ smoke)
                                  ├ api      (ruff + mypy + pytest, PG thật cùng image + Redis)
                                  ├ browser  (ruff + mypy + pytest Chromium thật)
                                  ├ web      (eslint, tsc, vitest, vite build, bridge test)
                                  └ images   (build 5 ảnh + `compose config -q`, KHÔNG khởi động stack)
                                 E2E chế độ "pr" — CHỈ khi PR đổi apps/genh/**, deploy/**, install.sh
                                  │
              squash-merge vào main  (main KHÔNG có branch protection — list_branches: protected=false)
                                  │
            ┌─────────────────────┴───────────────────────────┐   (chạy SONG SONG, không chờ nhau)
            ▼                                                 ▼
   CI ci.yml trên main                          Release release.yml (~7 phút)
                                                 meta: đọc VERSION, tag đã có ⇒ skip
                                                 ├ verify-docker-pins (sha256 Docker tĩnh)
                                                 ├ build-images ×5 (amd64+arm64) → GHCR :vX + :latest, lấy digest
                                                 ├ wsl-rootfs (Alpine nguyên bản)
                                                 ▼
                                                 pin-compose → compose.release.yaml (image@sha256)
                                                 ▼
                                                 build-genh ×6 (go:embed compose đã ghim, -X main.version)
                                                 ▼
                                                 release: checksums.txt + cosign sign-blob + GitHub Release
                                                 ══► NGAY LẬP TỨC là "latest" (máy Owner thấy được)
                                                 ▼ workflow_run (sau khi đã phát hành)
                                                 E2E e2e-install.yml (~3 phút)
                                                 ├ e2e-install: install.sh thật → ready → status → backup →
                                                 │   export/import → update → nút "Cập nhật ngay" → nút "Khôi phục"
                                                 │   → auto-update enable/status/disable → uninstall
                                                 └ e2e-upgrade: cài bản N-1 → genh update → ready → kiểm browser
                                                     cách ly mạng → app_db_password không đổi

 MÁY BOSS (Fedora, docker compose)
   ① systemd --user timer 03:00 ±30' (Persistent)  ─┐
   ② Nút Console → api ghi run/request/update.json ─┤→ genh update --yes --quiet [--if-requested]
      → systemd .path → `genh handle-requests`      │
   ③ Boss gõ `genh update` / chạy lại install.sh   ─┘
        hostlink.Start("running")
        selfupdate: GET api.github.com/releases/latest → tải checksums.txt + genh-linux-amd64 → so SHA-256
                    → thay binary → re-exec `genh update --self-updated`
        RunUpdate:  1 backup (pg_dump trong container api → volume gh_objects, CÙNG ổ đĩa)
                    1.5 đồng bộ compose.yaml với bản nhúng (giữ compose.yaml.bak)
                    2 di trú /tmp/gh-objects (bản v0.1.0)
                    3 docker compose pull   (5 ảnh ghim digest + caddy:2-alpine, redis:7-alpine KHÔNG ghim)
                    4 docker compose run migrate (alembic upgrade heads — forward-only)
                    5 docker compose up -d --remove-orphans → chờ /api/v1/ready ≤ 3 phút → tin cậy lại CA
        Lỗi sau bước 1 ⇒ rollback: compose.yaml.bak → pg_restore bản backup bước 1 → up -d → status "failed"
        Thành công ⇒ status "done", ghi run/genh.json (version, updater)
        (KHÔNG dọn image cũ, KHÔNG báo ra ngoài app, KHÔNG khoá chống chạy chồng)
```

Số liệu thực tế: CI thành công trung bình 8,2 phút (100 lần gần nhất); Release v0.1.31 7 phút 12 giây
(run 36845527953); E2E release ~3 phút (run 36846293658: cài sạch 1,5 phút, nâng cấp N-1 49 giây).
Merge → bản phát hành đã kiểm E2E: ~11 phút.

---

## (b) Điểm mạnh

1. **Phát hành một chạm, idempotent**: tăng `VERSION` là đủ; `meta` tự bỏ qua khi tag đã có
   (`release.yml:42-72`), CI kiểm định dạng `VERSION` ngay ở PR (`ci.yml:19-35`).
2. **Bộ phiên bản nhất quán**: 5 ảnh build cùng commit, digest được ghim vào `compose.release.yaml` rồi nhúng vào
   chính binary `genh` (`release.yml:329-370`, `:171-243`) — không lệch phiên bản giữa api/web/bridge/db/browser.
3. **Kiểm toàn vẹn bắt buộc**: `install.sh:113-116` và `selfupdate.go:243-265` từ chối binary sai SHA-256; checksum
   Docker tĩnh ghim cứng và được tải lại kiểm mỗi lần phát hành (`release.yml:81-159`); có ký cosign keyless.
4. **E2E cài thật rất tốt cho quy mô dự án**: cài sạch + nâng cấp từ N-1 + nút Cập nhật/Khôi phục trong Console +
   export/import + kiểm bí mật không bị tạo lại + kiểm cách ly mạng container Chromium (`e2e-install.yml:497-532`).
   E2E đã bắt lỗi thật trước khi merge (4 lần đỏ ngày 28/09 với nút "Cập nhật ngay": run 36412658455…36413062835).
5. **`genh update` có backup trước + rollback tự động** và được phủ unit test kỹ: 16 test `RunUpdate` trong
   `apps/genh/internal/ops/update_test.go` (7 kịch bản rollback); `go test ./...` chạy thử: tất cả xanh.
6. **Tự cập nhật không cần Boss**: tự thay binary + re-exec (`main.go:342-382`), timer `Persistent=true` +
   `RandomizedDelaySec` (`content.go:37-49`), bật `linger` (`systemd.go:246-248`), fallback crontab; nút Console đi
   qua "hộp thư" tệp — container api không cần socket Docker (thiết kế an toàn).
7. **Sao lưu**: mã hoá, GFS 7/4/12 + giữ mọi bản 24 giờ qua, bản an toàn trước update/restore/import
   (`backup.py:70-79, 268`), khôi phục trên UI cần PIN + gõ "KHÔI PHỤC", báo chuông khi backup lỗi (`backup.py:395-410`).
8. **Dọn dẹp dữ liệu định kỳ**: phiên hết hạn, hội thoại Gen, chuông thông báo, pg_partman (`worker.py:191-201`),
   Redis Stream có `MAXLEN` (`chassis/bus.py:97`), nhắc token Gen-hub sắp hết hạn.
9. **Chẩn đoán**: `genh doctor` xuất zip (runtime, cổng, chứng chỉ, dung lượng, đồng hồ, bridge + 500 dòng log);
   lỗi `OpError` có What/Why/Next tiếng Việt dễ hiểu; trang Trợ giúp có nút chép thông tin chẩn đoán.
10. **Ít flaky**: 100 lần CI gần nhất, main 8/9 xanh; lần đỏ duy nhất (test thứ tự thông báo) đã được sửa gốc bằng
    `clock_timestamp()` (`notifications.py:124`). Release 34 lần: 1 huỷ (v0.1.6, treo QEMU — đã sửa v0.1.7),
    1 lỗi mạng tạm thời (v0.1.30).

---

## (c) Phát hiện

### 🔴 Nghiêm trọng

#### 🔴1. Bản phát hành thành "latest" TRƯỚC khi được kiểm; không có cổng chặn CI/E2E — máy Owner tự nhận bản lỗi lúc 03:00
- **Bằng chứng**:
  - `release.yml` chạy trên `push: main` song song với CI, các job chỉ `needs: meta` — không chờ CI. Bản **v0.1.24**
    được phát hành (Release run 36581421234, xanh, published 14:23:34) trong khi **CI trên đúng commit đó đỏ**
    (run 36581421131, job 109450289123: `1 failed, 997 passed`).
  - E2E chỉ chạy **sau** khi Release xong (`e2e-install.yml:32-34`, `workflow_run`); release job dùng
    `softprops/action-gh-release` mặc định đánh dấu latest (`release.yml:489-497`). `selfupdate.go:208-237` và
    `install.sh:13` đọc `releases/latest` ⇒ E2E đỏ cũng không ngăn được máy Owner cập nhật.
  - `main` không bảo vệ (`list_branches`: `"protected": false`) — "CI xanh mới merge" chỉ là quy ước.
  - Tài liệu nói ngược: `docs/ROADMAP.md:3` "CI + E2E cài thật xanh mới phát hành".
  - Kênh `--channel stable|beta` chỉ là trang trí (`ops/update.go:25-31`) — không có độ trễ an toàn nào.
- **Ảnh hưởng**: một bản hỏng (migration lỗi, ảnh thiếu thư viện…) sẽ tự cài lên máy duy nhất đang chạy thật trong
  ≤ 24 giờ; 31 bản trong 6 ngày ⇒ xác suất không nhỏ.
- **Cách sửa**: release tạo bản **prerelease** (hoặc draft) → E2E kiểm đúng tag đó → job cuối `gh release edit
  <tag> --prerelease=false --latest` chỉ khi E2E xanh; release `needs` CI (gọi ci.yml dạng reusable workflow, hoặc
  kiểm check-runs của commit); bật branch protection với required checks (Boss bấm một lần trong Settings).
  Tuỳ chọn: auto-update chỉ nhận bản đã "chín" ≥ 12–24 giờ.
- **Effort**: M.

#### 🔴2. Rollback khôi phục CSDL kể cả khi CHƯA đụng gì (pull lỗi) — mất dữ liệu ghi trong khoảng đó; và chu trình này chạy MỖI ĐÊM
- **Bằng chứng**:
  - `ops/update.go:201-211`: `docker compose pull` lỗi ⇒ `rollbackAndWrap` ⇒ `restoreInContainer` (`:348`) ⇒
    `restore_backup` **xoá và tạo lại CSDL** rồi `pg_restore` (`backup.py:296-321`). Lúc pull lỗi dịch vụ cũ vẫn
    đang chạy và chưa migrate — restore là không cần, và xoá mọi tin Zalo/WhatsApp, thao tác của Boss ghi từ lúc
    backup tới lúc restore.
  - `main.go:281-313`: khi đã ở bản mới nhất, `genh update` vẫn chạy **đủ** backup → pull → migrate → up (E2E bước
    "đã mới nhất" chạy 8 giây = cả chu trình). Bước pull luôn cần mạng tới Docker Hub vì `caddy:2-alpine`,
    `redis:7-alpine` không ghim (`deploy/compose.yaml:31, 249`).
  - Pull thật của một bản mới là vài trăm MB–GB qua mạng gia đình; chính CI cũng gặp `ECONNRESET`
    (run 36741082010, job 109975416642).
- **Ảnh hưởng**: mỗi lần mạng chập chờn lúc cập nhật (đêm, hoặc Boss bấm nút ban ngày) = mất dữ liệu vài giây
  tới vài chục phút + gián đoạn dịch vụ do drop/restore CSDL.
- **Cách sửa**: (1) pull ảnh **trước** bước backup (không phá gì, lỗi thì dừng sạch); (2) chỉ restore CSDL nếu
  migrate đã chạy; (3) khi có migrate: dừng api/worker/bridge (nguồn ghi) trước khi backup để không có "khoảng
  mất"; (4) bỏ qua cả chu trình khi phiên bản không đổi và ảnh đã có sẵn (hoặc chỉ `up -d`).
- **Effort**: S–M.

#### 🔴3. Không dọn image cũ, không theo dõi dung lượng đĩa — đĩa đầy dần, Postgres dừng, Boss không được báo
- **Bằng chứng**: không có `docker image prune`/`rmi` nào trong `apps/genh` (grep "prune|rmi" chỉ ra GH_BACKUP_KEEP).
  Mỗi bản kéo ảnh mới theo digest; release build không có cache (`release.yml:293-306`, không `cache-from`) nên
  phần lớn lớp đổi digest mỗi bản (api có apt + agy + Claude CLI, `deploy/images/api.Dockerfile:12-49`). Đo ở
  v0.1.10: ~1,5 GB/bản giải nén (`machine/checks.go:48-50`), nay thêm ảnh browser Playwright. 31 bản/6 ngày.
  `/ready` không kiểm đĩa (`shell/routes.py:80-99`); `genh doctor` chỉ đếm dòng volume, không cảnh báo ngưỡng
  (`ops/doctor.go:117-124`); không có thông báo nào loại "đĩa" (danh sách `kind=` trong api).
- **Ảnh hưởng**: sau vài tuần–vài tháng tự cập nhật, ổ đầy ⇒ Postgres/Redis lỗi ghi ⇒ toàn hệ thống dừng âm thầm;
  rollback cũng thất bại vì không ghi được backup.
- **Cách sửa**: sau update thành công, xoá ảnh `ghcr.io/<owner>/gen-harness-*` không thuộc bản hiện tại và bản
  liền trước (giữ để rollback); thêm kiểm dung lượng trống (ví dụ < 10 % hoặc < 5 GB) vào watchdog/`/ready` và
  gửi cảnh báo.
- **Effort**: S.

#### 🔴4. Sao lưu chỉ nằm trên chính ổ đĩa đó, khoá giải mã cũng chỉ ở đó — hỏng ổ là mất hết
- **Bằng chứng**:
  - Backup lưu vào `ObjectStore` cục bộ = volume `gh_objects` (`backup.py:257`, `chassis/objects.py:55`) — cùng
    ổ với `pg_data`.
  - Tệp tải về từ Console "chỉ khôi phục được bằng khoá của bản cài này" (`system_api/backups.py:151-152`); khoá
    nằm trong `config/secrets.json` / `secrets/` trên máy. README tự cảnh báo (`README.md:124-132`).
  - Backup GFS chỉ là `pg_dump` (`backup.py:240-271`) — **tệp tài liệu đã tải lên** (`biz/relations/routes.py:747`)
    và volume `agy_state` (phiên đăng nhập CLI) không có trong backup tự động.
  - Đường duy nhất ra ngoài máy là `genh export` **thủ công** qua CLI (`README.md:129-132`) — Boss không gõ lệnh.
  - `genh uninstall` mặc định xoá volume (`ops/uninstall.go:71-72`) ⇒ xoá luôn mọi bản backup.
- **Ảnh hưởng**: ổ SSD hỏng / máy mất / gỡ nhầm = mất toàn bộ dữ liệu kinh doanh, không có đường khôi phục.
- **Cách sửa**: lịch tự động xuất `.ghbundle` (đã có định dạng, gồm CSDL + tài liệu + khoá, mã hoá bằng mật khẩu)
  ra đích ngoài máy (Google Drive qua connector, ổ USB/NAS gắn sẵn, hoặc rclone); Console hiện "bản sao ngoài máy
  gần nhất: X ngày trước" và báo khi > 7 ngày; giữ mật khẩu gói ở Kho/vault. `uninstall` mặc định `--keep-data`.
- **Effort**: M.

#### 🔴5. Không có cảnh báo chủ động nào ra ngoài ứng dụng — hệ thống sập thì không ai biết
- **Bằng chứng**: các loại thông báo hiện có (grep `kind=` trong `apps/api/gh`): `backup.done/failed`,
  `hub.token_expiring`, nghiệp vụ… **Không có** cho: cập nhật lỗi/rollback (chỉ ghi `run/update-status.json`, hiện ở
  `web/src/update/UpdateCard.tsx`), worker chết (cron backup/purge ngừng), kênh Zalo/WhatsApp rớt (bridge heartbeat
  chỉ ở `/ready`), CLI hết hạn (chỉ hiện nhãn trong UI, `providers/cli.py:16`), đĩa đầy. Mọi thông báo đều là chuông
  **trong app** — khi app sập thì vô dụng. Docker không tự khởi động lại container "unhealthy"; `/ready` không kiểm
  worker/browser.
- **Ảnh hưởng**: với Boss không rành kỹ thuật, "tự bảo trì" thất bại âm thầm: cập nhật lỗi lặp lại mỗi đêm (xem 🟠2),
  backup ngừng, kênh rớt nhiều ngày mà không ai hay.
- **Cách sửa**: watchdog do `genh` cài (timer 10–15 phút, cùng cơ chế auto-update): kiểm `/ready`, container
  unhealthy (tự restart), dung lượng đĩa, tuổi backup gần nhất, `update-status=failed`, heartbeat worker/bridge →
  gửi **một kênh ngoài** (Zalo/Telegram của Owner, email, hoặc ghi vào Kho dữ liệu qua Gen-hub để Claude thấy đầu phiên),
  chống spam (1 lần/sự cố + nhắc lại 24 giờ). Đồng thời thêm thông báo trong app cho các loại trên.
- **Effort**: M.

### 🟠 Nên sửa sớm

#### 🟠1. Bộ test Go của `genh` (bộ máy tự bảo trì) KHÔNG chạy trong CI
- **Bằng chứng**: 45 tệp `*_test.go` (~7,7 nghìn dòng) trong `apps/genh`; grep `go test|go vet` trong
  `.github/` và `Makefile`: không có. `installer-matrix.yml:34-50` chỉ `go build` + `genh version/help`.
  Chạy thử trên bản sao: `go vet` sạch, `go test ./...` xanh — nhưng không có gì giữ cho nó xanh.
- **Ảnh hưởng**: một thay đổi làm hỏng rollback/selfupdate sẽ qua CI và tự cài lên máy Owner (kết hợp 🔴1).
- **Cách sửa**: thêm job `genh` vào `ci.yml`: `go vet ./... && go test -race ./...` (+ `govulncheck`). **Effort**: S.

#### 🟠2. Rollback thất bại đúng ở kịch bản hay gặp nhất (container api mới crash-loop) → bản cũ chạy trên schema mới; và vòng lặp lỗi mỗi đêm
- **Bằng chứng**: rollback khôi phục DB bằng `docker compose exec api …` vào **container api mới** (`update.go:348`,
  `backupcore.go:101-112`); chỉ rơi về `run --rm` khi lỗi chứa "is not running" (`backupcore.go:128-130`) — container
  đang restart báo "is restarting" ⇒ restore lỗi ⇒ `up -d` bằng compose cũ ⇒ code cũ chạy trên CSDL đã migrate,
  thông báo "ROLLBACK THẤT BẠI". Binary `genh` đã tự cập nhật thì không quay lại; đêm sau `LocatePathSync` đồng bộ lại
  compose mới và thử lại ⇒ mỗi đêm: backup → update lỗi → restore (🔴2). E2E không có kịch bản tiêm lỗi nào để kiểm
  rollback thật trên Docker (chỉ unit test với runner giả).
- **Cách sửa**: trong rollback, khôi phục compose cũ rồi **luôn** dùng `run --rm --no-deps api` (ảnh cũ) để restore,
  hoặc `up -d` bản cũ trước rồi mới restore; ghi "bản lỗi" vào `run/` để không tự thử lại bản đó (chờ bản sau);
  thêm job E2E "bản hỏng cố ý" (migration raise) xác nhận rollback xanh. **Effort**: S (code) + M (E2E).

#### 🟠3. Không có khoá loại trừ trên máy chủ; trạng thái "running" có thể kẹt; SIGTERM giết giữa chừng
- **Bằng chứng**: timer đêm, watcher (`handle-requests`, `main.go:505-529`) và lệnh tay đều gọi `runUpdate`/
  `runRestore` không có `flock`; `runUpdate` không kiểm restore đang chạy. Máy mất điện giữa update ⇒
  `update-status.json` giữ "running"; API chỉ coi là "kẹt" với trạng thái "requested" (`system_api/update.py:117-121`)
  ⇒ Console chặn cả Cập nhật lẫn Khôi phục (`update.py:170-171`, `backups.py:188-189`) tới lần chạy đêm sau.
  Chỉ bắt `os.Interrupt` (`main.go:306`) — `systemctl stop`/đăng xuất gửi SIGTERM ⇒ chết giữa bước, không rollback.
- **Cách sửa**: `flock` trên `run/genh.lock` cho update/restore/import; "running" quá 60 phút ⇒ coi là failed;
  bắt SIGTERM vào cùng context để rollback. **Effort**: S.

#### 🟠4. Kiểm nâng cấp quá hẹp: chỉ từ bản N-1, CSDL rỗng, chỉ Ubuntu — máy Owner nhảy nhiều bản, có dữ liệu thật, chạy Fedora
- **Bằng chứng**: `e2e-upgrade` lấy đúng `tags[1]` (`e2e-install.yml:411-423`), không nạp dữ liệu (chỉ e2e-install gọi
  setup bước 1). 31 bản/6 ngày + timer `Persistent` ⇒ máy tắt vài ngày sẽ nhảy N-5 → N qua nhiều migration.
  Tài liệu ghi ma trận có Fedora 40/Debian 12 (`docs/handoff/05-installer.md:159`) nhưng cài thật chỉ chạy
  `ubuntu-24.04`; không kiểm Fedora (SELinux, firewalld, `update-ca-trust`, docker.service không tự bật mặc định).
  E2E nút "Cập nhật ngay" chỉ thử trường hợp "đã mới nhất" (`e2e-install.yml:284-310`).
- **Cách sửa**: ma trận nâng cấp từ N-1, N-5 và "bản Boss đang chạy"; `make seed-demo` trước nâng cấp và so số dòng
  sau; thêm ô Fedora (container `fedora:40` + Docker, hoặc runner tự host); thêm bước kiểm "docker.service enabled".
  **Effort**: M.

#### 🟠5. Ảnh nền và phụ thuộc không ghim — bản build không tái lập, tổ hợp chạy thật chưa từng được kiểm
- **Bằng chứng**: runtime `caddy:2-alpine`, `redis:7-alpine` (`compose.yaml:31, 170, 249`) được `genh update` kéo lại
  mỗi đêm; `pin-compose-images.sh` chỉ ghim 5 ảnh tự build. Ảnh nền build: `python:3.11-slim`, `node:22-slim`,
  `pgvector/pgvector:pg16`, `nginx-unprivileged:1.27-alpine` (chỉ ảnh browser ghim digest). Python dùng `>=` không
  lockfile (`apps/api/pyproject.toml`, `RUN pip install .` ở `api.Dockerfile:49`) — log CI đã cho thấy
  `starlette==1.7.0` được kéo tự do.
- **Cách sửa**: ghim digest caddy/redis trong compose (đi qua pin-compose), `uv lock` + `uv sync --frozen` trong
  Dockerfile/CI, bật Dependabot/Renovate để nâng có kiểm soát. **Effort**: M.

#### 🟠6. Lỗi mạng tạm thời làm hỏng phát hành; không có retry/cache
- **Bằng chứng**: v0.1.30 attempt 1 (run 36741082010, job 109975416642): `npm error code ECONNRESET` khi build ảnh
  web → cả Release dừng, phải chạy lại tay 27 phút sau; khi đó 4 ảnh khác đã đẩy `:latest` lên GHCR dù bản chưa phát
  hành (`release.yml:304-306`). Build-push không dùng cache.
- **Cách sửa**: trong Dockerfile `npm ci --fetch-retries=5 --fetch-retry-maxtimeout=120000` (tương tự `pip
  --retries`), bọc bước build bằng thử lại 1 lần (step thứ hai `if: failure()`), `cache-from/cache-to: type=gha`,
  chỉ gắn `:latest` sau khi release xong (hoặc bỏ hẳn). **Effort**: S.

#### 🟠7. Khó chẩn đoán từ xa: log production mất thời gian + traceback, mã ERR không nối được với log máy chủ
- **Bằng chứng**: `JsonFormatter` production chỉ ghi `level/logger/msg` (`app.py:217-226`) — 13 chỗ
  `log.exception`/`exc_info` mất stack trace; `genh doctor` lấy `docker compose logs --tail=500` không `-t`
  (`doctor.go:175`) ⇒ không có giờ. `ERR-…` sinh ở trình duyệt (`web/src/lib/errorId.ts`), không gửi máy chủ,
  không có request-id ⇒ Boss đọc mã lỗi cũng không tra được log. Gói doctor phải chạy CLI, không có
  `auto-update.log`, `update-status.json`, revision alembic, digest ảnh đang chạy; không lọc dữ liệu nhạy cảm (log
  bridge có thể chứa SĐT/tin nhắn). Ảnh không mang nhãn phiên bản (xem 🟡3).
- **Cách sửa**: formatter thêm `ts` + `exc_info`; middleware `X-Request-ID` đưa vào mọi lỗi RFC 7807 và log, web hiện
  request-id cạnh ERR; nút Console "Tải gói chẩn đoán" (qua hộp thư `run/request/doctor.json` → `genh doctor` có
  lọc) để Boss gửi một tệp cho Claude. **Effort**: M.

#### 🟠8. Log container không giới hạn
- **Bằng chứng**: `deploy/compose.yaml` không có khối `logging:` cho dịch vụ nào; worker chạy cron mỗi phút
  (`social_schedule`, `worker.py:200`) + mỗi 10 phút; driver mặc định `json-file` của Docker không xoay vòng (tuỳ
  cấu hình daemon trên máy). Log `~/.gen-harness/logs/auto-update.log` cũng chỉ nối thêm (`content.go:27-29`).
- **Cách sửa**: anchor `x-logging: {driver: json-file, options: {max-size: 10m, max-file: "3"}}` cho mọi dịch vụ;
  `genh` cắt `auto-update.log` khi > 5 MB. **Effort**: S.

#### 🟠9. Lỗ hổng kiểm thử khác trong CI
- **Bằng chứng**: Playwright e2e của web (`apps/web/e2e`, 9 spec gồm `visual.spec.ts`) không chạy ở CI (`ci.yml:93-104`
  chỉ `vitest`); `make api-test-app-role` (chạy test dưới role `gh_app` thật, bắt GRANT/RLS thiếu) không có trong CI;
  E2E chế độ "pr" chỉ kích hoạt khi đổi `apps/genh/**`, `deploy/**`, `install.sh` (`e2e-install.yml:41-51`) — đổi
  `apps/api/**` (vd. phụ thuộc Python) hay `apps/web/Dockerfile` không được cài thử trước khi phát hành (job `images`
  chỉ build + `compose config`, không khởi động); không có quét bảo mật nào (Dependabot, CodeQL, pip-audit, npm audit,
  govulncheck, Trivy); `alembic upgrade heads` (số nhiều, `compose.yaml:54`) che giấu nhánh migration kép.
- **Cách sửa**: thêm job Playwright (mock mode), job app-role, mở rộng path filter E2E pr (hoặc chạy cho mọi PR có
  `VERSION` đổi), job "alembic heads = 1", Dependabot + `govulncheck`/`pip-audit`. **Effort**: M.

#### 🟠10. Chữ ký cosign được tạo nhưng không ai kiểm; tài liệu nói có kiểm
- **Bằng chứng**: `release.yml:454-467` ký; `install.sh:113-116` và `selfupdate.go:243-265` chỉ so SHA-256 với
  `checksums.txt` tải từ **cùng** bản phát hành (bảo vệ toàn vẹn, không bảo vệ nguồn gốc). `05-installer.md:30`:
  "và chữ ký cosign nếu có".
- **Cách sửa**: ký `checksums.txt` bằng khoá ed25519/minisign có public key nhúng trong `genh` (đơn giản hơn
  sigstore), kiểm trong `selfupdate` + `install.sh`; hoặc sửa tài liệu cho đúng. **Effort**: M (S nếu chỉ sửa doc).

### 🟡 Vệ sinh / nhỏ

| # | Phát hiện | Bằng chứng | Cách sửa | Effort |
|---|---|---|---|---|
| 🟡1 | `HANDOFF-v0.1.1.md` phình mãi: 1 053 dòng / 124 KB (tệp lớn thứ 3 repo), tên ghi v0.1.1 nhưng chứa v0.1.2→v0.1.31, **trùng mục v0.1.30** (dòng 917 và 952); `CLAUDE.md` bắt ghi tiếp vào đây ⇒ mỗi phiên tốn ~35k token nếu đọc | `docs/reports/HANDOFF-v0.1.1.md` | `CHANGELOG.md` ngắn (3–5 dòng/bản, giọng Boss) + `docs/releases/vX.Y.Z.md` chi tiết; HANDOFF chỉ giữ "hiện trạng + việc dở" ≤ 200 dòng; sửa `CLAUDE.md` | S |
| 🟡2 | README lệch code: mục "Cài đặt" vẫn nói `genh` là "dự kiến" (`README.md:18-20`), lệnh cài cho Boss chôn ở mục Nâng cấp (`:155`); "8 dịch vụ" (`:34`) nay 11; "860+ test" (`:188`) nay ~1 000; "cả bốn lệnh" (`:191`) Makefile có 5; link chết `docs/reports/HANDOFF-v0.1.2.md` (`:125`); không có runbook sự cố (cập nhật lỗi, đĩa đầy, khôi phục) | README, `05-installer.md:30,159` | Viết lại README 2 phần: "Dành cho Boss" (1 lệnh cài, nơi xem trạng thái) và "Dành cho dev"; thêm `docs/runbook.md` | S |
| 🟡3 | Số phiên bản không thống nhất: `VERSION` v0.1.31 nhưng mọi `package.json`, `pyproject.toml`, `gh/__init__.py:3` = 0.1.0 ⇒ log "Gen-Harness API 0.1.0 sẵn sàng" (`app.py:175`), OpenAPI 0.1.0; ảnh không có nhãn OCI version ⇒ không biết container đang chạy bản nào khi chẩn đoán | grep version | build-arg `VERSION` → env `GH_VERSION` + `LABEL org.opencontainers.image.version`; `/system/about` trả cả version ảnh lẫn genh | S |
| 🟡4 | `apps/genh/internal/compose/embedded_compose.yaml` trong repo lệch `deploy/compose.yaml` (thiếu browser, browser-redis, browser-egress, `gh_browser_key`, mạng `browser*` — ~100 dòng diff); chỉ được ghi đè lúc release ⇒ ai `go build` tay ra `genh` nhúng compose cũ | `diff deploy/compose.yaml …/embedded_compose.yaml` | Thay bằng stub báo lỗi rõ "chỉ dùng bản release", hoặc test CI kiểm khớp | S |
| 🟡5 | Nhánh cũ: 11 nhánh ngoài `main`. Đã squash-merge, nên xoá: `cli-models-wip`, `hardening-wip`, `hublink-wip`, `refinery-wip`, `social-wip`, `ux-wip`, `phase-1-2-nen-du-lieu` (0 ahead/113 behind). Bỏ dở: `project-thread-bnesk5` (11 ahead/114 behind, 24/09), `zen-lovelace-ph1qa2` (1 ahead/63 behind, 26/09). Đang dùng: `admiring-goodall-6dmk8k` (= main), `effort-wip` (v0.1.32). 30/100 lần CI gần nhất bị huỷ do push wip liên tục | `list_branches`, `git rev-list` | Bật "Automatically delete head branches"; `paths-ignore: docs/**` cho ci.yml | S |
| 🟡6 | GitHub Actions: cảnh báo Node 20 bị khai tử (checkout@v4, docker/*@v3/v6, setup-uv@v6); action ghim theo tag, không theo SHA (`softprops/action-gh-release@v2` có `contents: write`); `ci.yml` không khai `permissions:` | log job 109975416642 | Nâng phiên bản action, ghim SHA (Dependabot tự nâng), `permissions: contents: read` | S |
| 🟡7 | Cập nhật đêm (03:00 ±30') trùng cửa sổ cron worker 03:15/03:40/03:45 (`worker.py:195-198`) — restart worker có thể bỏ lỡ job ngày đó (arq không chạy bù) | `content.go:42-44` | Dời cron dọn dẹp sang 04:30, hoặc bỏ restart khi không có bản mới (🔴2) | S |
| 🟡8 | Tải binary `genh` timeout 20 giây cho **cả tệp** (`selfupdate.go:90`, `http.Client.Timeout`) — mạng chậm ⇒ tự cập nhật binary hỏng âm thầm, chạy tiếp bản cũ | `main.go:358-363` | Timeout theo thời gian rảnh + thử lại 3 lần | S |
| 🟡9 | Không kiểm "tự lên sau khi bật lại máy": không kiểm `docker.service` enabled; `linger` chỉ cảnh báo lúc cài (`systemd.go:246-248`), Console không hiện ⇒ nếu thiếu, timer/watcher không chạy khi Boss chưa đăng nhập | grep `is-enabled docker`: không có | `genh doctor`/`status` + Console hiện 2 mục này; cần kiểm 1 lần trên máy Owner | S |
| 🟡10 | Thư mục hộp thư `run/` để 0777 (`hostlink.go:57-66`) — người dùng khác trên máy có thể thả yêu cầu cập nhật/khôi phục (khoá restore có regex chặt, rủi ro thấp) | `hostlink.go` | Chown về uid container (10001) + 0770 thay vì 0777 | S |

---

## (d) Top 5 khuyến nghị để "tự bảo trì" thật sự

1. **Cổng an toàn cho phát hành** (🔴1 + 🟠1): phát hành dạng prerelease → E2E (thêm nâng cấp N-5 có dữ liệu mẫu) →
   chỉ khi xanh mới gắn "latest"; Release chờ CI; thêm `go test`/`go vet` vào CI; bật branch protection. Máy Owner chỉ
   nhận bản đã qua kiểm thật.
2. **Làm `genh update` "không bao giờ làm hại"** (🔴2, 🔴3, 🟠2, 🟠3): pull trước khi backup; chỉ restore khi đã
   migrate; dừng nguồn ghi trước backup khi có migration; restore bằng ảnh cũ; `flock`; bỏ qua chu trình khi không có
   bản mới; dọn ảnh cũ (giữ N-1); không tự thử lại một bản đã rollback.
3. **Watchdog + cảnh báo ra ngoài app** (🔴5, 🟠8): timer 10–15 phút kiểm ready, unhealthy, đĩa, tuổi backup, update
   lỗi, kênh rớt, CLI hết hạn → tự restart cái tự sửa được, còn lại nhắn Boss qua một kênh ngoài (và ghi Kho dữ liệu để
   Claude thấy); kèm giới hạn log container.
4. **Sao lưu ra ngoài máy tự động** (🔴4): xuất `.ghbundle` theo lịch (CSDL + tài liệu + khoá, mật khẩu riêng) sang
   Drive/USB/NAS; Console hiện "bản ngoài máy gần nhất" và cảnh báo khi quá 7 ngày; `uninstall` mặc định giữ dữ liệu.
   Định kỳ (tháng) khôi phục thử bản ngoài máy vào CSDL tạm để chứng minh dùng được.
5. **Chẩn đoán một nút + tài liệu gọn** (🟠7, 🟡1–3): nút "Tải gói chẩn đoán" trong Console (đã lọc dữ liệu nhạy
   cảm, có version ảnh/genh, alembic, update-status, log có giờ + traceback + request-id); `CHANGELOG.md` ngắn thay
   HANDOFF khổng lồ; README tách phần Boss/dev + runbook sự cố.
