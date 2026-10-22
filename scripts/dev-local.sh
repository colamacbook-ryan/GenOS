#!/usr/bin/env bash
# Dựng/dừng/dọn môi trường dev riêng (project compose `genh-dev`, cổng 184xx). Xem docs/dev-local.md.
# Không bao giờ chạm project `gen-harness` (bản cài thật): mọi lệnh docker compose đều có -p genh-dev.
set -euo pipefail
cd "$(dirname "$0")/.."
PROJ=genh-dev
ENVF=.env.dev.local
DC=(docker compose -p "$PROJ" -f deploy/compose.yaml -f deploy/compose.dev.yaml --env-file "$ENVF")

rnd() { python3 -c "import secrets;print(secrets.token_hex(${1:-16}))"; }

init() {
  make -s secrets >/dev/null
  # `make secrets` tạo thêm .env (dành cho `make up` — bản cổng 8443); dev không dùng, xoá để khỏi gõ nhầm `make up`.
  [ -f .env ] && grep -q '^POSTGRES_PASSWORD=change-me$' .env && rm -f .env
  mkdir -p run-dev && chmod 777 run-dev
  if [ ! -f "$ENVF" ]; then
    umask 077
    cat >"$ENVF" <<ENV
GH_ENV=development
GH_PUBLIC_URL=https://localhost:18443
GHDEV_PORT_WEB=18443
GHDEV_PORT_API=18400
GHDEV_PORT_DB=18432
GH_HOST_LINK_DIR=$PWD/run-dev
POSTGRES_USER=gh
POSTGRES_PASSWORD=$(rnd)
POSTGRES_DB=gen_harness
GH_APP_DB_PASSWORD=$(rnd)
GH_SETUP_TOKEN=$(rnd 12)
GH_BACKUP_KEY=$(rnd 32)
ENV
    echo "Đã sinh $ENVF (bí mật ngẫu nhiên, không commit)."
  fi
}

case "${1:-}" in
  init)  init ;;
  up)    init; "${DC[@]}" up -d --build; echo "Console: https://localhost:18443/setup · mã thiết lập: grep GH_SETUP_TOKEN $ENVF" ;;
  ps)    "${DC[@]}" ps ;;
  logs)  "${DC[@]}" logs -f --tail=100 "${@:2}" ;;
  down)  "${DC[@]}" down ;;
  clean) "${DC[@]}" down -v --remove-orphans ;;
  *)     echo "Dùng: $0 {up|ps|logs [dịch vụ]|down|clean}   (clean = xoá luôn volume genh-dev_*)"; exit 2 ;;
esac
