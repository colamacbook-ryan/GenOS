# Lối tắt cho nhà phát triển. Người dùng cuối cài bằng trình cài `genh` (giai đoạn 6).
COMPOSE = docker compose -f deploy/compose.yaml --env-file .env
API = apps/api
# Cùng bản uv với CI (setup-uv `version:`) và ảnh (api/browser.Dockerfile) — lệch bản thì `uv lock --check` của CI có thể
# đỏ dù máy dev xanh. Renovate nâng cả ba trong cùng một PR (nhóm "uv", renovate.json).
UV_VERSION = 0.12.23
UV = uvx uv@$(UV_VERSION)

.PHONY: dev-up dev-ps dev-logs dev-down dev-clean secrets up down logs logs-token ps api-dev api-test api-test-app-role api-lint web-test bridge-test browser-test \
        api-sync lock test migrate seed-demo seed-demo-clean backup backup-list restore

secrets:
	@mkdir -p secrets
	@test -f secrets/gh_master_key || python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > secrets/gh_master_key
	@test -f secrets/gh_bridge_key || python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > secrets/gh_bridge_key
	@test -f secrets/gh_browser_key || python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > secrets/gh_browser_key
	@# gh_agy_key (v0.1.63, H1): khoá ký yêu cầu api/worker -> agy-box (hộp riêng Antigravity)
	@test -f secrets/gh_agy_key || python3 -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())" > secrets/gh_agy_key
	@# gh_offsite_key (v0.1.40): "Khoá khôi phục" — 6 nhóm × 5 ký tự base32 HOA nối '-', KHÔNG xuống dòng (như genh sinh)
	@test -f secrets/gh_offsite_key || python3 -c "import os,base64;k=base64.b32encode(os.urandom(19)).decode()[:30];print('-'.join(k[i:i+5] for i in range(0,30,5)),end='')" > secrets/gh_offsite_key
	@chmod 700 secrets && chmod 644 secrets/gh_master_key secrets/gh_bridge_key secrets/gh_browser_key secrets/gh_agy_key secrets/gh_offsite_key # tệp 644 để container (user khác) đọc được, thư mục 700 chặn user khác trên host
	@test -f .env || cp .env.example .env
	@echo "Đã có secrets/gh_master_key, secrets/gh_bridge_key, secrets/gh_browser_key, secrets/gh_agy_key, secrets/gh_offsite_key và .env — nhớ đổi mật khẩu trong .env"

up: secrets
	$(COMPOSE) up -d --build
	@echo "Mở https://localhost:8443/setup — mã thiết lập: make logs-token"

logs-token:
	$(COMPOSE) logs api | grep -i "Mã thiết lập" | tail -1

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

migrate:
	cd $(API) && .venv/bin/alembic upgrade heads

api-dev:
	cd $(API) && GH_COOKIE_SECURE=false .venv/bin/uvicorn gh.main:app --reload --port 8000

# F-36 (v0.1.48): cài môi trường dev đúng theo uv.lock (lần đầu, trước `make api-dev`/`api-test`); `make lock` tạo lại
# uv.lock khi đổi phụ thuộc. Cả hai dùng uv $(UV_VERSION) như CI (uvx tự tải đúng bản, không đụng uv đã cài trên máy).
api-sync:
	cd $(API) && $(UV) sync --frozen --extra dev

lock:
	cd apps/api && $(UV) lock && cd ../browser && $(UV) lock

api-test:
	cd $(API) && .venv/bin/pytest -q

# v0.1.1/1a — chạy toàn bộ test dưới role ứng dụng gh_app (không superuser, không BYPASSRLS — migration 0014)
# thay vì postgres, để bắt sớm GRANT còn thiếu. Migrate vẫn dùng superuser (xem tests/conftest.py).
api-test-app-role:
	cd $(API) && GH_TEST_APP_ROLE=1 .venv/bin/pytest -q

api-lint:
	cd $(API) && .venv/bin/ruff check gh tests && .venv/bin/mypy gh

web-test:
	npm run -w apps/web test

bridge-test:
	npm run -w apps/bridge test

# v0.1.29 — browser-worker: cần `cd apps/browser && uv sync --frozen --extra dev && .venv/bin/playwright install chromium`
# một lần. Test chạy Chromium thật trên trang mẫu (không gọi facebook.com).
browser-test:
	cd apps/browser && .venv/bin/ruff check ghb tests && .venv/bin/mypy ghb && .venv/bin/pytest -q

test: api-lint api-test bridge-test browser-test web-test

# PLAN §5.1 — dữ liệu mẫu đi qua đúng luồng raw → refinery → clean (gh/seed_demo.py). Idempotent: chạy lại
# không tạo trùng. seed-demo-clean xoá mọi kết luận/đối tượng đã sinh (không đụng raw.events — xem docstring
# đầu gh/seed_demo.py). v0.1.57: khoá an toàn — `seed` từ chối nếu thiếu cờ (--force / GH_ALLOW_SEED_DEMO=1) hoặc DB đã
# có dữ liệu thật của tổ chức (thoát mã 2); gõ `make seed-demo` là chủ ý nên đã truyền --force, còn kiểm "dữ liệu thật" vẫn chạy.
seed-demo:
	cd $(API) && .venv/bin/python -m gh.seed_demo seed --force

seed-demo-clean:
	cd $(API) && .venv/bin/python -m gh.seed_demo clear

# PLAN §5.6 — pg_dump mã hoá qua ObjectStore, vòng đời 7 ngày/4 tuần/12 tháng (gh/backup.py)
backup:
	cd $(API) && .venv/bin/python -m gh.backup run

backup-list:
	cd $(API) && .venv/bin/python -m gh.backup list

# vd: make restore BACKUP=backups/20260101T020000Z-abcd1234.pgcustom.enc
restore:
	cd $(API) && .venv/bin/python -m gh.backup restore --key $(BACKUP)

# Môi trường dev RIÊNG cạnh bản cài thật (project genh-dev, cổng 184xx) — xem docs/dev-local.md. KHÔNG đụng project gen-harness.
dev-up:
	scripts/dev-local.sh up

dev-ps:
	scripts/dev-local.sh ps

dev-logs:
	scripts/dev-local.sh logs

dev-down:
	scripts/dev-local.sh down

dev-clean:
	scripts/dev-local.sh clean
