# Kế hoạch tổng — đưa Gen-Harness tới bản hoàn chỉnh (từ v0.1.33)

Đầu vào: 5 báo cáo kiểm toán (`1-kien-truc.md` … `5-lo-trinh.md`), 36 phát hiện đã qua kiểm chứng đối kháng (đứng vững),
1 phát hiện bị bác (F-24), 55 phát hiện vàng chưa kiểm chứng. Dữ liệu gốc: `0-phat-hien-da-kiem.json`.
Mã nguồn đối chiếu: main v0.1.31; v0.1.32 (tách model / mức suy nghĩ + chẩn đoán CLI) sắp phát hành, nên kế hoạch bắt đầu từ v0.1.33.

---

## (a) Tình trạng tổng thể

- Phần lõi đã làm (Gen có đề xuất + xác nhận + PIN, sàng lọc, Kho, đọc Facebook, genh cài/cập nhật/khôi phục) có test và nhìn chung chắc. Các lỗi thật nằm ở **chỗ nối** giữa các phần, và ở những thứ chỉ được kiểm bằng mock.
- **Phát hành chưa có cổng chặn**: một bản có thể thành "latest" khi CI còn đỏ, rồi máy Owner tự cài lúc 03:00. Cập nhật đêm còn có thể khôi phục đè CSDL khi chỉ lỗi mạng (F-9, F-10). Vì vậy việc này phải làm đầu tiên.
- Có 5 lỗi đỏ. Ba lỗi liên quan trực tiếp tới Boss: giao việc / gán người / gán trợ lý **luôn hỏng** trên máy thật (F-1); nhân viên chiếm được phiên Owner qua tệp tải lên (F-5); hệ thống hỏng mà không báo ai (F-6). Hai lỗi còn lại là nguy cơ âm thầm: đĩa đầy dần (F-11); sao lưu và khoá giải mã chỉ nằm trên cùng một ổ (F-12).
- Gen mới là "khung chat trả lời khi được hỏi": chưa chủ động, mất hội thoại khi tải lại trang, chưa có kênh tới Boss ngoài Console. Phần lớn tích hợp mới (Gen-hub, Facebook, agy/Claude CLI, Jev) **chưa từng chạy với tài khoản thật**.
- Giao diện xếp theo kiến trúc kỹ thuật. Một số chỗ hứa mà chưa làm (dữ liệu mẫu, 4 dòng gán model chết, "hạn lưu dữ liệu" không ai thi hành).

## (b) Nguyên tắc sắp xếp

1. **An toàn phát hành trước hết.** Máy Owner chỉ nhận bản đã qua CI + E2E cài thật, và cập nhật đêm không bao giờ làm hại dữ liệu. Chừng nào chưa có cổng này, mọi bản sửa sau đều có thể làm hỏng máy Owner.
2. **Xếp theo giá trị cho Boss chia công sức.** Lỗi đỏ đi trước, kế đến là lỗi làm Boss hiểu sai hoặc không biết hệ thống hỏng, rồi tới tính năng trợ lý.
3. **Mỗi đợt là một bản phát hành, ≤ 1–2 ngày công**, chỉ một mục tiêu. Đợt nào cũng có tiêu chí nghiệm thu kiểm được bằng test tự động (pytest / vitest / go test / Playwright / e2e-live / E2E cài thật). "Xem bằng mắt" không tính là tiêu chí.
4. **Chặn tái phát chứ không chỉ sửa một chỗ.** Lỗi loại "mock xanh, máy thật hỏng" thì phải có test trên API thật, và mock phải kiểm dữ liệu giống API thật.
5. **Một nguồn sự thật.** Không thêm bản sao, công tắc hay menu thứ hai. Gộp lại, không mở thêm.
6. **Dùng better_fix khi đã có.** Cách sửa ghi dưới đây là cách cuối cùng sau kiểm chứng, không phải đề xuất gốc của người kiểm toán.
7. **Không bắt Boss làm việc kỹ thuật.** Việc cần Boss chỉ là bấm hoặc đăng nhập tài khoản của chính Boss, và ghi rõ ở từng đợt.
8. **Luật cứng giữ nguyên** (không thuộc quyền chọn của Owner): không tài khoản giả, không né chống bot, không lộ bí mật vào log hay kết quả. QD-12 vẫn áp dụng: cung cấp công cụ, Owner tự quyết rủi ro sau khi được cảnh báo.

Quy ước công sức: **S** ≈ ½–1 ngày · **M** ≈ 1½–2 ngày.

---

## (c) Các đợt

### v0.1.33 — Cổng phát hành & CI đủ test · M (~2 ngày)

**Mục tiêu:** máy Owner chỉ nhận bản đã qua CI + E2E cài thật. (Phần "cập nhật không làm hại" tách sang v0.1.34, xem mục g.)

Việc:
- **F-9 — Cổng phát hành (tự động, không cần người duyệt).**
  1. `ci.yml` thêm `on: workflow_call`. `release.yml` gọi nó thành job `ci` và đưa job này vào `needs` của job release.
  2. softprops chạy với `prerelease: true` và `make_latest: false`. `/releases/latest` vốn bỏ qua prerelease, nên genh, `install.sh` và `system_api/update.py:72` sẽ không thấy bản này.
  3. `e2e-install.yml` ở chế độ release kiểm **đúng tag** lấy từ `workflow_run.head_sha`. Nếu xanh, job cuối chạy `gh release edit $TAG --prerelease=false --latest` (permissions `contents: write`). Thêm `workflow_dispatch` "promote tay" cho người bảo trì khi E2E lỗi vì lý do ngoài mã.
  4. Thời gian chín: `selfupdate.latestTag` đọc `published_at` và bỏ qua bản dưới 24 giờ, **chỉ áp cho timer đêm (`--yes`)**. Nút "Cập nhật ngay" Boss tự bấm vẫn lấy bản mới nhất.
  5. Bật branch protection cho `main` với các required check (api, web, genh-go, installer) và bảo vệ tag `v*`. Không đặt người duyệt bắt buộc, vì quy trình tự merge khi CI xanh vẫn giữ.
     - Required check chỉ được là job **luôn chạy** trên mọi PR (không lọc đường dẫn). Nếu sau này cần lọc đường dẫn thì thêm một job tổng `ci-ok` luôn chạy và chỉ bắt buộc job đó; nếu không, PR chỉ sửa tài liệu sẽ kẹt "Expected — waiting" mãi.
     - Luật bảo vệ tag `v*` phải cho phép `github-actions[bot]` (release.yml) tạo tag, nếu không chính bước phát hành bị chặn.
     - `ci.yml` có `concurrency: ci-${{ github.ref }}` + `cancel-in-progress: true`. Khi được gọi qua `workflow_call` từ release.yml trên `main`, nhóm này trùng với lần CI chạy do push, nên hai bên huỷ lẫn nhau. Phải đổi nhóm thành `ci-${{ github.workflow }}-${{ github.event_name }}-${{ github.ref }}` (hoặc bỏ concurrency khi `workflow_call`).
  6. Sửa `docs/ROADMAP.md:3` và `docs/handoff/05-installer.md:30` cho đúng thực tế: genh **chưa** kiểm cosign. Việc kiểm cosign/minisign để sau (xem mục d).
- **F-13 — CI chạy đủ bộ test đã có.** Thêm `go vet ./...` và `go test ./...` vào `installer-matrix.yml` (4 hệ điều hành, đã có setup-go). Bước pytest thêm `GH_TEST_APP_ROLE: "1"`. Job web thêm `npx playwright test` (mock). Thêm bước kiểm `alembic heads` chỉ có đúng 1 head. Mở rộng bộ lọc đường dẫn của E2E chế độ pr để gồm `apps/api/**`, `apps/web/Dockerfile`, `deploy/images/**` và `VERSION`. (Phần "quét bảo mật" của F-13 làm ở v0.1.48 cùng Renovate.)

Tiêu chí nghiệm thu:
- Release v0.1.33 ban đầu là prerelease, và `gh api repos/:o/:r/releases/latest` vẫn trả v0.1.32. Sau khi E2E xanh, v0.1.33 tự thành latest. Job promote tự in kết quả `releases/latest` trước/sau vào log (bước kiểm có `exit 1` nếu sai).
- Một PR thử cố ý làm đỏ CI không merge được (branch protection). Một PR thử chỉ sửa `docs/` vẫn merge được. Đóng/xoá cả hai sau khi kiểm.
- Lần chạy Release trên `main` không bị huỷ (không có job `ci` ở trạng thái cancelled).

Kiểm thử bắt buộc:
- `go test ./...` đã chạy trong CI.
- Test cho `selfupdate`: bỏ qua bản dưới 24 giờ khi chạy `--yes`; nút "Cập nhật ngay" không bị chặn.
- E2E-install (chế độ release + upgrade) phải xanh trước khi promote.

Boss: nếu token của Claude không có quyền admin repo thì cần bấm bật branch protection một lần (~2 phút, Claude gửi đường dẫn). Ngoài ra không cần làm gì.

---

### v0.1.34 — Cập nhật đêm không làm hại dữ liệu · M (~2 ngày)

**Mục tiêu:** lần cập nhật 03:00 không khôi phục đè CSDL khi chưa đụng tới CSDL, không chạy khi không có bản mới, không làm đầy đĩa, và khi bản mới crash thì rollback **thật sự** về bản cũ, không lặp lại mỗi đêm. Đây là nửa thứ hai của "an toàn trước hết": phải xong trước mọi bản sửa app.

Việc:
- **F-10 — Cập nhật không khôi phục đè khi chưa đụng CSDL.**
  0. Đặt timeout cho `docker compose pull` và thử lại 3 lần, có backoff.
  1. Pull **trước** backup: ghi compose nhúng ra tệp tạm cùng thư mục rồi chạy `docker compose -f <tmp> pull`. Không đồng bộ `compose.yaml` sớm, để không tái phát lỗi #3 ở `update.go:100-110`. Pull lỗi thì trả `OpError` "chưa đụng gì" và không rollback.
  2. `rollbackAndWrap` thêm tham số `dbTouched`, chỉ đặt true từ bước migrate trở đi. Khi false thì chỉ khôi phục `compose.yaml.bak` rồi chạy `up -d`, không gọi `restoreInContainer`.
  3. `main.go`: khi không có bản mới **và** compose nhúng trùng từng byte với tệp trên đĩa thì bỏ qua `RunUpdate`, ghi hostlink `done`, không backup, không pull.
  4. Khi bản mới có migrate: dừng `worker` và `bridge` (nguồn ghi) **trước** bản backup pre-update, để dữ liệu ghi giữa lúc backup và migrate không bị mất khi phải restore.
- **F-11 — Đĩa không đầy vì ảnh cũ.**
  - Trước bước pull: dùng lại `machine.probeDiskFree`/`CheckDisk` (export ra). Nếu chỗ trống dưới `MinDiskBytes` thì dọn ảnh cũ trước. Vẫn thiếu thì dừng với `OpError` và ghi trạng thái vào `run/`. (Chuông "đĩa sắp đầy" đọc trạng thái này ở v0.1.36.)
  - Sau khi update thành công, và cả trong nhánh rollback: xoá các ảnh `ghcr.io/<owner>/gen-harness-*` không thuộc digest của bản hiện tại hay bản liền trước (lấy từ compose nhúng và `compose.yaml.bak`).
- **F-37 (chuyển từ đợt tự lành)** — anchor `x-logging` (json-file, max-size 10m, max-file 3) cho cả 12 dịch vụ. Đổi healthcheck của web sang `/healthz`. Thêm test trong `compose_test.go`: mọi service đều có `logging.max-size`. Lý do: log container không giới hạn cũng làm đầy đĩa, cùng nhóm với F-11.
- **F-33 lõi (chuyển từ đợt tự lành)** — rollback dùng ảnh cũ. Cùng hàm `rollbackAndWrap` với F-10, nên sửa chung một lần. Sau `restoreComposeFromBackupIfAny`:
  1. `docker compose stop api worker bridge web` với compose cũ.
  2. **Luôn** restore bằng `run --rm --no-deps api` (ảnh cũ). Thêm tham số ép oneOff thay cho việc đoán theo chuỗi lỗi.
  3. Chạy `up -d --remove-orphans`.
  4. Coi `is restarting` như không chạy trong `isServiceNotRunning`.
  5. Ghi `run/update-blocked.json {version}` khi rollback, để timer không thử lại đúng bản đó nhưng vẫn lên được bản mới hơn.
- **F-35 phần lõi (chuyển từ đợt tự lành)** — e2e-upgrade nạp dữ liệu mẫu bằng `docker compose exec -T api python -m gh.seed_demo seed` trước khi nâng cấp từ `tags[1]`, rồi đếm số dòng các bảng chính trước và sau. Thêm job **"bản hỏng cố ý"**: api chạy `command: ["false"]`; kiểm `ready` xanh trở lại bằng bản cũ và số dòng giữ nguyên. Lý do: cổng E2E ở v0.1.33 chạy trên CSDL rỗng, không bắt được migration hỏng trên dữ liệu thật; F-33 không có job này thì không chứng minh được.

Tiêu chí nghiệm thu:
- Chạy `genh update --yes` khi đã ở bản mới nhất: không có bản backup mới, không chạy lệnh pull, thoát 0. Bước "đã mới nhất" của E2E chứng minh điều này.
- Sau E2E nâng cấp, `docker images` chỉ còn ảnh của bản hiện tại và bản liền trước.
- e2e-upgrade có dữ liệu từ `tags[1]` xanh, số dòng các bảng chính không đổi.
- Job E2E "bản hỏng cố ý" xanh: hệ thống quay về bản cũ, số dòng không đổi, `run/update-blocked.json` có đúng version hỏng.

