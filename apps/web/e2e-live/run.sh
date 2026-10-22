#!/usr/bin/env bash
# E2E trên hệ thống thật: Postgres + Redis cục bộ, api + worker thật, bridge giả (QR, danh bạ, tin nhắn),
# model giả tương thích OpenAI, máy chủ MCP giả (giai đoạn 5.3 luồng 8). Dùng CSDL gh_live và Redis db 3 (bị xoá
# mỗi lần chạy). Chạy nối tiếp live-phase2 (thiết lập, tin thật, sàng lọc) rồi live-phase3 (luồng 4–8: cơ hội,
# agent soạn/duyệt/gửi, hợp nhất danh tính, plugin lỗi liên tục, MCP) trong CÙNG một phiên api/worker/CSDL —
# live-phase3 tiếp tục đúng dữ liệu live-phase2 để lại (xem docs/reports/phase-5-e2e-live.md).
#   cd apps/web && bash e2e-live/run.sh
# v0.1.35 (F-14): `LIVE_SPECS` chọn tệp spec (mặc định "live-phase2 live-phase3" như trên — chạy tay giữ nguyên).
# CI (job `api` của .github/workflows/ci.yml) chạy bản rút gọn tự đủ: `LIVE_SPECS=live-ci bash e2e-live/run.sh`.
# Playwright đỏ → in đuôi api.log + worker.log ra stderr (khoá GH_* chỉ nằm trong env, không bao giờ vào log).
set -euo pipefail
LIVE_SPECS=${LIVE_SPECS:-"live-phase2 live-phase3"}
for tool in psql redis-cli curl; do
  command -v "$tool" >/dev/null || { echo "Thiếu lệnh '$tool' — cài trước khi chạy e2e thật (Ubuntu: apt-get install postgresql-client redis-tools curl)" >&2; exit 1; }
done
HERE=$(cd "$(dirname "$0")" && pwd); API=$(cd "$HERE/../../api" && pwd); WEB=$(cd "$HERE/.." && pwd)
export LIVE_OUT=${LIVE_OUT:-$WEB/test-results/live-shots}; OUT=$LIVE_OUT; mkdir -p "$OUT"
# `export` (không chỉ gán $OUT cục bộ) để fake_bridge.py chạy nền bên dưới cũng thấy đúng LIVE_OUT — thiếu dòng
# này trước đây khiến `bridge_sent.log` (luồng 5.3.5, ghi khi có tin gửi thật) rơi vào cwd thay vì $OUT.
PG=${GH_LIVE_PG:-postgresql://postgres:postgres@localhost:5432}
export GH_DATABASE_URL=${PG/postgresql:/postgresql+asyncpg:}/gh_live GH_REDIS_URL=redis://localhost:6379/3
export GH_SETUP_TOKEN=live-setup-token GH_COOKIE_SECURE=false GH_CLI_HOME=$OUT/agy/.gemini/antigravity-cli
export GH_MASTER_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
export GH_BRIDGE_KEY=$(python3 -c 'import os,base64;print(base64.b64encode(os.urandom(32)).decode())')
PIDS=()
# `npx vite` chạy vite thành tiến trình CHÁU của npx (npx → sh → node vite) — chỉ kill npx thì vite mồ côi vẫn giữ
# cổng, lần chạy sau (`--strictPort`) hỏng vì cổng bận. Kill cả cây con (sâu trước) rồi mới tới tiến trình nền.
killtree() { local c; for c in $(pgrep -P "$1" 2>/dev/null); do killtree "$c"; done; kill "$1" 2>/dev/null || true; }
cleanup() { for p in "${PIDS[@]}"; do killtree "$p"; done; }
trap cleanup EXIT
psql "$PG/postgres" -qc "DROP DATABASE IF EXISTS gh_live WITH (FORCE)" -c "CREATE DATABASE gh_live"
redis-cli -n 3 flushdb >/dev/null
(cd "$API" && .venv/bin/alembic upgrade heads >/dev/null)

# Khoá ký ed25519 cho luồng 5.3.7 (nạp plugin từ tệp, chữ ký THẬT — không phải chuỗi giả như test có mock):
# khoá riêng ở đây đóng vai "người phát triển plugin" ký ngoài trình duyệt (sign_plugin.py); khoá công khai
# tương ứng vào GH_PLUGIN_TRUSTED_SIGNING_KEYS để api tin cậy đúng cặp khoá này khi kiểm chữ ký.
PLUGIN_KEY=$OUT/plugin_signing.key
# python3 hệ thống thiếu _cffi_backend hoạt động được (cryptography cài kèm rust bị lỗi nạp) — dùng đúng venv
# của api (nơi `cryptography` chạy thật cho gh.plugins_api.routes._verify_signature) để tránh treo/âm thầm rỗng.
export GH_PLUGIN_TRUSTED_SIGNING_KEYS=$("$API/.venv/bin/python" -c "
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization as s
import base64
k = Ed25519PrivateKey.generate()
open('$PLUGIN_KEY', 'wb').write(k.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()))
print(base64.b64encode(k.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)).decode())
")
# `export VAR=$(cmd)` nuốt mã thoát của cmd (chỉ giữ mã thoát của export) — set -e sẽ KHÔNG bắt được lệnh trên
# nếu nó lỗi, nên kiểm tay: rỗng nghĩa là chữ ký ed25519 sẽ luôn sai ở luồng 5.3.7, thà dừng sớm còn hơn chạy
# tiếp rồi báo lỗi khó hiểu ở playwright.
[ -n "$GH_PLUGIN_TRUSTED_SIGNING_KEYS" ] || { echo "Không sinh được khoá ký plugin (xem lỗi python ở trên)" >&2; exit 1; }

