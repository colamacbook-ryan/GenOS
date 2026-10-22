#!/usr/bin/env bash
# Sinh deploy/compose.release.yaml từ deploy/compose.yaml: thay từng dòng
# "build: { context: .., dockerfile: ... }" bằng "image: <tham chiếu đã ghim
# digest>" cho các service hiện build cục bộ (web, migrate, api, worker,
# bridge, db, browser, browser-egress, agy-box, agy-egress) — xem docs/handoff/05-installer.md mục "Phát hành":
# "compose.yaml nhúng trong genh ghim đúng digest của bản phát hành đó."
#
# migrate/api/worker/agy-box (v0.1.63, hộp riêng Antigravity) dùng CHUNG
# deploy/images/api.Dockerfile (xem deploy/compose.yaml) => CHUNG một ảnh (--api);
# mỗi dòng build: trùng văn bản của cả 4 service đều được thay bằng cùng một
# image ref. (agy-box PHẢI khai build theo đúng dạng một dòng
# `build: { context: .., dockerfile: deploy/images/api.Dockerfile }` thì mới được
# ghim; release.yml kiểm lại bằng check_agy_box_isolation.py --release.) Tương tự
# browser/browser-egress (v0.1.29) dùng chung deploy/images/browser.Dockerfile
# => chung một ảnh (--browser).
#
# Dùng trong .github/workflows/release.yml (job pin-compose), SAU khi job
# build-images đã build + push cả 4 ảnh đa kiến trúc lên GHCR và biết digest
# thật của lần build đó (build-push-action output "digest").
#
# Cách làm: thay CHÍNH XÁC từng dòng build: theo văn bản (không parse rồi
# dump lại YAML) để giữ nguyên comment, anchor (&app-env, *app-env), thụt lề
# của compose.yaml gốc — một trình dump YAML tổng quát sẽ làm mất các thứ đó.
#
# NHÚNG VÀO BINARY: apps/genh/internal/compose/embed.go nhúng sẵn
# apps/genh/internal/compose/embedded_compose.yaml qua go:embed — Locate()
# rơi về ghi tệp đó ra <installDir>/deploy/compose.yaml khi không tìm thấy
# compose.yaml nào trên đĩa (trường hợp thật: genh chạy độc lập, không có
# checkout repo). Vì vậy job build-genh (release.yml) PHẢI copy
# deploy/compose.release.yaml mà script này sinh ra ĐÈ LÊN
# apps/genh/internal/compose/embedded_compose.yaml TRƯỚC KHI `go build` —
# xem release.yml, job build-genh phụ thuộc job pin-compose.
set -euo pipefail

usage() {
	cat >&2 <<'EOF'
usage: pin-compose-images.sh <input compose.yaml> <output file> \
         --api <image ref> --web <image ref> --bridge <image ref> --db <image ref> \
         --browser <image ref>

Mỗi <image ref> dạng: ghcr.io/<owner>/gen-harness-<service>@sha256:<digest>
EOF
	exit 2
}

[ $# -ge 2 ] || usage
IN="$1"
OUT="$2"
shift 2

IMG_API=""
IMG_WEB=""
IMG_BRIDGE=""
IMG_DB=""
IMG_BROWSER=""
while [ $# -gt 0 ]; do
	case "$1" in
	--api)
		IMG_API="$2"
		shift 2
		;;
	--web)
		IMG_WEB="$2"
		shift 2
		;;
	--bridge)
		IMG_BRIDGE="$2"
		shift 2
		;;
	--db)
		IMG_DB="$2"
		shift 2
		;;
	--browser)
		IMG_BROWSER="$2"
		shift 2
		;;
	*) usage ;;
	esac
done

[ -n "$IMG_API" ] && [ -n "$IMG_WEB" ] && [ -n "$IMG_BRIDGE" ] && [ -n "$IMG_DB" ] && [ -n "$IMG_BROWSER" ] || usage
[ -f "$IN" ] || {
	echo "pin-compose-images: không thấy $IN" >&2
	exit 1
}

cp "$IN" "$OUT"

# replace_build_line <dockerfile path như xuất hiện trong compose.yaml> <image ref thay vào>
# Thay MỌI dòng khớp (sed xử lý từng dòng của cả file) — đúng ý muốn cho
# api.Dockerfile xuất hiện ở 5 service (migrate/api/worker/agy-box/agy-egress — proxy lối ra của hộp, review v0.1.63 P2-2).
replace_build_line() {
	local dockerfile="$1" image="$2"
	local pattern="build: { context: .., dockerfile: ${dockerfile} }"
	local replacement="image: ${image}"

	if ! grep -qF "$pattern" "$OUT"; then
		echo "pin-compose-images: không tìm thấy dòng sau trong $IN — compose.yaml đã đổi cấu trúc, cập nhật script này:" >&2
		echo "  $pattern" >&2
		exit 1
	fi

	local esc_pattern esc_replacement
	esc_pattern=$(printf '%s' "$pattern" | sed -e 's/[]\/$*.^[]/\\&/g')
	esc_replacement=$(printf '%s' "$replacement" | sed -e 's/[\/&]/\\&/g')
	sed -i.bak "s/${esc_pattern}/${esc_replacement}/g" "$OUT"
	rm -f "${OUT}.bak"
}

replace_build_line "apps/web/Dockerfile" "$IMG_WEB"
replace_build_line "deploy/images/api.Dockerfile" "$IMG_API"
replace_build_line "deploy/images/bridge.Dockerfile" "$IMG_BRIDGE"
replace_build_line "deploy/images/db.Dockerfile" "$IMG_DB"
replace_build_line "deploy/images/browser.Dockerfile" "$IMG_BROWSER"

# Không còn "build:" cục bộ nào sót lại cho 9 service trên — nếu còn thì một
# trong 5 lần thay ở trên đã không khớp đủ số dòng mong đợi.
remaining=$(grep -c "build: { context: \.\." "$OUT" || true)
if [ "$remaining" -ne 0 ]; then
	echo "pin-compose-images: còn $remaining dòng build: chưa được thay trong $OUT — kiểm lại danh sách service." >&2
	exit 1
fi

echo "pin-compose-images: đã sinh $OUT" >&2