Kiểm thử bắt buộc:
- Unit test mới trong `ops/update_test.go`, mỗi nhánh một test: pull lỗi thì **không** restore (sửa test cũ ở :167); migrate lỗi thì **có** restore; trùng bản thì bỏ qua; đĩa thiếu thì dừng; dọn ảnh giữ đúng 2 bản; có migrate thì worker/bridge dừng trước backup.
- Runner giả trả "is restarting" thì phải gọi `run --rm`. Bản bị chặn không được thử lại ở lần chạy `--yes` kế tiếp, nhưng bản mới hơn vẫn được nhận.
- `compose_test` cho logging.

Review trước merge dùng Opus (sửa lưới an toàn). Boss: không cần làm gì.

---

### v0.1.35 — Sửa lỗi đỏ trong ứng dụng · M (~2 ngày)

**Mục tiêu:** giao việc, gán người và gán trợ lý chạy được với người và trợ lý thật; nhân viên không còn chiếm được phiên Owner qua tệp tải lên.

Việc:
- **F-1 — Bỏ ID giả viết cứng.**
  - Thêm `GET /api/v1/pickers/users` (quyền `queue.act` hoặc `opportunity.write`; tái dùng SQL ở `gen/routes.py:214-218`; chỉ trả id + tên của người đang hoạt động).
  - Thêm `GET /api/v1/pickers/agents` (quyền `profile.write`; chỉ trả id + tên).
  - Không dùng `/users` (cần `roles.manage`), `/gen/assignees` (trả 403 khi vai trò chưa bật Gen) hay `/agents` (cần `system.read` ALL).
  - Thêm hook dùng chung `useAssignees()` / `useAgentOptions()` cho `InboxScreen`, `DealsScreen`, `DirectoryScreen`, và cho bộ lọc người phụ trách `graph/graphModel.ts:99-103`. "Tôi" lấy `me.id`.
  - Mock dùng UUID và trả 422 khi nhận mã không phải UUID.
  - Thêm bước kiểm tĩnh trong CI: cấm chuỗi `id: 'u-` và `'agent-` trong `apps/web/src`.
- **F-5 — Chặn XSS qua Tài liệu.**
  - Thi hành **lúc phục vụ tệp** (`relations/routes.py:783-784`), vì các dòng cũ đã lưu mime tuỳ ý. Chỉ cho mở inline với `application/pdf` và `image/png|jpeg|webp|gif`. Mọi mime khác ép `application/octet-stream` kèm `attachment`.
  - Mọi phản hồi tệp luôn có `Content-Security-Policy: sandbox; default-src 'none'`.
  - Caddyfile:16: thêm `script-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'`, bỏ `wss:` khỏi `connect-src`.
- **F-20 (phần gấp)** — đóng kênh tuồn dữ liệu chính: thêm thao tác PIN `ai.route_change` cho tạo/sửa nhà cung cấp, sửa chuỗi chuyển hướng và thêm khoá. Phần còn lại làm ở v0.1.45.
- **F-14 (phần chặn tái phát)** — chạy `apps/web/e2e-live/run.sh` bản rút gọn (đã có fake_bridge và fake_llm) trong job api của `ci.yml`, job này đã có Postgres và Redis. Thêm 4 luồng: giao việc ở Hộp thư, gán người cho Vụ việc, gán BOT cho nhóm, xác nhận đề xuất của Gen. Thêm một test pytest chung: mọi problem+json có `detail` là chuỗi. Việc sinh type từ OpenAPI để sau.
- **F-15 — Sổ tay vượt phạm vi.** Ở `data_api/routes.py:709-710`, đổi `NB_READ` thành `data.read` và `NB_WRITE` thành `data.manage`, cho khớp phần còn lại của router.
- **F-43 — Lỗi rò thông tin kỹ thuật.**
  - `db_error_handler` chỉ trả 503 cho `OperationalError`/`InterfaceError`. Các `DBAPIError` khác thì ghi `log.exception` và trả 500 INTERNAL, không đưa `str(exc)` ra ngoài.
  - Tách `ConnectionError`/`TimeoutError` khỏi phần còn lại của `OSError`.
  - Tắt `/docs` ở production.
  - Che email trong dòng nhật ký đăng nhập thất bại.

Tiêu chí nghiệm thu:
- e2e-live chạy trong CI và xanh với 4 luồng trên API thật. Ba luồng giao/gán phải trả 200 và lưu đúng UUID.
- Tải lên tệp `text/html` và `application/javascript` rồi mở: nhận `attachment` + `octet-stream` + CSP sandbox.
- Bước grep trong CI không còn tìm thấy ID giả.
- AgentNV gọi `/notebooks/person/{ngoài phạm vi}` nhận 403.
- Một `IntegrityError` trả 500 và không có SQL trong thân phản hồi.
- Tạo nhà cung cấp mà không có PIN thì nhận 423.

Kiểm thử bắt buộc:
- pytest: `test_p3_relations.py` (hồi quy mime), test sổ tay theo vai trò, test `db_error_handler`, test bảng 423 cho nhóm providers.
- vitest cho các hook picker.
- e2e-live chạy trong CI.

Boss: không cần làm gì.

---

### v0.1.36 — Hệ thống tự báo khi hỏng (trong app) + sao lưu chắc · M (~2 ngày)

**Mục tiêu:** các sự cố hay gặp đều thành một chuông có nút sửa, và đầu Tổng quan có dải "Cần Sếp xử lý". Log đủ thông tin để Claude tra ra lỗi.

Việc:
- **F-6 bước 1 — báo ngay ở chỗ trạng thái đổi**, nhờ vậy tự khử trùng lặp.
  - (a) `data/ingest.py`, nhánh `session.ended` khi reason ∈ {expired, error, logged_out}: gọi `notify(owner_ids, kind='channel.down', link='/system?tab=channels')`.
  - (b) `providers/router.py` `_set_auth_state`: khi `rowcount>0` và state='expired' thì gửi `model.auth_expired`.
  - (c) **Không** đưa worker vào `/ready`, vì genh dùng `/ready` để quyết rollback (`update.go:248`). Thay vào đó thêm `GET /system/health`: đọc khoá health-check của arq, `gh:browser:heartbeat`, độ dài từng stream `.dlq`, và lần chạy cuối của từng cron (ghi Redis khi job xong). Kết quả hiện thành thẻ "Sức khoẻ hệ thống" ở Điều khiển hệ thống.
  - (d) Chuông khi `update-status` = failed/rolled_back, khi bản sao lưu mới nhất cũ hơn 36 giờ, khi worker im quá 10 phút, và khi genh ghi trạng thái "đĩa thiếu chỗ" vào `run/` (từ v0.1.34).
  - (e) Dải "Cần Sếp xử lý" đầu Tổng quan, gộp NoModelBanner, kênh rớt và cập nhật lỗi.
- **F-3 — sao lưu theo lịch không chết âm thầm.**
  - `backup.py:443` thêm `"timeout": 3600`.
  - `scheduled_backup_scan` bắt `asyncio.CancelledError`: gửi chuông rồi raise lại. `_run` gọi `proc.kill()` khi bị huỷ, để không còn pg_dump mồ côi.
  - Thêm kiểm tra "bản mới nhất, tính cả pre-update, cũ hơn 36 giờ" → chuông P1. Kiểm tra này dùng chung với (d) ở trên.
  - Ghi dump theo luồng: để sau.
- **F-4 bước 1 — log tra được.** `JsonFormatter` thêm `ts`, `exc` (`formatException`) và `stack_info`. Thêm handler `Exception` chung: ghi `log.exception` kèm method/path, trả problem+json INTERNAL. `doctor.go` chạy `docker compose logs` thêm `-t`.
- **F-2 (tạm)** — StorageTab ghi rõ "Chưa tự xoá — sẽ áp dụng ở bản sau" và khoá nút Sửa. Job thật làm ở v0.1.40.
- **F-46** — truyền build-arg `VERSION` thành `GH_VERSION` và thêm `LABEL org.opencontainers.image.version`. `gh/__init__.py` đọc `GH_VERSION`. `/system/about` trả cả version ảnh lẫn version genh.
- **F-45** — đặt timezone cho WorkerSettings rõ ràng (Asia/Ho_Chi_Minh). Dời các job nặng/dọn dẹp ra khỏi giờ làm việc và khỏi cửa sổ cập nhật 03:00 ±30' (ví dụ sang 04:30).

Tiêu chí nghiệm thu:
- Phiên Zalo hết hạn → đúng 1 chuông `channel.down`. Sự kiện lặp lại không sinh chuông thứ hai.
- Model hết hạn → 1 chuông.
- `/system/health` báo worker còn sống hay đã im.
- Playwright (mock) cho thấy dải "Cần Sếp xử lý" khi có sự cố.
- `/system/about` và log khởi động trả đúng chuỗi trong tệp `VERSION` (pytest so với tệp, không viết cứng số bản).
- Có tệp trạng thái "đĩa thiếu chỗ" trong `run/` thì sinh đúng 1 chuông.
- Một lỗi 500 cố ý tạo ra có traceback và `ts` trong log JSON.

Kiểm thử bắt buộc:
- pytest cho (a), (b), (d) và khử trùng lặp.
- Test `JOBS` của backup có timeout 3600.
- Test `CancelledError` → có chuông.
- Test formatter có `exc`.
- vitest/Playwright cho dải trên Tổng quan.

Boss: không cần làm gì.

---

### v0.1.37 — Cập nhật tự lành (phần còn lại) · S–M (~1½ ngày)

**Mục tiêu:** genh không kẹt trạng thái "running", không chết giữa chừng, tự cập nhật không hỏng vì mạng chậm, và máy tự lên lại sau khi bật. (F-33 lõi, F-37 và kiểm nâng cấp có dữ liệu từ `tags[1]` đã chuyển lên v0.1.34.)

Việc:
- **F-34 — khoá loại trừ và tín hiệu dừng.**
  - Dùng `signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)`. Rollback chạy bằng `context.WithoutCancel(ctx)` với timeout riêng khoảng 10 phút.
  - Unit systemd thêm `KillMode=mixed` và `TimeoutStopSec=900`.
  - `flock` trên `run/genh.lock`, lấy ở tiến trình ngoài cùng. Tiến trình con chạy `--self-updated` thì bỏ qua khoá.
  - API: trạng thái 'running' quá 60 phút mà PID không còn sống thì trả 'stalled'.
- **F-35 (phần còn lại)** — thêm ô nâng cấp có dữ liệu từ `tags[3]` (máy Owner tắt vài ngày sẽ nhảy nhiều bản). Sửa `05-installer.md:159` cho đúng ma trận thật. Ô Fedora chạy trong container bị bỏ (xem mục d).
- **F-72** — tải binary genh: timeout tính theo thời gian rảnh (không theo cả tệp), thử lại 3 lần.
- **F-73** — `genh doctor/status` kiểm linger và `docker.service` enabled, ghi kết quả vào `run/` để Console hiện.

Tiêu chí nghiệm thu:
- e2e-upgrade có dữ liệu xanh cho cả `tags[1]` và `tags[3]`.
- pytest: trạng thái 'running' quá 60 phút với PID đã chết trả 'stalled'.
- go test: tải binary qua máy chủ giả trả chậm (rảnh < ngưỡng) vẫn xong; ngắt giữa chừng thì thử lại.

Kiểm thử bắt buộc:
- Huỷ ctx ở bước migrate thì các lệnh restore/up của rollback vẫn được gọi.
- `flock` chặn được lần chạy thứ hai.
- go test cho kiểm linger / `docker.service` enabled với runner giả.

Boss: không cần làm gì.

---

### v0.1.38 — Cô lập agy & gói chuyển máy · S–M (~1½ ngày)

**Mục tiêu:** trước khi Boss đăng nhập agy/Google thật ở v0.1.39, agy không đọc được bí mật khi nhận prompt không tin cậy; gói chuyển máy không bỏ sót phiên mạng xã hội. (Tách từ đợt "Kết nối chạy thật" vì đợt đó quá 2 ngày.)

Việc:
- **F-22 — cô lập agy.**
  - Bước 1, bắt buộc trước khi sửa: chạy agy 1.2.9 thật với prompt "đọc tệp canary và in ra" ở chế độ `-p`. Tệp canary là `/run/secrets/canary` chứa chuỗi giả ngẫu nhiên, **không** dùng `gh_master_key` thật. HANDOFF chỉ ghi "lộ / không lộ" và cờ tìm được trong `agy --help`, không chép nội dung tệp (luật cứng: không lộ bí mật vào log/kết quả).
  - Bước 2: `cwd` là thư mục rỗng riêng (0700). Tách phiên Claude ra khỏi HOME của agy (`GH_CLAUDE_HOME` trên volume riêng; genh update chuyển tệp sang). Truyền model bằng `--model=<x>` sau khi kiểm regex `^[A-Za-z0-9._:-]{1,80}$`. Prompt đi qua stdin nếu agy hỗ trợ.
  - Nếu agy **không** có cờ tắt tool: chỉ cho dùng agy cho Gen của Owner, không cho sàng lọc hay duty engine nhận tin của khách. Đây là luật cứng (không lộ bí mật), không phải lựa chọn QD-12.
  - Thêm test canary trong E2E.