# Seed một plugin @e2e/exploder (origin marketplace, đã duyệt) TRƯỚC khi api khởi động — PluginManager chỉ nạp
# `ops.plugins` đang 'approved' lúc boot (gh.app.build_plugin_manager); dùng để luồng 5.3.7 có breaker THẬT tự mở
# khi lỗi liên tục (`tests.plugin_fixtures:Exploder` — luôn raise), tách biệt hoàn toàn khỏi luồng nghiệp vụ
# chính bằng stream riêng "e2e.plugin.explode" (xem explode_plugin.py). Việc "nạp plugin TỪ TỆP qua UI thật"
# (chữ ký + PIN + quyền xin) vẫn được live-phase3 kiểm riêng qua POST /plugins/local — plugin đó cố tình KHÔNG
# được kích hoạt runtime thật, vì bản thân backend chưa hỗ trợ chạy mã local_file (xem docstring
# gh.plugins_api.routes.install_local) — không phải một khoảng trống của bộ test.
psql "$PG/gh_live" -qc "
INSERT INTO ops.plugins (package, name, layer, origin, version, is_enabled, load_order, sandbox, permissions,
                          signature_ok, manifest, permissions_status)
VALUES ('@e2e/exploder', 'E2E Exploder (giai đoạn 5.3 luồng 7)', 'extension', 'marketplace', '1.0.0', true, 500,
        '{\"mode\":\"inprocess\",\"memory_mb\":64,\"timeout_s\":5,\"net\":\"none\"}'::jsonb, '{}', true,
        '{\"package\":\"@e2e/exploder\",\"name\":\"E2E Exploder (giai đoạn 5.3 luồng 7)\",\"version\":\"1.0.0\",
          \"layer\":\"extension\",\"entry\":\"tests.plugin_fixtures:Exploder\",\"permissions\":[],
          \"events\":{\"subscribe\":[\"e2e.plugin.explode\"],\"publish\":[]},
          \"sandbox\":{\"mode\":\"inprocess\",\"memory_mb\":64,\"timeout_s\":5,\"net\":\"none\"},
          \"load_order\":500,\"removable\":true,\"can_disable\":true,
          \"breaker\":{\"failure_threshold\":3,\"window_s\":120,\"cooldown_s\":120}}'::jsonb, 'approved');
"

python3 "$HERE/fake_llm.py" > "$OUT/llm.log" 2>&1 & PIDS+=($!)
python3 "$HERE/fake_mcp.py" > "$OUT/mcp.log" 2>&1 & PIDS+=($!)
(cd "$API" && exec .venv/bin/uvicorn gh.main:app --port 8000) > "$OUT/api.log" 2>&1 & PIDS+=($!)
(cd "$API" && exec .venv/bin/arq gh.worker.WorkerSettings) > "$OUT/worker.log" 2>&1 & PIDS+=($!)
(cd "$WEB" && exec npx vite --port 5175 --strictPort) > "$OUT/web.log" 2>&1 & PIDS+=($!)
for _ in $(seq 1 60); do curl -sf localhost:5175/api/v1/health >/dev/null && break; sleep 1; done
curl -sf localhost:5175/api/v1/health >/dev/null || { echo "api/web chưa lên sau 60 giây — xem $OUT/api.log, $OUT/web.log" >&2; tail -n 120 "$OUT/api.log" "$OUT/web.log" >&2 || true; exit 1; }
export ORG=$(psql "$PG/gh_live" -tAc "SELECT id FROM core.organizations")
SCAN_AFTER=5 "$API/.venv/bin/python" "$HERE/fake_bridge.py" > "$OUT/bridge.log" 2>&1 & PIDS+=($!)
sleep 2
# shellcheck disable=SC2086 # LIVE_SPECS cố ý tách theo khoảng trắng thành nhiều tên spec
cd "$WEB" && LIVE_OUT=$OUT SEND_SCRIPT=$HERE/send.py DETECT_SCRIPT=$HERE/detect_identities.py \
  SIGN_SCRIPT=$HERE/sign_plugin.py PLUGIN_SIGN_KEY=$PLUGIN_KEY EXPLODE_SCRIPT=$HERE/explode_plugin.py \
  PY=$API/.venv/bin/python LIVE_BASE_URL=http://localhost:5175 LIVE_DB_URL=$PG/gh_live \
  npx playwright test -c playwright.live.config.ts $LIVE_SPECS || {
  code=$?
  for f in api.log worker.log; do
    echo "──── đuôi $OUT/$f (120 dòng) ────" >&2
    tail -n 120 "$OUT/$f" >&2 || true
  done
  echo "E2E thật đỏ (LIVE_SPECS=$LIVE_SPECS, mã $code) — ảnh chụp + log ở $OUT, vết Playwright ở $WEB/test-results/live" >&2
  exit "$code"
}
