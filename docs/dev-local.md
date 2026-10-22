# Môi trường dev riêng (genh-dev)

## Mục đích
Chạy một bản Gen-Harness thử NGAY TRÊN MÁY dev, cạnh bản cài thật, để bấm thử giao diện và chạy test mà không ảnh hưởng dữ liệu thật.

## Nguyên tắc: không đụng bản thật
- Bản thật là project compose `gen-harness` (cổng 8443) và các container `gen-hub-*`, `genhub-*`. Môi trường dev không bao giờ stop,
  restart, exec hay mount volume của chúng.
- Mọi lệnh dev đi qua `scripts/dev-local.sh`: luôn có `-p genh-dev` ⇒ container `genh-dev-*`, volume `genh-dev_*`.
- Đừng gõ `make up`/`make down` để thử dev (đó là bản cổng 8443). Chỉ dùng các lệnh `make dev-*` dưới đây.

## Lệnh
| Lệnh | Việc làm |
|---|---|
| `make dev-up` | Sinh bí mật + `.env.dev.local` (lần đầu), dựng và chạy stack (`up -d --build`) |
| `make dev-ps` | Xem trạng thái container `genh-dev-*` |
| `make dev-logs` | Theo dõi log (100 dòng cuối). Một dịch vụ: `scripts/dev-local.sh logs api` |
| `make dev-down` | Dừng và gỡ container, GIỮ volume (dữ liệu dev còn) |
| `make dev-clean` | Dừng, gỡ container VÀ xoá volume `genh-dev_*` (dữ liệu dev mất, làm lại từ đầu) |

## Cổng (chỉ nghe 127.0.0.1)
- Console (HTTPS, chứng chỉ tự ký): `https://localhost:18443`
- API: `127.0.0.1:18400`
- Postgres: `127.0.0.1:18432`

## Xem trong VS Code
`Ctrl+Shift+P` → `Simple Browser: Show` → nhập `https://localhost:18443`. Nếu trình duyệt báo chứng chỉ không tin cậy
thì chấp nhận (chứng chỉ tự ký, chỉ dùng cục bộ).

## Mã thiết lập
Lần đầu mở Console sẽ vào `/setup`. Mã thiết lập là giá trị `GH_SETUP_TOKEN` trong `.env.dev.local`
(lấy bằng `grep GH_SETUP_TOKEN .env.dev.local`). Tệp này nằm ngoài git (`*.local`), chứa bí mật: không dán vào chat, log hay commit.

## Chạy test api (Postgres + Redis riêng, cổng lạ)
Test api cần Postgres 16 + pgvector + pg_partman và Redis, đúng như job `api` trong `.github/workflows/ci.yml`. Dựng hai container
tạm ở cổng không trùng stack dev hay bản thật (đã chạy thử đúng các lệnh này):
```
docker build -f deploy/images/db.Dockerfile -t ghdev-test-db .
docker run -d --name ghdev-test-db -p 127.0.0.1:55432:5432 -e POSTGRES_PASSWORD=postgres ghdev-test-db
docker run -d --name ghdev-test-redis -p 127.0.0.1:56379:6379 \
  redis:7-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
make api-sync          # lần đầu: cài phụ thuộc theo uv.lock
cd apps/api
export GH_TEST_PG=postgresql://postgres:postgres@127.0.0.1:55432
export GH_TEST_REDIS=redis://127.0.0.1:56379/15 GH_TEST_BROWSER_REDIS=redis://127.0.0.1:56379/13
.venv/bin/pytest -q tests/test_account.py      # hoặc cả bộ: .venv/bin/pytest -q
```
Chờ Postgres sẵn sàng trước khi chạy: `docker exec ghdev-test-db pg_isready -U postgres`. Digest Redis lấy từ ci.yml; nếu ci.yml
đổi thì theo ci.yml. Tuyệt đối không trỏ `GH_TEST_PG` vào cổng 5432 của máy hay vào Postgres của bản thật.

## Kiểm trước/sau
Lưu `docker ps --format '{{.Names}} {{.Status}}' | sort` trước khi làm và so lại sau: các container `gen-harness-*`, `gen-hub-*`,
`genhub-*` phải còn nguyên, thời gian chạy (Up …) không bị đặt lại.

## Dọn dẹp
- Xong phiên: `make dev-down` (giữ dữ liệu) hoặc `make dev-clean` (xoá sạch dữ liệu dev).
- Test api: `docker rm -f ghdev-test-db ghdev-test-redis && docker rmi ghdev-test-db`.
- Thư mục `run-dev/`, `.env.dev.local` do script tạo, đã nằm ngoài git; xoá tay nếu muốn làm lại hoàn toàn.