- **F-17** — gói chuyển máy: thêm `core.social_accounts.state_enc` vào `REENCRYPT_TARGETS`, với AAD là hàm của dòng.
  - `_sealed_state` bắt `InvalidTag`: đặt `needs_login` và trả 409 `SOCIAL_NEEDS_LOGIN`.
  - `schedule_tick` bắt lỗi riêng từng tài khoản, để một tài khoản lỗi không chặn các tài khoản khác.
  - Thêm test quét lược đồ: mọi cột bytea `*_enc` đều có trong `REENCRYPT_TARGETS`.

Tiêu chí nghiệm thu:
- Test canary agy (E2E): đầu ra và log không chứa chuỗi canary.
- pytest: import gói sang khoá khác thì social báo `needs_login` (409), không còn 500.
- pytest quét lược đồ `*_enc` xanh.

Kiểm thử bắt buộc: pytest cho bundle và social; unit test cho `AgyClient._run` (cwd, regex model); E2E canary.

Boss: không cần làm gì.

---

### v0.1.39 — Kết nối chạy thật cùng Boss · M (~2 ngày, gồm ~20 phút của Boss)

**Mục tiêu:** mọi tích hợp đã có (Gen-hub, Facebook, Google/agy với cả việc đổi tài khoản, Claude CLI, Jev) được chứng minh chạy với tài khoản thật của Boss. Boss tìm thấy chỗ nối ở đúng nơi.

Việc:
- **Trang "Việc Boss cần làm"** trong Hướng dẫn thiết lập, khoảng 20 phút, mỗi dòng là một nút:
  1. Dán token Gen-hub → Kiểm tra.
  2. Đăng nhập Facebook **ngay trong app** → Đọc ngay.
  3. Đăng nhập Google/agy → Gọi thử → thêm tài khoản Google thứ hai → đổi qua lại.
  4. Đăng nhập Claude Code CLI → Gọi thử.
  5. (Tuỳ chọn) Khoá Jev → Kiểm tra.
- **F-74, F-89** — nghiệm thu Gen-hub thật. Chỉnh quy tắc che dữ liệu Kho theo dữ liệu thật. Kiểm G1–G4 phía Gen-hub (expires_at, 2 token song song).
- **F-75** — nghiệm thu Facebook thật: sửa selector theo trang thật, chạy thử ảnh arm64.
- **F-76** — đổi tài khoản Google/agy **phải chạy**. Đối chiếu định dạng `agy models` khi đã đăng nhập, và mã model Claude qua Antigravity.
- **F-77** — Claude Code CLI: đăng nhập tới cuối, kiểm tệp `.credentials.json`. Ghi lại **dạng** mã đăng nhập thật (bộ ký tự, độ dài; không ghi giá trị) để đối chiếu regex F-56 ở v0.1.45, tránh regex chặn nhầm mã thật.
- **F-78** — Jev: kiểm đúng 1 lần với khoá thật. Lỗi thì ẩn thẻ Jev, không làm riêng thêm (QD-10).
- Mỗi nút "Kiểm tra / Gọi thử / Đọc ngay" trên trang "Việc Boss cần làm" **lưu kết quả** (đạt/lỗi, mã lỗi, thời điểm; không lưu bí mật) vào CSDL, hiện ngay cạnh dòng đó, và có trong gói chẩn đoán. Claude đọc kết quả này thay vì hỏi Boss.
- **F-32** — đưa màn `social` vào danh mục màn: hiện trên thanh bên cho Owner, và Gen điều hướng tới được. Thêm thẻ "Facebook → Mở trang tài khoản mạng xã hội" ở Hệ thống › Kênh.
- **F-31 — Gen-hub dễ nối.**
  - Khi endpoint là https công khai, UI bật sẵn công tắc mạng công cộng và hiện cảnh báo. Owner vẫn thấy và tự bỏ được (QD-12). Máy chủ không tự bật.
  - `_classify` đổi lỗi `MCP_NETWORK_BLOCKED` thành câu "Bật 'Cho phép Gen-hub ở mạng công cộng' ngay trong thẻ này".
  - Nút Kiểm tra tự lưu trước khi kiểm, gói trong một lần nhập PIN.
- **F-28** — gom về một tên "Hướng dẫn thiết lập". Sửa việc 10 trỏ `/system?tab=users`. Thêm việc Facebook (`/social`) và Gen-hub (link tới console). Backend thêm truy vấn nhận biết đã xong (có tài khoản social, có hub_link), để thẻ Tổng quan không nhắc mãi. Telegram để sau.

Tiêu chí nghiệm thu:
- pytest: bấm Kiểm tra (với dịch vụ giả đạt/lỗi) lưu đúng kết quả từng dòng; bản ghi không chứa token/mật khẩu.
- Nghiệm thu thật: các dòng bắt buộc (1–4) trên máy Owner có kết quả "đạt" trong CSDL; Claude chép bảng kết quả (không bí mật) vào HANDOFF.
- Đổi tài khoản Google 2 lần: lần gọi thử sau mỗi lần đổi báo đúng tài khoản. Có test tự động với agy giả.
- vitest: HubLinkCard bật sẵn công tắc khi nhập URL công khai.

Kiểm thử bắt buộc:
- pytest cho kết quả kiểm tra của trang "Việc Boss cần làm".
- vitest cho HubLinkCard và guide.
- Playwright mock cho mục social trên thanh bên.

Boss phải làm: khoảng 20 phút, theo trang "Việc Boss cần làm". Đăng nhập tài khoản **của chính Boss**, tạo token trên Gen-hub. Jev là tuỳ chọn.

---

### v0.1.40 — Dữ liệu an toàn: bản sao ngoài máy + hạn lưu thật · M (~2 ngày)

**Mục tiêu:** hỏng ổ đĩa không làm mất hết dữ liệu. "Hạn lưu dữ liệu" trên màn là thật. Các job nền không chậm dần theo lịch sử.

