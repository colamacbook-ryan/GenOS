#!/usr/bin/env bash
# Công cụ dữ liệu cho E2E cài thật (.github/workflows/e2e-install.yml, v0.1.34 F-35).
#
# Dùng trên runner đã cài Gen-Harness (docker compose project "gen-harness"):
#   e2e_data.sh seed                 nạp dữ liệu mẫu (python -m gh.seed_demo seed trong container api)
#   e2e_data.sh count                in "<bảng> <số dòng>" (đã sắp xếp) cho các bảng chính; bảng chưa có → -1
#   e2e_data.sh require-data <tệp>   thoát 1 nếu tệp đếm cho thấy raw.events hoặc core.persons rỗng
#   e2e_data.sh backups              in số bản sao lưu (gh.backup.list_backups)
#   e2e_data.sh pulls-since <ts>     in số sự kiện "tải ảnh" (docker events type=image event=pull) từ <ts> (unix)
#
# Không in bí mật: chỉ dùng biến môi trường SẴN CÓ trong container (POSTGRES_USER/POSTGRES_DB,
# GH_ADMIN_DATABASE_URL được mở rộng BÊN TRONG container, không đi qua log của runner).
set -euo pipefail

PROJECT="gen-harness"

# Các bảng chính phải giữ nguyên số dòng qua nâng cấp / quay về bản cũ.
TABLES=(
  core.organizations
  core.persons
  core.groups
  raw.events
  clean.meaning_units
  biz.opportunities
  biz.market_signals
  memory.entries
)

die() {
  echo "::error::$*" >&2
  exit 1
}

# container <dịch vụ> → in ID container đang chạy của dịch vụ đó (lỗi nếu không có).
container() {
  local id
  id="$(docker ps -q \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1" | head -n1)"
  [ -n "$id" ] || die "không thấy container đang chạy của dịch vụ '$1' (project $PROJECT) — Gen-Harness chưa lên?"
  printf '%s' "$id"
}

# psql_db <câu SQL> → chạy trong container db bằng tài khoản superuser của chính container (bỏ qua RLS).
psql_db() {
  local db
  db="$(container db)"
  # $1 được truyền làm đối số vị trí cho sh -c — không nội suy vào chuỗi lệnh.
  # shellcheck disable=SC2016 # $POSTGRES_USER/$POSTGRES_DB mở rộng BÊN TRONG container.
  docker exec "$db" sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "$1"' psql "$1"
}

# v0.1.57: gh.seed_demo từ chối chạy khi thiếu GH_ALLOW_SEED_DEMO=1 hoặc DB đã có dữ liệu thật (thoát mã 2). E2E luôn nạp
# vào DB mới tinh nên truyền biến môi trường (KHÔNG truyền --force: bản cũ trong ma trận nâng cấp chưa có cờ này và
# argparse của nó sẽ báo lỗi). Mã 2 = bị từ chối có chủ đích ⇒ không thử lại bằng tài khoản quản trị (sẽ bị từ chối y hệt).
cmd_seed() {
  local api rc
  api="$(container api)"
  echo "nạp dữ liệu mẫu (gh.seed_demo seed) bằng tài khoản ứng dụng…"
  rc=0
  docker exec -e GH_ALLOW_SEED_DEMO=1 "$api" python -m gh.seed_demo seed || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "OK: đã nạp dữ liệu mẫu."
    return 0
  fi
  if [ "$rc" -eq 2 ]; then
    die "gh.seed_demo từ chối nạp dữ liệu mẫu (mã 2: thiếu cờ cho phép hoặc DB đã có dữ liệu thật) — xem câu 'Chi tiết kỹ thuật' ở trên."
  fi
  # Tài khoản ứng dụng gh_app chịu RLS — có bản cũ không đủ quyền tạo tổ chức mẫu; thử lại bằng tài khoản quản trị.
  echo "::warning::seed bằng tài khoản ứng dụng lỗi — thử lại bằng GH_ADMIN_DATABASE_URL (superuser)."
  # shellcheck disable=SC2016 # $GH_ADMIN_DATABASE_URL mở rộng BÊN TRONG container, không lộ ra log runner.
  if docker exec -e GH_ALLOW_SEED_DEMO=1 "$api" sh -c 'GH_DATABASE_URL="$GH_ADMIN_DATABASE_URL" python -m gh.seed_demo seed'; then
    echo "OK: đã nạp dữ liệu mẫu (tài khoản quản trị)."
    return 0
  fi
  die "nạp dữ liệu mẫu thất bại cả bằng tài khoản ứng dụng lẫn quản trị — xem log gh.seed_demo ở trên."
}

cmd_count() {
  local t exists n
  for t in "${TABLES[@]}"; do
    exists="$(psql_db "SELECT to_regclass('$t') IS NOT NULL")"
    if [ "$exists" = "t" ]; then
      n="$(psql_db "SELECT count(*) FROM $t")"
    else
      n=-1
    fi
    printf '%s %s\n' "$t" "$n"
  done | sort
}

cmd_require_data() {
  local f="${1:-}" t n
  [ -n "$f" ] && [ -f "$f" ] || die "require-data cần đường dẫn tệp đếm (đầu ra của 'count')."
  for t in raw.events core.persons; do
    n="$(awk -v t="$t" '$1==t{print $2}' "$f")"
    if [ -z "$n" ] || [ "$n" -le 0 ]; then
      die "bảng $t có ${n:-?} dòng sau khi nạp dữ liệu mẫu — seed không có tác dụng, phép so số dòng sau nâng cấp/quay về bản cũ sẽ vô nghĩa."
    fi
  done
  echo "OK: có dữ liệu mẫu (raw.events, core.persons > 0)."
}

cmd_backups() {
  local api
  api="$(container api)"
  docker exec "$api" python -c "import asyncio; from gh.backup import list_backups; print(len(asyncio.run(list_backups())))"
}

cmd_pulls_since() {
  local ts="${1:-}"
  case "$ts" in
    '' | *[!0-9]*) die "pulls-since cần mốc thời gian unix (số giây), vd \$(date +%s)." ;;
  esac
  docker events --since "$ts" --until "$(date +%s)" \
    --filter type=image --filter event=pull --format '{{.ID}}' | wc -l | tr -d ' '
}

main() {
  local sub="${1:-}"
  [ "$#" -gt 0 ] && shift
  case "$sub" in
    seed) cmd_seed ;;
    count) cmd_count ;;
    require-data) cmd_require_data "$@" ;;
    backups) cmd_backups ;;
    pulls-since) cmd_pulls_since "$@" ;;
    *)
      echo "cách dùng: $0 {seed|count|require-data <tệp>|backups|pulls-since <unix ts>}" >&2
      exit 2
      ;;
  esac
}

main "$@"