Việc:
- **F-12 — bản sao ngoài máy.**
  - Không làm tích hợp S3. Dùng lại `genh export` (`bundle.py` đã gói CSDL, tệp và khoá, mã hoá argon2id). Thêm lịch xuất hằng tuần qua cùng bộ hẹn giờ của autoupdate (systemd/launchd/schtasks) ra một đường dẫn Owner chọn (USB hoặc NAS đã mount).
  - Sau mỗi lần xuất, genh tự kiểm gói vừa ghi: giải mã được và `pg_restore --list` đọc được. Lỗi thì coi như chưa có bản sao ngoài máy và báo. (Khuyến nghị #4 của báo cáo vận hành: chứng minh bản sao dùng được, không chỉ "có tệp".)
  - Đích chưa mount (USB rút ra) thì ghi lỗi rõ "chưa thấy ổ USB/NAS", không ghi vào thư mục rỗng trên ổ chính.
  - Ghi thời điểm xuất gần nhất vào `run/`. Console hiện "Bản sao ngoài máy gần nhất" và cảnh báo khi quá 7 ngày; cảnh báo này nối vào dải "Cần Sếp xử lý".
  - Thêm nút "Tải gói mang đi" (cần Owner + PIN) và "Bộ khôi phục" (khoá in ra hoặc QR).
  - Thu `Literal destination` của API về `'local'`.
  - `uninstall` mặc định `--keep-data`.
- **F-2 — hạn lưu thật.**
  - `raw.events`, `clean.meaning_units` và `agent.model_calls` đã phân vùng: chỉ cần ghi `keep_days` vào `partman.part_config.retention` (`partition_maintenance` đã chạy mỗi giờ).
  - Các bảng thường xoá theo lô; `browser_jobs.result` giữ 14 ngày.
  - Gộp 3 kiểu purge rải rác thành một job `gh/retention.py`.
  - `ops.action_log` làm sau cùng vì vướng chuỗi băm; tạm ghi "không áp dụng".
  - Bỏ nhãn tạm của v0.1.36.
- **F-16 — job nặng.**
  - `identity/service.py`: thêm `NOT EXISTS` trên `identity_merge_candidates` trước `LIMIT`. Đây là lỗi logic: hiện tại đề xuất mới có thể ngừng hẳn.
  - Giới hạn vế a theo watermark. Dùng `a.name % b.name` với `set_limit(0.55)` và chỉ mục `gin(lower(display_name) gin_trgm_ops)`.
  - `graph/jobs.py`: thêm cửa sổ thời gian `occurred_at`, gộp upsert thành `INSERT … SELECT … ON CONFLICT`.
  - Job nào timeout 2 lần liên tiếp thì gửi chuông.

Tiêu chí nghiệm thu:
- E2E: chạy lịch xuất ra thư mục tạm, rồi `genh import` gói đó vào một cài đặt mới, số dòng khớp. Đây là thử khôi phục thật.
- pytest: đặt keep_days thì `partman.part_config.retention` được ghi.
- Purge theo lô xoá đúng phần quá hạn.
- 2001 cặp cũ cộng 1 cặp mới thì `detect()` > 0.

Kiểm thử bắt buộc:
- go test cho lịch xuất (systemd/launchd/schtasks).
- pytest retention và identity.
- E2E export → import.

Boss: cắm một ổ USB hoặc chọn thư mục NAS **một lần** rồi bấm "Chọn nơi lưu bản sao ngoài máy". Cất "Bộ khôi phục" (bản in) ở chỗ an toàn.

---

### v0.1.41 — Gen trợ lý thật, lát 1: nhớ hội thoại, bản tin, chấm điểm, chi phí · M (~2 ngày)

**Mục tiêu:** Gen tự báo Boss mỗi sáng và chiều, không còn quên hội thoại. Boss thấy Gen tốn bao nhiêu tiền và hữu ích tới đâu, để chọn nguồn model (CLI / OpenRouter / Gemini / Jev) bằng số liệu.

Việc:
- **F-8 (a) — giữ hội thoại.** Ở `genStore.ts:65`, `partialize` thêm `conversationId`. Khi GenPanel mount mà có `conversationId` và chưa có tin nhắn thì gọi `loadConversation`. Thêm danh sách "Hội thoại cũ" dùng `api.gen.conversations`.
- **F-8 (b) — "Bản tin Gen" lúc 07:30 và 17:30 giờ VN.** Job worker gom: việc đến hạn, khách nóng, nháp chờ duyệt, sự cố cần Sếp (từ v0.1.36), Facebook mới (nếu bật), Kho có gì mới. Gửi thành 1 thẻ chuông; bấm vào thì mở Gen với ngữ cảnh đó.
- **F-86** — việc nền (bản tin, sàng lọc, đọc theo lịch) **mặc định** chạy bằng **khoá API**; CLI cá nhân mặc định chỉ dùng khi Owner hỏi trực tiếp. Theo QD-12 đây là rủi ro của Owner (điều khoản gói Pro/Max), không phải luật cứng: nếu Owner thêm nguồn CLI vào chuỗi việc nền thì hiện cảnh báo rõ rủi ro và chỉ lưu sau khi Owner xác nhận + PIN; không chặn cứng.
  - **Chưa có khoá API nào** (Boss chưa dán OpenRouter/Gemini): bản tin vẫn gửi đúng giờ phần **không cần model** (danh sách việc đến hạn, nháp chờ, sự cố, số Facebook mới) kèm một dòng "Dán khoá OpenRouter/Gemini để Gen tóm tắt" dẫn tới Bộ não AI. Không im lặng bỏ bản tin.
- **F-84 (phần ưu tiên)**:
  - Nút Hữu ích / Không hữu ích trên mỗi câu trả lời và bản tin của Gen.
  - Bảng giá theo model. Quy `agent.model_calls` ra VND/ngày theo từng agent, hiện trên Tổng quan › Sức khoẻ. Trần ngân sách/ngày, vượt trần thì gửi chuông.
  - Mẫu nhà cung cấp **OpenRouter** (`openai_compat`, endpoint `https://openrouter.ai/api/v1`). e2e-live thêm luồng "nối model": tạo nhà cung cấp từ mẫu (với fake_llm theo giao thức OpenAI) → gọi thử → thấy trong chuỗi.
  - Bộ 10–15 câu hỏi chuẩn và quyết định giữ/bỏ Jev: **chuyển sang việc song song** (script, không tăng VERSION) để đợt này vừa 2 ngày. Kết quả ghi vào ghi chú phát hành bản kế tiếp.

Tiêu chí nghiệm thu:
- Playwright: tải lại trang thì hội thoại vẫn còn.
- pytest: job bản tin tạo đúng 1 thông báo mỗi khung giờ (chạy lại job cùng khung giờ không sinh thông báo thứ hai), có đủ các mục, chạy bằng nguồn không phải CLI.
- pytest: không có khoá API nào thì bản tin vẫn tạo, có các mục không cần model và dòng nhắc dán khoá; không gọi CLI.
- pytest: lưu chuỗi việc nền có nguồn CLI mà không PIN/xác nhận thì nhận 423; có PIN + xác nhận thì lưu được.
- Đánh giá Hữu ích được lưu.
- Tổng VND/ngày khớp với dữ liệu mẫu `model_calls`.

Kiểm thử bắt buộc:
- vitest cho genStore.
- pytest cho bản tin (fake_llm và không có model), tính chi phí và trần ngân sách.
- e2e-live luồng "nối model" OpenRouter.

Boss: (tuỳ chọn) tạo khoá OpenRouter hoặc Gemini và dán vào Bộ não AI, để bản tin không phải dùng CLI. Bấm Hữu ích / Không hữu ích khi đọc bản tin.

---

### v0.1.42 — "Chế độ Boss": một menu gọn theo việc · M (~2 ngày)

**Mục tiêu:** Boss không phải nhớ "cái gì nằm ở đâu". Việc quản trị không còn nằm trong "Kỹ thuật · Backend". Mỗi thứ chỉ có một chỗ. "Chế độ Boss" Boss yêu cầu **chính là menu mặc định duy nhất** này (không có công tắc); phải nói rõ với Boss điều này trong ghi chú phát hành.

Việc:
- **F-7** — **không** làm công tắc "Chế độ Boss" (thêm một cây menu thứ hai thì thêm chỗ dễ lệch). Sắp lại **một lần** đồng thời ở `apps/api/gh/shell/navigation.py` (NAV) và `packages/contracts/src/screens.ts` (DOMAINS/NAV_ORDER), theo khung 6 mục của báo cáo UX (top 5 #1):
  - Cấp 1 cho Owner: **Hôm nay** (dải "Cần Sếp xử lý" + 4 số) · **Hộp thư & Việc** · **Khách & Cơ hội** · **Kết nối** · **Đội ngũ** · **Cài đặt**, cộng **"Nâng cao"** (đổi tên từ "Kỹ thuật · Backend", thu gọn mặc định).
  - **Kết nối** là một trang duy nhất, mỗi thứ một thẻ (Bộ não AI, Zalo, Facebook, Gen-hub, MCP…) với cùng một kiểu trạng thái: Đang chạy / Cần Sếp xử lý / Chưa nối + một nút chính. Dùng lại các thẻ đã có, chỉ thêm viên trạng thái chung (top 5 #2 của báo cáo UX).
  - Đánh giá và Chăm sóc nhân viên chỉ hiện khi đã có ít nhất 1 nhân viên.
  - Sửa mô tả ở `screens.ts:227`.
- **F-61** — mỗi thẻ chỉ ở một chỗ, nơi khác chỉ để liên kết: UpdateCard, CliCard và PinCard mỗi thứ 1 nơi; thang tự trị khai ở 1 nơi; tên "Chuỗi chuyển hướng" thống nhất.
- **F-26** — thêm component `HomeRedirect`, trang đích là màn đầu tiên trong `/navigation`, dùng cho index route, `safeNext`, ForcePassword và Setup.
  - SystemScreen lọc tab theo quyền.
  - BrainTab hiện ổ khoá khi không có `system.read`.
  - NoModelBanner chỉ chạy khi là Owner (`enabled: isOwner`).
- **F-41** — ẩn màn "Plugin & Tiện ích" và đóng băng nền tảng plugin.
- **F-65** — bỏ "Hồ sơ sống" khỏi menu, chỉ mở từ danh sách.
- **F-66** — thêm `handle` cho `/guide` và `/guide/:n`.
- **F-63** — bỏ mục "Phụ đề tiếng Anh".
- **F-64** — Tổng quan còn 4 số kinh doanh; số kỹ thuật chuyển sang thẻ Sức khoẻ; bỏ thẻ độ trễ trùng.
- **F-67** — dải tab Hệ thống không tràn ở 1440px; đưa viên "tự trị", khiên % và "Góc nhìn đã lưu" ở header vào Nâng cao; logo hiện phiên bản thật; đổi tên "Hộp thư ý nghĩa".
- Đường cắt nếu vượt 2 ngày: F-64 và F-67 dời sang một bản đánh bóng nhỏ ngay sau v0.1.43; F-7, F-61, F-26 không được cắt.

Tiêu chí nghiệm thu:
- Test RBAC/navigation ở API và `nav.test.tsx` khớp cây mới.
- Playwright (mock, Owner): thanh bên cấp 1 có ≤ 7 mục; "Nâng cao" thu gọn khi mở trang; 5 việc chính (xem Cần Sếp xử lý, giao việc ở Hộp thư, mở Bản tin Gen, Sao lưu & cập nhật, Kết nối) đi tới được trong ≤ 2 cú bấm từ trang đích.
- Playwright: chưa có nhân viên thì không thấy Đánh giá/Chăm sóc.
- agent_staff vào `/` thì được chuyển sang màn đầu tiên của vai trò, không gặp ổ khoá.
- Manager mở `/system` chỉ thấy tab Nhật ký.
- Một test tĩnh đếm: UpdateCard/CliCard/PinCard mỗi thứ chỉ được render ở 1 màn.
- Playwright ở khung 1440px: dải tab Hệ thống có `scrollWidth <= clientWidth` và không tab nào bị cắt chữ (thay cho "ảnh chụp xem bằng mắt").

Kiểm thử bắt buộc:
- pytest navigation.
- vitest nav/HomeRedirect/SystemScreen.
- Playwright mock theo 3 vai trò.
- Hạ mức so ảnh pixel xuống kiểm khói cho các màn đã sắp lại.

Boss: không cần làm gì. Sau khi lên bản, Boss xem thử menu mới và nói nếu chỗ nào khó tìm.

---

### v0.1.43 — Bỏ lời hứa không thật & chữ khó hiểu · S–M (~1½ ngày)

**Mục tiêu:** giao diện không hứa điều không làm. Chữ trên màn là tiếng Việt Boss đọc hiểu được.

Việc:
- **F-23** — xoá fieldset "Cách bắt đầu" (`Step1Welcome.tsx:85-106`), luôn gửi `empty`. Sửa `steps.ts:18` và `setup.test.tsx:188-195`. **Không** nối `seed_demo` vào bản chạy thật, vì `raw.events` chỉ ghi thêm nên tin mẫu sẽ ở đó vĩnh viễn.
- **F-25** — bỏ `core.intent`, `core.scoring` và `core.indexing` khỏi `CORE_AGENT_KEYS` và `_bind_core_agents`. Đổi `biz/core/routes.py:323` sang `core.reply` với nhãn "Soạn lại / dịch nháp".
- **F-29** — thêm `<DataEmptyState>` dùng `GET /header` (`channels_live`, `groups_listening`):
  - Chưa nối kênh → "[Nối Zalo]" dẫn tới `/guide/5`.
  - Chưa chọn nhóm nào để nghe → dẫn tới `/guide/6`.
  - Vai trò khác Owner thấy "Nhờ Owner…".
  - Áp cho 5 màn chính: Hộp thư, Việc, Nhóm & Con người, Bàn duyệt, Cơ hội.
- **F-30** — thang tự trị rút còn 3 mức **chỉ ở giao diện**, không động tới thang 0–6 ở backend:
  - 0–2 hiện là "Chỉ ghi nhận" (ghi 0).
  - 3 hiện là "Gợi ý".
  - 4 hiện là "Soạn sẵn chờ duyệt" (ghi 4).
  - 5 và 6 chỉ đặt ở Nâng cao. Agent **đang** ở 5/6 phải hiện đúng là "Tự làm (đặt ở Nâng cao)", **không** hiện thành "Soạn sẵn chờ duyệt", vì như vậy Boss tưởng tin còn chờ duyệt trong khi agent tự gửi. Chỉ hiển thị thì không bao giờ ghi lại mức; chỉ ghi khi Boss chọn mức khác.

  Ngoài ra:
  - Lọc tin có 3 mức Thấp/Vừa/Cao, ánh xạ sang `min_score`; Jev và trọng số chuyển vào Nâng cao.
  - Ẩn "độ tin cậy" trên thẻ Hộp thư, chỉ hiện khi rê chuột.
  - Đổi ví dụ của Gen thành việc thật ("Khách nào hỏi giá hôm nay?").
- **F-62** — tooltip thanh bên chỉ có tiếng Việt; một tên cho nút tìm kiếm và màn tương ứng; Việt hoá các nhãn SSOT / "đơn vị ý nghĩa"; ghi chú phát hành bỏ tiền tố `feat(...)`.
- **F-38 (phần logic)** — gom chuẩn hoá chữ tiếng Việt vào `gh/textnorm.py`. Hiện lọc quy tắc dùng NFKD còn lọc trùng dùng NFD, nên cùng một câu có thể bị chuẩn hoá khác nhau. Gom các hàm định dạng web vào `lib/format.ts`.
- **F-24 (bị bác, chỉ sửa chữ)** — đổi "Đã xác nhận" trên thẻ nháp tin thành "Đã lưu nháp — chưa gửi" và bỏ biểu tượng máy bay giấy. Thêm nút "Duyệt & gửi" trên thẻ đó, dẫn thẳng tới đúng nháp ở Bàn duyệt (dùng luồng gửi đã có, không thêm đường gửi mới) — khép vòng trợ lý theo top 5 #3/#4d của báo cáo UX. Lý do vẫn làm dù phát hiện bị bác: rủi ro đọc lướt hiểu nhầm là có thật, trong khi công sửa nhỏ.
- Đường cắt nếu vượt 2 ngày: F-62 dời sang bản đánh bóng nhỏ (cùng F-64/F-67); F-38 và F-30 không được cắt vì là lỗi logic / hiểu sai.

Tiêu chí nghiệm thu:
- Bước 1 không còn lựa chọn dữ liệu mẫu.
- `list_bindings` chỉ trả `core.refinery`, `core.reply`, `core.gen` và `agent:*`.
- Render với header 0/0 hiện đúng nút dẫn đường.
- Chọn "Soạn sẵn chờ duyệt" ghi `autonomy_level=4`.
- vitest: agent có `autonomy_level=5` hiện "Tự làm (đặt ở Nâng cao)"; mở rồi đóng hộp chọn không gửi PATCH nào.
- Playwright: nút "Duyệt & gửi" trên thẻ nháp của Gen mở đúng nháp ở Bàn duyệt.
- Test chuẩn hoá: cùng một câu cho ra cùng kết quả ở cả rules lẫn triage.

Kiểm thử bắt buộc:
- vitest: setup, DataEmptyState, AutonomySelect.
- pytest: bindings, textnorm.
- Playwright mock cho Hộp thư khi chưa có kênh.

Boss: không cần làm gì.

---

### v0.1.44 — Kênh tới Boss ngoài app + watchdog + gói chẩn đoán · M (~2 ngày)

**Mục tiêu:** khi máy hay app sập, Boss vẫn được báo qua điện thoại. Bản tin và nhắc việc của Gen tới tận tay Boss. Claude có một gói chẩn đoán đầy đủ để tự sửa lỗi.

Việc:
- **F-6 bước 2 — watchdog ngoài app.**
  - genh cài một timer 10–15 phút, cạnh `gen-harness-update`, chạy `genh doctor --notify`.
  - Watchdog đọc: `update-status=failed`, compose health unhealthy (tự restart service đó), dung lượng đĩa, tuổi bản sao lưu (trong máy và ngoài máy), heartbeat của worker/bridge.
  - Gửi cảnh báo qua **Telegram Bot API chính thức** (bot của Boss tạo bằng BotFather, gửi tới `chat_id` của Boss). Có chống spam: mỗi sự cố chỉ báo 1 lần cho tới khi hết.
  - Chạy được cả khi api đã chết.
- **F-8 (c)** — bản tin và nhắc việc của v0.1.41 cũng đi qua cùng đường Telegram này, gửi một chiều. Không đi qua bridge Zalo cá nhân. Mọi hành động vẫn phải xác nhận trong Console. Boss nhắn lại để hỏi Gen thì để lát sau.
- **F-4 bước 2.**
  - Middleware gán `X-Request-ID`, trả trong header, đưa vào mọi problem+json và mọi dòng log. Web hiện request-id cạnh mã ERR.
  - Thêm `POST /client-errors`.
  - Nút "Tải gói chẩn đoán" trong Console: qua `run/request/doctor.json` gọi `genh doctor`, đã lọc dữ liệu nhạy cảm. Gói gồm `auto-update.log`, `update-status.json`, revision alembic, digest ảnh, và log có giờ.
- Token bot Telegram là bí mật: lưu mã hoá như các khoá khác, che trong log/gói chẩn đoán, và có trong `REENCRYPT_TARGETS` (test quét `*_enc` của v0.1.38 phải bắt được).
- Đường cắt nếu vượt 2 ngày: F-4 bước 2 tách thành bản riêng ngay sau; watchdog + Telegram không được cắt.

Tiêu chí nghiệm thu:
- Go test: dừng api (giả lập) thì watchdog gửi đúng 1 tin. Lần chạy thứ hai không gửi lại. Hết sự cố thì gửi 1 tin "đã ổn".
- Gói chẩn đoán không chứa chuỗi bí mật nào. Test này quét theo mẫu từ `secrets.json`.
- Request-id xuất hiện ở cả phản hồi lỗi lẫn dòng log tương ứng.

Kiểm thử bắt buộc:
- go test cho watchdog với HTTP Telegram giả.
- pytest cho request-id và client-errors.
- E2E-install: timer watchdog được cài và chạy 1 lần.

Boss phải làm: tạo bot Telegram bằng BotFather (khoảng 3 phút, Claude hướng dẫn từng bước), dán token và bấm "Gửi thử" trong Console.

---

### v0.1.45 — Khoá cấu hình nhạy cảm & vệ sinh bảo mật · M (~1½–2 ngày)

**Mục tiêu:** phiên Owner lỡ bị lấy cũng không hạ rào được (đổi tự trị, đổi tool MCP, gắn tài khoản lạ), và các lỗ nhỏ đã biết được vá.

Việc:
- **F-20 (phần còn lại)** — chỉ chặn đúng các đường hạ rào, không gắn PIN tràn lan, để Boss không phải nhập PIN liên tục:
  1. `patch_agent` chỉ đòi PIN `policy.change` khi body có `autonomy_level`, `forbidden`, `limits` hoặc `channel_scopes`.
  2. `patch_tool_access` đòi PIN `mcp.expose` khi chuyển tool từ write sang read.
  3. `cli_login` đòi PIN `cli.switch_account`.
  4. Setup bước 9/10 khi `finished_at` đã có thì đòi PIN `policy.change` / `user.manage`.

  Bỏ cập nhật và sao lưu khỏi danh sách. Thêm test dạng bảng: các route trên trả 423 khi phiên Owner chưa nhập PIN.
- **F-58** — thống nhất `require("system.manage", rbac.ALL)`, kèm test dạng bảng.
- **F-49** — dùng `pin_endpoint` cho mọi máy chủ MCP và nhà cung cấp. Luôn cấm link-local, unspecified và tên dịch vụ compose. Bắt buộc https khi có token.
- **F-52** — `run/` đặt quyền 0770, cùng nhóm với uid 10001. Mở tệp bằng `O_NOFOLLOW`/`CreateTemp`. Kiểm chủ sở hữu tệp yêu cầu.
- **F-54** — truyền mật khẩu pg_dump/pg_restore qua `PGPASSWORD`/`PGPASSFILE` thay cho argv.
- **F-55** — WebSocket kiểm Origin, nạp lại phiên mỗi 60 giây, đóng socket khi phiên bị thu hồi.
- **F-56** — mã CLI phải khớp `^[A-Za-z0-9._~#/+=-]{4,500}$`. Trước khi merge, đối chiếu regex với **dạng** mã thật đã ghi ở v0.1.39 (F-77); test có một mẫu cùng dạng phải được chấp nhận.
- **F-57** — áp `mask_for_model` cho mọi máy chủ MCP; lưu digest của args thay cho nguyên văn.
- **F-60** — thêm một đoạn ngắn trong Trợ giúp nói rõ giới hạn của PIN. Điểm đánh giá nhân sự có cờ "đáng ngờ".

Tiêu chí nghiệm thu:
- Test bảng 423/403 xanh cho mọi route trong danh sách.
- Thêm MCP trỏ tới `169.254.169.254` bị từ chối.
- Thử ghi đè symlink trong `run/` thì không có tác dụng (go test).
- WS bị đóng trong vòng ≤ 60 giây sau khi thu hồi phiên.

Kiểm thử bắt buộc: pytest các bảng quyền/PIN, SSRF và WS; go test cho hostlink.

Boss: không cần làm gì (chỉ là nhập PIN đúng lúc khi đổi các cấu hình trên).

---

### v0.1.46 — Nhân viên & điện thoại vào được · M (~2 ngày)

**Mục tiêu:** Boss mở Console được trên điện thoại, nhân viên nhận lời mời mở ra được, trong khi cổng không còn mở toang cho cả mạng.

Việc:
- **F-27 — địa chỉ truy cập từ máy khác.**
  - Lời mời lấy địa chỉ từ `GH_PUBLIC_URL`, không lấy `window.location.origin`. Nếu địa chỉ là localhost hoặc 127.x thì hiện cảnh báo đỏ trong TempPasswordDialog.
  - Thêm lệnh `genh remote` (hoặc `set-address`), ưu tiên **Tailscale Serve / Cloudflare Tunnel** để có chứng chỉ thật và không phải mở cổng. Phương án phụ là tên LAN, kèm hướng dẫn cài CA cho điện thoại.
  - Caddyfile giữ **cả hai** địa chỉ: `localhost:8443, {$GH_SITE_ADDRESS}:8443`. Lý do: genh ProxyHost cố định là localhost, bỏ localhost thì kiểm ready và tự cập nhật hỏng.
- **F-21 — thu hẹp cổng.**
  - Compose đổi thành `"${GH_BIND_ADDR:-127.0.0.1}:${GH_PORT:-8443}:8443"`, cả trong `embedded_compose.yaml`. genh chỉ ghi `GH_BIND_ADDR=0.0.0.0` khi Owner chọn mở LAN, và hiện cảnh báo.
  - **Không phá máy đang chạy:** hiện cổng đang nghe mọi giao diện (`compose.yaml:33`). Mặc định 127.0.0.1 chỉ áp cho **cài mới**. Khi `genh update` gặp bản cài cũ chưa có `GH_BIND_ADDR` thì ghi `GH_BIND_ADDR=0.0.0.0` để giữ nguyên hành vi, và tạo một chuông "Cổng đang mở cho cả mạng — bấm để chỉ cho máy này / dùng Tailscale" để Owner tự quyết (QD-12). Nếu không, lần cập nhật 03:00 sẽ cắt truy cập của nhân viên và điện thoại mà không báo.
  - Giới hạn số lần đăng nhập theo IP và email bằng Redis: 10 lần sai trong 15 phút thì trả 429.
  - Email không tồn tại vẫn chạy argon2 giả, để không lộ email qua thời gian phản hồi.
  - Phiên có hạn tuyệt đối 30 ngày.
  - TOTP để sau.

Tiêu chí nghiệm thu:
- E2E-install: mặc định cổng chỉ nghe trên 127.0.0.1. Chạy `genh remote --lan` thì nghe trên 0.0.0.0 và vẫn qua được `ready`.
- Tự cập nhật vẫn xanh khi đã đặt `GH_SITE_ADDRESS`.
- pytest: lần đăng nhập sai thứ 11 trả 429. Email không tồn tại vẫn gọi `verify_secret`.
- E2E-upgrade từ bản cũ (không có `GH_BIND_ADDR`): sau nâng cấp cổng vẫn nghe trên 0.0.0.0 và có đúng 1 chuông cảnh báo cổng mở.
- e2e-live thêm luồng "mời người": Owner mời → nhân viên đăng nhập bằng mật khẩu tạm → đổi mật khẩu → vào màn đầu tiên của vai trò (top 5 #5 của báo cáo UX).
- vitest: hộp mời có cảnh báo khi địa chỉ là localhost.

Kiểm thử bắt buộc: E2E-install (2 chế độ bind), pytest đăng nhập, vitest hộp mời.

Boss phải làm: chọn cách truy cập từ xa (Tailscale khuyên dùng: cài app trên điện thoại và đăng nhập, khoảng 5 phút), rồi mở thử Console trên điện thoại. **Cần kiểm trên máy chủ Fedora thật.**

---

### v0.1.47 — Facebook ghi, lát 1: trả lời / nhắn qua đề xuất + PIN · M (~2 ngày)

**Mục tiêu:** Gen soạn được câu trả lời bình luận hay tin nhắn Facebook trên **tài khoản của chính Boss**. Boss bấm Xác nhận và nhập PIN thì mới gửi, mỗi lần gửi có ảnh chụp làm bằng chứng.

Điều kiện trước:
- Facebook đọc đã chạy thật ít nhất 1 tuần (sau v0.1.39).
- **F-85** đã chốt một trong hai: bật sandbox Chromium, hoặc Boss ký chấp nhận rủi ro bằng văn bản theo QD-12.

Việc:
- **F-79** — dùng khung permit chung (`gh/social/permit.py`):
  - Thêm `write_kinds` cho `reply_comment` và `send_message`.
  - Adapter `ghb` thực hiện thao tác, với ảnh chụp và trace cho mỗi việc.
  - Gen tạo đề xuất; thẻ đề xuất có Xác nhận + PIN.
  - Giới hạn số lượt mỗi ngày.
  - Nút khẩn "Dừng tất cả" (KillSwitchCard) phải chặn được việc ghi.
  - Đăng bài để lát 2.
- **F-83 (phần MXH)** — thêm job kiểm phiên Facebook hằng ngày. Phiên hết hạn thì gửi chuông và báo qua Telegram (v0.1.44).
- **F-59** — ghi lý do vào QD: trễ là để lịch sự với nền tảng (giới hạn tốc độ), chạy có giao diện là để Owner tự đăng nhập. Đổi sang trễ cố định để không bị hiểu là né chống bot.
- **F-92** — bỏ số phiên bản khỏi các chú thích "CHỖ CẮM v0.1.30".

Tiêu chí nghiệm thu:
- pytest: thiếu PIN hoặc permit quá hạn thì không gửi được.
- Kill switch chặn được việc ghi.
- Có giới hạn lượt/ngày.
- ghb với trang mẫu: trả lời bình luận tạo được ảnh chụp.
- Nghiệm thu thật: Boss xác nhận 1 câu trả lời trên bài của chính Boss.

Kiểm thử bắt buộc: pytest permit/proposal, test adapter ghb với trang mẫu, Playwright cho thẻ đề xuất.

Boss phải làm: chốt điều kiện F-85 (đọc 1 trang cảnh báo rồi bấm đồng ý, hoặc chờ bản bật sandbox), rồi thử 1 lần trả lời thật.

---

### v0.1.48 — Bản build tái lập & pipeline gọn · S–M (~1–1½ ngày)

**Mục tiêu:** máy Owner chạy đúng tổ hợp thư viện mà CI đã kiểm, và phát hành không hỏng chỉ vì lỗi mạng tạm thời.

Việc:
- **F-19.**
  - Ghim digest thẳng trong `deploy/compose.yaml` (`caddy:2-alpine@sha256:…`, `redis:7-alpine@sha256:…`) và trong ảnh nền của các Dockerfile.
  - `uv lock` cho `apps/api` và `apps/browser`. CI dùng `uv sync --frozen --extra dev`, Dockerfile dùng `uv sync --frozen --no-dev`.
  - Sửa `fastapi>=0.121`.
  - Bật Renovate: PR nâng digest và phụ thuộc chạy qua CI, xanh thì tự merge theo quy trình.
- **F-36.**
  - Bỏ tag `:latest`.
  - Bước build thử lại 1 lần (`continue-on-error` cho lần đầu, rồi chạy bước build2).
  - Thêm cache `type=gha`.
  - npm chạy với `--fetch-retries=5`, kèm `--mount=type=cache,target=/root/.npm`.
- **F-71** — `permissions: contents: read` mặc định cho `ci.yml` và `installer-matrix.yml`; nâng các action khỏi Node 20; ghim theo SHA (Renovate tự nâng).
- **F-13 (phần quét bảo mật, trước đây bị bỏ sót)** — `govulncheck` trong installer-matrix, `pip-audit` (trên `uv.lock`) và `npm audit --omit=dev` trong ci.yml. Chạy dạng **báo cáo, không chặn** (tránh CI đỏ vì CVE ngoài tầm tay); lỗ mức cao thì Renovate/Claude mở PR sửa.
- **F-44** — CI so `embedded_compose.yaml` với `deploy/compose.yaml`, hoặc thay bằng stub báo lỗi "chỉ dùng bản release". Bỏ `docs/handoff/schema.sql`, hoặc sinh tự động.

Tiêu chí nghiệm thu:
- Hai lần build liên tiếp từ cùng một commit cho cùng tổ hợp phụ thuộc (so `uv.lock` và `pip freeze` trong ảnh).
- Compose không còn ảnh nào không có digest (test trong `compose_test.go`).
- CI đỏ khi `embedded_compose.yaml` lệch.

Kiểm thử bắt buộc: pytest/vitest/go test như cũ; E2E cài thật với ảnh mới.

Boss: không cần làm gì.

---

### v0.1.49 — Gen đọc lịch / mail / việc qua Gen-hub (chỉ đọc) · M (~1½ ngày)

**Mục tiêu:** bản tin và câu trả lời của Gen có cả lịch hôm nay, mail cần trả lời và việc đang mở của Boss.

Việc:
- **Phần A — luôn làm (không cần QD mới):** thêm tool đọc **nội bộ** cho Gen: Tài liệu và Deal/Vụ việc (top 5 #4f của báo cáo UX, trước đây bị bỏ sót). Dữ liệu nằm sẵn trong Gen-Harness, chỉ Owner, qua `mask_for_model`.
- **Phần B — chỉ làm khi Boss đã ra QD mới** (nếu Boss không duyệt thì đợt này chỉ còn phần A):
- Mở rộng danh sách hậu tố cho phép ở `hub_link/service.py:39` (hiện chỉ `kho_*`) sang các tool **chỉ đọc**: `calendar_list_events`, `tasks_list`, `gmail_search`, `gmail_read_message`, `drive_search`.
- Tái dùng `invoke_tool`, ghim DNS, `mask_for_model`, đệm 5 phút, và chỉ cho Owner.
- Ghép vào Bản tin Gen (F-8).
- **F-83 (phần Gen-hub)** — thêm breaker 60 giây riêng cho Gen-hub.

Tiêu chí nghiệm thu:
- pytest: Gen gọi được tool đọc Tài liệu và Deal/Vụ việc; vai trò khác Owner nhận 403.
- (Phần B) pytest với MCP giả: Gen gọi được tool lịch; tool ghi bị từ chối; nội dung được che.
- (Phần B) Bản tin có mục "Lịch hôm nay".

Kiểm thử bắt buộc: pytest hub_link (allowlist, breaker), pytest bản tin.

Boss phải làm: **duyệt phạm vi** (nội dung mail sẽ đi qua model đám mây, đã được che), rồi tick thêm quyền đọc cho token trên Gen-hub. Cần một QD mới tương tự QD-11(1).

---

### v0.1.50 — Gen ghi Kho có xác nhận (trí nhớ ngoài hội thoại) · M (~1½–2 ngày)

**Mục tiêu:** Gen tự đề xuất ghi Phiên/Việc vào Kho và ghi lại sở thích của Boss. Boss xác nhận rồi mới ghi.

**Trạng thái theo quyết định hiện hành:** "Gen ghi Gen-hub để sau" vẫn hiệu lực. Phần ghi Kho (F-81, F-87 tự động) **mặc định hoãn**, chỉ bắt đầu khi Boss đã ra QD mới thay thế. "Gen nhớ" lưu **cục bộ** trong Gen-Harness, không ghi Gen-hub, nên luôn làm.

Việc:
- **"Gen nhớ" (luôn làm)** — ghi chú quy ước/sở thích của Sếp lưu trong CSDL Gen-Harness; Gen đề xuất, Sếp xác nhận; sửa/xoá được ở Cài đặt; Gen đọc khi trả lời và khi soạn bản tin.
- **F-81 (chỉ khi có QD mới)** — dùng chung khung permit với Facebook. Gen đề xuất `kho_create`/`kho_update` cho Phiên/Việc; Boss Xác nhận + PIN.
- **F-87 (phần tự động, chỉ khi có QD mới)** — sau khi có đường ghi, Gen đề xuất PHIEN cho các bản đã ra.

Tiêu chí nghiệm thu:
- pytest: ghi chú "Gen nhớ" chỉ được lưu sau khi Owner xác nhận; lần trả lời sau prompt có chứa ghi chú đó.
- (Khi có QD) pytest với MCP giả: không có xác nhận thì không có lời gọi ghi nào.
- (Khi có QD) Có xác nhận thì ghi đúng 1 bản ghi.
- (Khi có QD) Action Log có dòng tương ứng.

Kiểm thử bắt buộc: pytest proposals/hub_link, Playwright cho thẻ đề xuất ghi Kho.

Boss phải làm: duyệt phạm vi ghi Kho (QD mới, thay "Gen ghi Gen-hub để sau").

---

### Song song (không tăng VERSION) — Tài liệu, Kho, nhánh · S, làm xen giữa các đợt

- **F-90** — đồng bộ tài liệu:
  - ROADMAP: sửa ngày, ghi đúng "Tiếp theo", thêm mục **Nợ** và mục cho các bản phản ứng.
  - Ba tài liệu thiết kế: một dòng trạng thái ở đầu; bỏ các đoạn về Jules và câu "Gen không dùng Playwright"; sửa migration, quyền và cờ theo mã thật.
  - README: tách "Dành cho Boss" và "Dành cho dev"; sửa số dịch vụ và link chết; thêm `docs/runbook.md`.
  - ARCHITECTURE/PLAN: trỏ sang tài liệu thiết kế và ROADMAP.
- **F-69, F-47** — `CHANGELOG.md` ngắn (3–5 dòng mỗi bản) cộng `docs/releases/vX.Y.Z.md`. HANDOFF chỉ giữ hiện trạng và việc dở, ≤ 200 dòng. Gộp hai mục trùng tên v0.1.30. **Sửa CLAUDE.md cần Boss duyệt.**
- **F-91, F-92** — ghi chú các lời hẹn đã trượt, trỏ sang mục Nợ.
- **F-42** — xoá Idempotency-Key khỏi tài liệu, hoặc ghi rõ "chưa thi hành".
- **F-39** — ghi một dòng: RLS chỉ là phòng thủ phụ, không mở rộng thêm.
- **F-70** — xoá 10 nhánh `claude/*` đã merge và các nhánh `worktree-agent-*` local; bật "Automatically delete head branches". **Không** thêm `paths-ignore: docs/**` cho `ci.yml`: v0.1.33 bắt các check của ci.yml là required, lọc đường dẫn sẽ làm PR chỉ sửa tài liệu kẹt mãi. Muốn tiết kiệm CI thì dùng job tổng `ci-ok` (xem v0.1.33). **Cần Boss cho phép.**
- **Bộ 10–15 câu hỏi chuẩn (chuyển từ v0.1.41)** — script chạy trên 2–3 nguồn (CLI, OpenRouter, Gemini), ghi điểm + chi phí vào ghi chú phát hành; quyết định giữ/bỏ Jev theo số đo. Chạy sau khi v0.1.41 lên.
- **F-87** — ghi PHIEN cho v0.1.28 → bản hiện tại; tạo VIEC cho mục Nợ; cập nhật QD-12 (hạn đã trượt) và DA-1; chốt câu định vị "Gen là mặt tiền chính, Console là nơi xem chi tiết". **Cần Boss cho phép ghi Kho.**

Boss phải làm: trả lời "ghi Kho đi", "cho xoá nhánh", "cho sửa CLAUDE.md" (3 câu, có thể gộp làm 1 lần).

---

## (d) Đóng băng / cắt (kèm lý do)

| Mục | Quyết định | Lý do |
|---|---|---|
| F-24 Gen báo "Đã xác nhận" | **Bị bác**; chỉ sửa chữ ở v0.1.43 | Tóm tắt trên thẻ đã ghi "chưa gửi đi". Phần còn lại chỉ là chữ và biểu tượng dễ đọc nhầm. |
| Công tắc "Chế độ Boss" (2 cây menu) | **Cắt**, thay bằng sắp lại 1 menu (v0.1.42) | Thêm một bản sao là thêm chỗ lệch, đúng loại lỗi đang muốn bỏ. |
| Người duyệt bắt buộc (Boss) cho phát hành | **Cắt** | Trái mục tiêu tự bảo trì và trái quy trình tự merge. Cổng tự động (CI + E2E + thời gian chín) đã đủ. |
| Kiểm cosign/minisign trong genh và install.sh | **Hoãn** (Nợ) | Chỉ chống tráo tệp trên Release, không chống token bị lộ. Trước mắt sửa tài liệu cho đúng. |
| Tích hợp S3/MinIO | **Cắt** | Dùng `genh export` theo lịch (v0.1.40). Thu API về `local`. |
| Ô E2E Fedora chạy trong container | **Cắt** | Không kiểm được SELinux, systemd --user hay firewalld. Chỉ sửa tài liệu; chạy VM Fedora khi có runner. |
| Ghi dump sao lưu theo luồng / `to_thread` | **Hoãn** | CSDL nhỏ, timeout 3600 giây là đủ. |
| Sinh type từ OpenAPI (F-14 phần gốc) | **Hoãn** | Không route nào có `response_model`, nên type sinh ra rỗng. e2e-live trong CI chặn lỗi hiệu quả hơn. |
| TOTP cho Owner | **Hoãn** | Đã có rate-limit, argon2 và PIN. Làm sau khi mở truy cập từ xa ổn định. |
| F-80 cổng API Trang FB / IG / Zalo OA / TikTok / LinkedIn / X | **Đóng băng** | Chờ Facebook cá nhân chạy thật ≥ 2 tuần và Boss xác nhận có Trang/OA cần dùng. |
| Thêm nền tảng mạng xã hội mới (kể cả đăng nhập trong app cho chúng) | **Đóng băng** | Facebook trước; mỗi nền tảng là một gánh bảo trì. |
| F-78 Jev làm riêng | **Đóng băng** (QD-10) | Chỉ kiểm 1 lần (v0.1.39). Giữ lại nếu số đo của bộ câu hỏi chuẩn (việc song song, sau v0.1.41) cho thấy rẻ và đúng ở khâu phân loại. |
| Đánh bóng thêm nguồn CLI | **Ngừng** sau v0.1.32 (F-88) | Mặc định CLI chỉ dành cho Owner hỏi trực tiếp; việc nền dùng khoá API (F-86). Owner vẫn được tự thêm CLI vào việc nền sau cảnh báo + PIN (QD-12). |
| F-82 Phương án B và Playwright cho agent vòng ngoài | **Cắt** khỏi lộ trình gần | Chưa ai cần; trùng với D3. |
| F-68 Mở Gen cho vai trò khác | **Hoãn** | Chờ có nhân viên dùng thật. |
| F-84 phần stream và Gen cho vai trò khác | **Hoãn** | Đánh giá và chi phí được ưu tiên trước. |
| Gen ghi ra ngoài qua Gen-hub: nháp Gmail, tạo sự kiện Lịch, kanban/warroom (Mốc 5b của báo cáo lộ trình) | **Hoãn** | Quyết định hiện hành "Gen ghi Gen-hub để sau". Chỉ mở sau khi có QD mới, cùng khung permit của v0.1.50. |
| Watchdog ghi sự cố vào Kho dữ liệu (khuyến nghị #3 của báo cáo vận hành) | **Hoãn** | Cùng lý do (ghi Gen-hub để sau). Claude đọc sự cố qua gói chẩn đoán (v0.1.44) và kết quả kiểm tra (v0.1.39). |
| Boss nhắn lại Gen qua Telegram (2 chiều) | **Hoãn** sau v0.1.44 | Một chiều đủ cho bản tin/cảnh báo. Hai chiều cần xác thực người gửi và chống lệnh giả; làm sau khi đường một chiều chạy ổn ≥ 2 tuần. |
| Đổi mặc định cổng sang 127.0.0.1 cho máy **đang chạy** | **Không làm tự động** | Sẽ cắt truy cập của nhân viên/điện thoại trong lần cập nhật đêm. Chỉ áp cho cài mới; máy cũ được chuông đề nghị, Owner tự quyết (QD-12). |
| F-41 Nền tảng plugin | **Ẩn & đóng băng** (v0.1.42) | 9 manifest đều có `entry: null`, không có giá trị cho Owner. |
| F-39 Mở rộng RLS | **Dừng** | Mỗi bản cài chỉ có 1 tổ chức. Ghi là phòng thủ phụ. |
| F-40 Khung đa tổ chức | **Không refactor** | Job mới dùng `bootstrap.org_id()`. |
| F-18 Refactor route sang service diện rộng | **Không lập đợt riêng** | Chỉ rút import chéo khi đang sửa đúng chỗ đó. |
| F-48 Chia gói web theo màn | **Hoãn** | Chạy trong LAN, máy tự host. |
| F-50 Redis ACL, F-51 superuser sidecar, F-53 CA có NameConstraints | **Hoãn** (Nợ bảo mật) | Vàng, công M. Xem lại khi mở truy cập từ xa rộng hơn hoặc sau lát ghi Facebook. |
| So ảnh pixel với thiết kế gốc | **Hạ** xuống kiểm khói | Phần mới liên tục phải ẩn khi so, tốn công mà ít giá trị. |
| F-12 mục "ẩn s3/minio trên UI" | **Bỏ** | UI vốn đã ẩn; chỉ cần thu Literal ở API. |
| F-28 việc Telegram trong hướng dẫn | **Hoãn** | Chưa có plugin kênh Telegram. Đường Telegram ở v0.1.44 là bot báo tin cho Boss, không phải kênh khách. |

---

## (e) Bảng toàn bộ phát hiện

Trạng thái: **đứng vững** = qua kiểm chứng đối kháng · **bị bác** = phản biện bác bỏ · **🟡** = vàng, chưa kiểm chứng.
Mức của phát hiện đứng vững là mức sau kiểm chứng.

| ID | Mức | Tiêu đề | Đợt | Trạng thái |
|---|---|---|---|---|
| F-1 | 🔴 đỏ | Giao việc (Hộp thư), gán người (Vụ việc), gán BOT (Nhóm & Con người) dùng ID giả viết cứng → luôn lỗi 422 ở bản thật; test mock không bắt được | v0.1.35 | đứng vững |
| F-2 | 🟠 cam | "Hạn lưu dữ liệu" trên giao diện là giả — không job nào thi hành; nhiều bảng phình vô hạn | v0.1.36 (nhãn tạm) → v0.1.40 | đứng vững |
| F-3 | 🟠 cam | Sao lưu theo lịch có thể hỏng âm thầm, không chuông, không bù | v0.1.36 | đứng vững |
| F-4 | 🟠 cam | Log production nuốt traceback và thời gian; mã ERR-… trên web không nối được với log máy chủ | v0.1.36 (bước 1) → v0.1.44 (bước 2) | đứng vững |
| F-5 | 🔴 đỏ | Stored XSS qua Tài liệu → nhân viên chiếm phiên Owner | v0.1.35 | đứng vững |
| F-6 | 🔴 đỏ | Hệ thống hỏng mà không báo cho Boss: không giám sát sức khoẻ, không cảnh báo chủ động trong lẫn ngoài app | v0.1.36 (trong app) → v0.1.44 (ngoài app) | đứng vững |
| F-7 | 🟠 cam | Menu xếp theo kiến trúc kỹ thuật, việc quản trị của Boss nằm trong "Kỹ thuật · Backend" | v0.1.42 | đứng vững |
| F-8 | 🟠 cam | Gen chưa phải trợ lý thật: chỉ trả lời trong khung chat Console | v0.1.41 (a,b) → v0.1.44 (c) → v0.1.49 (A nội bộ; B cần QD) → v0.1.50 (Gen nhớ) | đứng vững |
| F-9 | 🟠 cam | Phát hành và tự cập nhật không có cổng chặn: bản thành "latest" trước CI/E2E, chữ ký cosign không ai kiểm, không người duyệt — máy Owner tự nhận bản lỗi (hoặc bị chiếm) lúc 03:00 | v0.1.33 | đứng vững |
| F-10 | 🟠 cam | Rollback khôi phục CSDL kể cả khi CHƯA đụng gì (pull lỗi) — mất dữ liệu ghi trong khoảng đó; chu trình chạy MỖI ĐÊM | v0.1.34 | đứng vững |
| F-11 | 🔴 đỏ | Không dọn image cũ, không theo dõi dung lượng đĩa — đĩa đầy dần, Postgres dừng, Boss không được báo | v0.1.34 (genh) + v0.1.36 (chuông) | đứng vững |
| F-12 | 🔴 đỏ | Sao lưu và khoá giải mã chỉ nằm trên cùng ổ đĩa; tuỳ chọn S3/MinIO không có tác dụng — hỏng ổ là mất hết | v0.1.40 | đứng vững |
| F-13 | 🟠 cam | CI bỏ sót nhiều bộ test đã có (Go của genh, Playwright web, app-role, E2E cài thật) và không quét bảo mật | v0.1.33 (test) + v0.1.48 (quét bảo mật) | đứng vững |
| F-14 | 🟡 vàng | Hợp đồng API và mock viết tay, không đối chiếu với backend → lệch là chuyện thường | v0.1.35 (e2e-live CI); sinh type: hoãn | đứng vững |
| F-15 | 🟡 vàng | Hai API sổ tay song song, phân quyền khác nhau → vượt phạm vi dữ liệu | v0.1.35 | đứng vững |
| F-16 | 🟠 cam | Một số job định kỳ có chi phí tăng theo toàn bộ lịch sử, chạy mỗi 10–15 phút | v0.1.40 | đứng vững |
| F-17 | 🟡 vàng | Gói chuyển máy (.ghbundle) bỏ sót phiên mạng xã hội; danh sách bí mật viết tay không có gì bảo vệ | v0.1.38 | đứng vững |
| F-18 | 🟡 vàng | Logic nghiệp vụ nằm trong route; route import route; tệp quá lớn | Không lập đợt (làm khi chạm) | đứng vững |
| F-19 | 🟡 vàng | Ảnh nền và phụ thuộc không ghim (Python không lockfile, caddy/redis và ảnh nền build không digest) — bản build không tái lập | v0.1.48 | đứng vững |
| F-20 | 🟠 cam | Thiếu PIN ở các thao tác "định tuyến lại / hạ rào" | v0.1.35 (providers) → v0.1.45 | đứng vững |
| F-21 | 🟡 vàng | Phơi cổng & không chống dò mật khẩu | v0.1.46 | đứng vững |
| F-22 | 🟠 cam | agy CLI nhận prompt không tin cậy mà không khoá công cụ/không cô lập (cần kiểm chứng trên agy 1.2.9) | v0.1.38 | đứng vững |
| F-23 | 🟡 vàng | "Dùng dữ liệu mẫu" ở bước 1 không làm gì | v0.1.43 | đứng vững |
| F-24 | — (đề xuất 🟠) | Gen báo "Đã xác nhận" nháp tin nhưng tin chưa được gửi | v0.1.43 (chỉ sửa chữ) | bị bác (1 lens bác 0,8; 1 lens hạ vàng) |
| F-25 | 🟡 vàng | Bảng "Gán model cho từng agent" có 4 dòng không có tác dụng | v0.1.43 | đứng vững |
| F-26 | 🟡 vàng | Người không phải Owner vẫn gặp ngõ cụt | v0.1.42 | đứng vững |
| F-27 | 🟠 cam | Mời nhân viên nhưng có thể nhân viên không vào được | v0.1.46 | đứng vững |
| F-28 | 🟡 vàng | Hướng dẫn: 3 tên gọi, một liên kết sai, thiếu các việc Boss thật sự muốn làm | v0.1.39 | đứng vững |
| F-29 | 🟡 vàng | Trạng thái rỗng không dẫn đường | v0.1.43 | đứng vững |
| F-30 | 🟡 vàng | Quá nhiều khái niệm AI và lọc tin | v0.1.43 | đứng vững |
| F-31 | 🟡 vàng | Gen-hub khó nối với người không rành kỹ thuật | v0.1.39 | đứng vững |
| F-32 | 🟠 cam | Facebook chỉ vào được từ menu tài khoản, không nằm trong danh sách kênh | v0.1.39 | đứng vững |
| F-33 | 🟠 cam | Rollback thất bại đúng kịch bản hay gặp nhất (api mới crash-loop) → bản cũ chạy trên schema mới; vòng lặp lỗi mỗi đêm | v0.1.34 | đứng vững |
| F-34 | 🟡 vàng | Không có khoá loại trừ trên máy chủ; trạng thái "running" có thể kẹt; SIGTERM giết giữa chừng | v0.1.37 | đứng vững |
| F-35 | 🟠 cam | Kiểm nâng cấp quá hẹp: chỉ từ N-1, CSDL rỗng, chỉ Ubuntu — máy Owner nhảy nhiều bản, có dữ liệu thật, chạy Fedora | v0.1.34 (seed + bản hỏng cố ý) → v0.1.37 (tags[3]) | đứng vững |
| F-36 | 🟡 vàng | Lỗi mạng tạm thời làm hỏng phát hành; không có retry/cache | v0.1.48 | đứng vững |
| F-37 | 🟡 vàng | Log container không giới hạn | v0.1.34 | đứng vững |
| F-38 | 🟡 vàng | Sao chép-dán giữa các cụm (hệ quả của làm song song theo cụm) | v0.1.43 (textnorm); phần còn lại khi chạm | 🟡 chưa kiểm |
| F-39 | 🟡 vàng | RLS: tốn công bảo trì nhưng gần như không bảo vệ gì | Song song (ghi chú) · đóng băng | 🟡 chưa kiểm |
| F-40 | 🟡 vàng | Khung đa-tổ chức không dùng | Cắt (không refactor) | 🟡 chưa kiểm |
| F-41 | 🟡 vàng | Nền tảng plugin không có plugin thật | v0.1.42 (ẩn, đóng băng) | 🟡 chưa kiểm |
| F-42 | 🟡 vàng | Hợp đồng Idempotency-Key chỉ có một nửa | Song song (tài liệu) | 🟡 chưa kiểm |
| F-43 | 🟡 vàng | Xử lý lỗi rò thông tin kỹ thuật và phân loại sai: lỗi DB lộ SQL/tham số, mọi OSError thành 503, Swagger công khai, email sai ghi vào nhật ký | v0.1.35 | 🟡 chưa kiểm |
| F-44 | 🟡 vàng | Vệ sinh migration/lược đồ; embedded_compose.yaml lệch deploy/compose.yaml (~100 dòng, thiếu dịch vụ browser) | v0.1.48 | 🟡 chưa kiểm |
| F-45 | 🟡 vàng | Lịch cron worker đặt sai giờ: theo UTC (job nặng rơi vào giờ làm việc VN) và không tránh cửa sổ cập nhật đêm 03:00 | v0.1.36 | 🟡 chưa kiểm |
| F-46 | 🟡 vàng | Số phiên bản không thống nhất: VERSION v0.1.31 nhưng package.json/pyproject/gh/__init__.py = 0.1.0; ảnh không nhãn OCI version | v0.1.36 | 🟡 chưa kiểm |
| F-47 | 🟡 vàng | Mảnh rỗng/đặt nhầm chỗ | Song song | 🟡 chưa kiểm |
| F-48 | 🟡 vàng | Web không chia gói theo màn | Hoãn | 🟡 chưa kiểm |
| F-49 | 🟡 vàng | SSRF từ cấu hình: máy chủ MCP thường và endpoint nhà cung cấp | v0.1.45 | 🟡 chưa kiểm |
| F-50 | 🟡 vàng | Redis chính không xác thực, dùng chung với bridge | Hoãn (Nợ bảo mật) | 🟡 chưa kiểm |
| F-51 | 🟡 vàng | Thông tin superuser CSDL nằm trong api/worker; RLS chỉ là lớp mỏng | Hoãn (Nợ bảo mật) | 🟡 chưa kiểm |
| F-52 | 🟡 vàng | Hộp thư máy chủ run/ để 0777 — user khác trên máy hoặc container bị chiếm có thể thả yêu cầu cập nhật/khôi phục hoặc ghi đè tệp qua symlink | v0.1.45 | 🟡 chưa kiểm |
| F-53 | 🟡 vàng | CA nội bộ không giới hạn tên miền được cài vào kho tin cậy | Hoãn (Nợ bảo mật) | 🟡 chưa kiểm |
| F-54 | 🟡 vàng | Bí mật và nội dung nằm trên dòng lệnh | v0.1.45 | 🟡 chưa kiểm |
| F-55 | 🟡 vàng | WebSocket | v0.1.45 | 🟡 chưa kiểm |
| F-56 | 🟡 vàng | Mã đăng nhập CLI ghi thẳng vào PTY | v0.1.45 | 🟡 chưa kiểm |
| F-57 | 🟡 vàng | Nhật ký MCP lưu dữ liệu thô | v0.1.45 | 🟡 chưa kiểm |
| F-58 | 🟡 vàng | Phân quyền system.manage chưa đồng nhất | v0.1.45 | 🟡 chưa kiểm |
| F-59 | 🟡 vàng | Vùng xám chính sách QD-12 ở trình duyệt mạng xã hội | v0.1.47 | 🟡 chưa kiểm |
| F-60 | 🟡 vàng | Giới hạn cần nói rõ với Boss | v0.1.45 | 🟡 chưa kiểm |
| F-61 | 🟡 vàng | Bản sao và khai báo hai nơi (a2) | v0.1.42 | 🟡 chưa kiểm |
| F-62 | 🟡 vàng | Tiếng Anh và chữ kỹ thuật còn sót trên giao diện (a3) | v0.1.43 | 🟡 chưa kiểm |
| F-63 | 🟡 vàng | Bỏ "Phụ đề tiếng Anh" khỏi menu tài khoản | v0.1.42 | 🟡 chưa kiểm |
| F-64 | 🟡 vàng | Tổng quan quá nhiều số: 11 thẻ, 2 thẻ độ trễ trùng nhau | v0.1.42 | 🟡 chưa kiểm |
| F-65 | 🟡 vàng | "Hồ sơ sống" là màn chi tiết nhưng nằm ở menu | v0.1.42 | 🟡 chưa kiểm |
| F-66 | 🟡 vàng | /guide và /guide/:n không có tiêu đề ở header | v0.1.42 | 🟡 chưa kiểm |
| F-67 | 🟡 vàng | Lỗi đánh bóng tồn từ rà soát v0.1.27 chưa xử lý: dải tab Hệ thống tràn ở 1440px (V6), nút header khó hiểu (L10), logo (L1), tên hướng dẫn (L15), "Hộp thư ý nghĩa" (L16) | v0.1.42 | 🟡 chưa kiểm |
| F-68 | 🟡 vàng | Gen chỉ dành cho Owner, không có chỗ bật cho người khác | Hoãn | 🟡 chưa kiểm |
| F-69 | 🟡 vàng | HANDOFF-v0.1.1.md phình mãi và tên gây hiểu nhầm: 1 053 dòng / 124 KB chứa v0.1.1→v0.1.31, trùng mục v0.1.30, CLAUDE.md bắt ghi tiếp | Song song | 🟡 chưa kiểm |
| F-70 | 🟡 vàng | Nhánh cũ chưa dọn: 11 nhánh claude/* trên origin (9 đã merge squash), ~45 nhánh worktree-agent-* local; 30/100 lần CI bị huỷ do push wip liên tục | Song song (cần Boss cho phép) | 🟡 chưa kiểm |
| F-71 | 🟡 vàng | GitHub Actions: cảnh báo Node 20 khai tử, action ghim theo tag không SHA, ci.yml không khai permissions | v0.1.48 | 🟡 chưa kiểm |
| F-72 | 🟡 vàng | Tải binary genh timeout 20 giây cho cả tệp — mạng chậm ⇒ tự cập nhật hỏng âm thầm | v0.1.37 | 🟡 chưa kiểm |
| F-73 | 🟡 vàng | Không kiểm "tự lên sau khi bật lại máy": docker.service enabled, linger chỉ cảnh báo lúc cài | v0.1.37 | 🟡 chưa kiểm |
| F-74 | 🟡 vàng | Gen-hub thật chưa nghiệm thu: Boss chưa tạo token, chưa bấm Kiểm tra | v0.1.39 | 🟡 chưa kiểm |
| F-75 | 🟡 vàng | Facebook thật chưa nghiệm thu: chưa đăng nhập và đọc 1 lần, selector chỉ thử trang mẫu | v0.1.39 | 🟡 chưa kiểm |
| F-76 | 🟡 vàng | Antigravity CLI + Google thật chưa kiểm: tài khoản thứ hai, định dạng agy models, mã model Claude | v0.1.39 | 🟡 chưa kiểm |
| F-77 | 🟡 vàng | Claude Code CLI: đăng nhập thật tới cuối và tệp .credentials.json chưa kiểm | v0.1.39 | 🟡 chưa kiểm |
| F-78 | 🟡 vàng | Jev chưa có khoá thật; schema và mã model là giả định | v0.1.39 (kiểm 1 lần) · đóng băng phần riêng | 🟡 chưa kiểm |
| F-79 | 🟡 vàng | Ghi lên mạng xã hội (đăng/trả lời/nhắn) chưa làm, trễ hẹn 2 lần (QD-12 v0.1.30, ROADMAP v0.1.31) | v0.1.47 | 🟡 chưa kiểm |
| F-80 | 🟡 vàng | Cổng API chính thức cho Trang FB, IG chuyên nghiệp, Zalo OA (TikTok, LinkedIn, X) chưa có dòng mã nào | Đóng băng | 🟡 chưa kiểm |
| F-81 | 🟡 vàng | Gen ghi sang Gen-hub (kanban/warroom, ghi Phiên/Việc vào Kho) chờ Boss cho phép | v0.1.50 (chỉ khi có QD mới; mặc định hoãn) | 🟡 chưa kiểm |
| F-82 | 🟡 vàng | Phương án B (Gen-hub đọc Gen-Harness) và Playwright cho agent vòng ngoài: chờ Boss | Cắt | 🟡 chưa kiểm |
| F-83 | 🟡 vàng | Ngắt mạch 60 s cho Gen-hub; kiểm phiên MXH tự động hằng ngày; Jev phân loại từng tin MXH chưa làm | v0.1.47 (MXH) + v0.1.49 (breaker) | 🟡 chưa kiểm |
| F-84 | 🟡 vàng | Gen thiếu đánh giá Hữu ích/số đo, duyệt nháp, đề xuất Deal/Vụ việc, vai trò khác, stream; Lọc đầu và UX còn lại | v0.1.41 (đánh giá, chi phí); phần khác hoãn | 🟡 chưa kiểm |
| F-85 | 🟡 vàng | Sandbox Chromium đang TẮT, đang bù bằng cách ly container và Redis riêng | Điều kiện trước v0.1.47 | 🟡 chưa kiểm |
| F-86 | 🟡 vàng | Claude Code CLI dùng gói Pro/Max qua app tự động: rủi ro hạn chế theo điều khoản | v0.1.41 | 🟡 chưa kiểm |
| F-87 | 🟡 vàng | Kho lệch thực tế Gen-Harness: QD-12 vẫn hẹn v0.1.30, DA-1 mô tả cũ và không có Việc, thiếu PHIEN v0.1.28–v0.1.32 | Song song (cần Boss) + v0.1.50 | 🟡 chưa kiểm |
| F-88 | 🟡 vàng | v0.1.32 (effort-wip) đang dở: cần làm xong hoặc gác lại rõ ràng | v0.1.32 (đang phát hành) — đóng | 🟡 chưa kiểm |
| F-89 | 🟡 vàng | Việc phía Gen-hub G1–G4 chưa kiểm (expires_at, 2 token song song, hướng dẫn MCP, ghi chú Tri thức) | v0.1.39 | 🟡 chưa kiểm |
| F-90 | 🟡 vàng | Tài liệu lệch mã và Kho: ROADMAP, ba tài liệu thiết kế (gen-browser-agent, gen-hub-link, gen-v1), README, ARCHITECTURE/PLAN | Song song | 🟡 chưa kiểm |
| F-91 | 🟡 vàng | HANDOFF v0.1.29 "Để lại: ghi có xác nhận (v0.1.30…)" đã trượt hẹn | Song song | 🟡 chưa kiểm |
| F-92 | 🟡 vàng | Chú thích trong mã hứa phiên bản "CHỖ CẮM v0.1.30", "v0.1.30: 'post' …" đã trượt | v0.1.47 + song song | 🟡 chưa kiểm |

Tổng: 36 đứng vững (5 đỏ · 15 cam · 16 vàng), 1 bị bác, 55 vàng chưa kiểm. Không phát hiện đỏ/cam nào bị bỏ ngoài kế hoạch.

---

## (f) Rủi ro của chính kế hoạch

1. **Bật đủ bộ test trong CI (v0.1.33) có thể làm lộ các test vốn đã đỏ** (go test trên Windows/macOS, Playwright mock, pytest dưới `gh_app`), khiến v0.1.33 trễ.
   - Cách xử lý: test nào hỏng vì môi trường thì cách ly, có danh sách và hạn sửa rõ ràng. Không tắt cả bộ test.
   - Không gộp việc sửa test vào phạm vi khác.
2. **Cổng phát hành mới có thể chặn nhầm** khi E2E chập chờn hoặc lỗi mạng (F-36 chưa làm tới v0.1.48). Khi đó bản mới không lên `latest`.
   - Boss vẫn an toàn vì đứng yên ở bản cũ, nhưng bản sửa tới chậm.
   - Cách xử lý: có `workflow_dispatch` promote tay cho người bảo trì. Nếu lỗi mạng còn lặp lại thì kéo F-36 lên sớm hơn.
3. **Thời gian chín 24 giờ làm bản vá khẩn tới chậm 1 đêm.**
   - Cách xử lý: thời gian chín chỉ áp cho timer đêm. Nút "Cập nhật ngay" của Boss vẫn lấy bản mới nhất.
   - Không thêm cờ "bỏ qua chín" ở phía phát hành, vì cờ đó mở lại đúng lỗ F-9.
4. **genh là lưới an toàn**, mà v0.1.34 và v0.1.37 đều sửa chính `ops/update.go`. Một lỗi ở đây nguy hiểm hơn mọi lỗi trong app.
   - Cách xử lý: bắt buộc unit test theo từng nhánh, E2E nâng cấp có dữ liệu, và job "bản hỏng cố ý".
   - Review trước merge dùng Opus.
5. **Branch protection có thể chặn quy trình tự merge hiện tại**, ví dụ khi tên check thay đổi hoặc token thiếu quyền admin.
   - Cách xử lý: liệt kê đúng tên các required check. Kiểm bằng 1 PR thử. Ghi cách gỡ vào runbook.
6. **Phụ thuộc vào Boss**:
   - v0.1.39: nghiệm thu thật, khoảng 20 phút.
   - v0.1.40: chọn USB/NAS.
   - v0.1.44: bot Telegram.
   - v0.1.46: Tailscale.
   - v0.1.47: chốt F-85.
   - v0.1.49 (phần B) và v0.1.50 (phần ghi Kho): duyệt phạm vi / ra QD mới.
   - Song song: cho phép ghi Kho, xoá nhánh, sửa CLAUDE.md.

   Nếu Boss chưa làm kịp thì **không chặn đợt sau**: chuyển sang đợt không phụ thuộc, và trang "Việc Boss cần làm" nhắc lại.
7. **Ước lượng có thể thấp.**
   - F-1 cần 2 endpoint mới, 3 màn và e2e-live trong CI.
   - F-6 bước 2 cần một kênh ngoài app hoàn toàn mới.
   - F-79 phụ thuộc trang Facebook thật vốn hay đổi.

   Cách xử lý: đợt nào vượt 2 ngày thì cắt đôi. Không dồn thêm việc vào cùng bản.
8. **Nhịp 1 bản mỗi 1–2 ngày vẫn nhanh hơn tốc độ Boss dùng**, giống nhận xét của báo cáo lộ trình. Tính năng dễ ra đời trước khi tính năng cũ được nghiệm thu.
   - Cách xử lý: v0.1.39 (nghiệm thu) đứng trước mọi tính năng trợ lý mới (v0.1.41 trở đi).
   - Các đợt tính năng (v0.1.41 trở đi) chỉ bắt đầu khi bản trước đã chạy trên máy Owner ít nhất 1 đêm, không lỗi.
9. **Sắp lại menu (v0.1.42) phá các test so ảnh pixel và thói quen hiện tại của Boss.** Cách xử lý: hạ so ảnh xuống kiểm khói, và nhờ Boss xem thử ngay sau khi lên bản.
10. **Một số phát hiện còn là giả định**, ví dụ F-22: agy có tự chạy tool hay không. Còn 55 mục vàng chưa kiểm chứng.
    - Cách xử lý: bước kiểm chứng nằm ngay trong đợt, và kết quả có thể làm đổi cách sửa.
    - Mục vàng nào khi làm mới thấy sai thì đóng, ghi lý do.
11. **Gen đọc mail và lịch (v0.1.49) đưa dữ liệu riêng tư lên model đám mây.** Bắt buộc có QD mới, có che dữ liệu, chỉ Owner, và Boss phải duyệt trước. Nếu Boss không duyệt thì bỏ phần B của đợt này (phần A đọc dữ liệu nội bộ vẫn làm), không làm vòng.

---

## (g) Hiệu chỉnh sau phê bình

Phê bình đối chiếu kế hoạch với `0-phat-hien-da-kiem.json` (36 đứng vững, 55 vàng), 5 báo cáo (mục top 5) và các yêu cầu của Boss. Kết quả kiểm: **mọi phát hiện đỏ/cam đều đã có đợt** (F-1,2,3,4,5,6,7,8,9,10,11,12,13,16,20,22,27,32,33,35). Những gì đã sửa, và lý do:

1. **Tách v0.1.33 thành hai bản; số bản từ đó dời theo.** Bản cũ gộp F-9 + F-13 + F-10 + F-11 (3 việc M + 1 việc S), quá 2 ngày. Giờ là v0.1.33 = cổng phát hành + CI; v0.1.34 = cập nhật đêm không làm hại. Bảng đổi số: cũ .34→.35, .35→.36, .36→.37, .37→.38 + .39, .38→.40, .39→.41, .40→.42, .41→.43, .42→.44, .43→.45, .44→.46, .46→.47, .45→.48, .47→.49, .48→.50.
2. **Đợt đầu chưa thật sự làm cập nhật đêm an toàn.** F-33 (rollback hỏng khi api mới crash-loop → code cũ chạy trên schema mới, lặp lại mỗi đêm) nằm tận đợt thứ 4, trong khi cổng E2E chạy trên CSDL rỗng không bắt được migration hỏng trên dữ liệu thật. Đã kéo lên v0.1.34: F-33 lõi (cùng hàm `rollbackAndWrap` với F-10), job "bản hỏng cố ý", nâng cấp có dữ liệu từ `tags[1]`, F-37 (log container cũng làm đầy đĩa). Thêm việc dừng worker/bridge trước backup khi có migrate (khuyến nghị #2 của báo cáo vận hành, trước đây bị bỏ qua).
3. **Bẫy của cổng phát hành** mà bản cũ không nhắc tới:
   - required check không được lọc đường dẫn;
   - luật tag `v*` phải cho bot của Actions tạo tag;
   - `concurrency` của ci.yml sẽ tự huỷ khi được gọi bằng `workflow_call` trên `main`.

   Mục F-70 "thêm `paths-ignore: docs/**`" trái với branch protection, nên đã bỏ. Đã thêm tiêu chí: PR chỉ sửa tài liệu vẫn merge được, và Release không bị huỷ.
4. **Tách đợt "Kết nối chạy thật"** (vượt 2 ngày) thành v0.1.38 (cô lập agy F-22 + gói chuyển máy F-17) và v0.1.39 (nghiệm thu cùng Boss). Nhờ vậy agy được cô lập **trước khi** Boss đăng nhập thật. Bước thử F-22 trước đây bảo agy đọc `gh_master_key` thật rồi ghi kết quả vào HANDOFF, tức trái luật cứng "không lộ bí mật". Đã đổi sang tệp canary giả, và chỉ ghi lộ/không lộ.
5. **Mâu thuẫn với QD-12:** F-86 chặn cứng CLI trong việc nền. Rủi ro điều khoản gói Pro/Max là rủi ro của Owner, không thuộc luật cứng. Đã đổi thành: mặc định không dùng CLI cho việc nền; Owner vẫn thêm được sau khi xem cảnh báo + nhập PIN. Kế hoạch cũ còn sót một lỗi logic: Boss chưa dán khoá API thì sẽ không có bản tin nào. Nay bản tin vẫn gửi phần không cần model, kèm lời nhắc dán khoá.
6. **Trái quyết định "Gen ghi Gen-hub để sau":** v0.1.50 (ghi Kho) và các việc ghi Gmail/Lịch/watchdog→Kho nay được ghi rõ là mặc định hoãn, chỉ làm khi có QD mới. "Gen nhớ" lưu cục bộ nên vẫn làm. v0.1.49 có phần A luôn làm (Gen đọc Tài liệu, Deal/Vụ việc; top 5 #4f của báo cáo UX trước đây bị bỏ qua). Phần B (mail/lịch) cần QD.
7. **Lỗi làm hỏng trải nghiệm khi cập nhật:** F-21 đổi cổng mặc định sang 127.0.0.1 sẽ cắt nhân viên và điện thoại ngay trong lần cập nhật 03:00. Nay mặc định mới chỉ áp cho cài mới; máy cũ giữ hành vi hiện tại và nhận một chuông để Owner tự quyết. Có thêm test E2E-upgrade cho trường hợp này.
8. **Lỗi logic ở thang tự trị 3 mức (F-30):** agent đang ở mức 5/6 sẽ hiện là "Soạn sẵn chờ duyệt" trong khi thực tế tự gửi. Đã sửa: hiện đúng nhãn, và chỉ hiển thị thì không ghi lại mức.
9. **"Chế độ Boss":** đã ghi rõ đây là menu mặc định duy nhất, theo khung 6 mục của báo cáo UX, cộng thêm trang "Kết nối" có trạng thái thống nhất (top 5 #1–#2 của báo cáo UX, trước đây chỉ làm một phần). Thêm tiêu chí kiểm được: thanh bên ≤ 7 mục, 5 việc chính tới được trong ≤ 2 cú bấm.
10. **Tiêu chí không kiểm được, đã thay:**
    - "Ảnh chụp 1440px" → assert `scrollWidth` bằng Playwright.
    - "Bản ghi HANDOFF" → kết quả kiểm tra lưu trong CSDL và có pytest.
    - "Log in đúng v0.1.35" → so với tệp `VERSION`.
    - "Kiểm bằng log workflow" → bước kiểm có `exit 1`.
11. **Mục bị bỏ qua lặng lẽ, nay đã thêm:**
    - Quét bảo mật của F-13 (đặt ở v0.1.48, chạy dạng báo cáo).
    - Kiểm gói sao lưu ngoài máy sau mỗi lần xuất; báo lỗi khi USB chưa cắm.
    - Chuông "đĩa thiếu chỗ".
    - Nút "Duyệt & gửi" trên thẻ nháp Gen.
    - e2e-live cho "mời người" và "nối model" (top 5 #5 của báo cáo UX).
    - Token Telegram được mã hoá.
    - Regex F-56 đối chiếu với dạng mã đăng nhập Claude thật.
12. **Cỡ đợt:** bộ câu hỏi chuẩn chuyển sang việc song song. v0.1.42, v0.1.43 và v0.1.44 có sẵn đường cắt, ghi rõ việc nào được dời và việc nào không được cắt.
13. **Thứ tự:** Facebook ghi (yêu cầu trực tiếp của Boss) lên v0.1.47, trước "Bản build tái lập" (v0.1.48, việc nội bộ, mức vàng). Nhân viên & điện thoại (F-27, cam) vẫn đứng trước.

Bản trước khi phê bình còn lưu ở `0-ke-hoach-tong.md.bak-truoc-phe-binh`.
