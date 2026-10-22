#!/bin/sh
# Gen-Harness — bootstrap một lệnh (Linux/macOS).
#
#   curl -fsSL https://github.com/Genesis-ryan-84-0567536339/Gen-Harness/releases/latest/download/install.sh | sh
#
# Việc duy nhất của tệp này: tải đúng binary genh cho máy, kiểm SHA-256,
# thêm vào PATH, rồi giao lại cho `genh install` (xem
# docs/handoff/05-installer.md). Không giả định có gì ngoài `sh` + mạng +
# curl HOẶC wget — không cần Docker, git, Python, Node… có sẵn.
set -eu

REPO="Genesis-ryan-84-0567536339/Gen-Harness"
RELEASE_BASE="https://github.com/${REPO}/releases/latest/download"
INSTALL_ROOT="${GEN_HARNESS_HOME:-$HOME/.gen-harness}"
BIN_DIR="${INSTALL_ROOT}/bin"
# SECRETS_FILE: config.Paths.ConfigDir()/secrets.json do secretgen.Ensure ghi
# ở Bước 4 — có tệp này nghĩa là máy ĐÃ có một bản cài (xem main() dưới cùng).
SECRETS_FILE="${INSTALL_ROOT}/config/secrets.json"

log() { printf '%s\n' "$*" >&2; }
die() { log "genh: $*"; exit 1; }

# GEN_HARNESS_RELEASE_TAG (tuỳ chọn): cài ĐÚNG tag này thay vì bản chính thức (latest) —
# dành cho CI/E2E kiểm bản thử (prerelease) trước khi promote; người dùng bình thường không đặt.
# Máy đã cài thì main() chạy `genh update --no-self-update` để genh không tự thay bản ghim bằng latest.
RELEASE_LABEL="bản phát hành mới nhất"
PINNED_TAG=""
if [ -n "${GEN_HARNESS_RELEASE_TAG:-}" ]; then
	# Regex thật (glob `v[0-9]*.…` lọt cả 'v1a.2b.3c') — cùng định dạng với job meta của release.yml.
	# `case` chặn trước ký tự lạ (kể cả xuống dòng: grep xét TỪNG dòng, một dòng đúng là lọt).
	case "$GEN_HARNESS_RELEASE_TAG" in
	*[!0-9A-Za-z.-]*) die "GEN_HARNESS_RELEASE_TAG không hợp lệ: chỉ được chữ, số, '.', '-' (vd v0.1.33)." ;;
	esac
	if ! printf '%s' "$GEN_HARNESS_RELEASE_TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.]+)?$'; then
		die "GEN_HARNESS_RELEASE_TAG không hợp lệ: '$GEN_HARNESS_RELEASE_TAG' — cần dạng vMAJOR.MINOR.PATCH[-PRERELEASE] (vd v0.1.33)."
	fi
	PINNED_TAG="$GEN_HARNESS_RELEASE_TAG"
	RELEASE_BASE="https://github.com/${REPO}/releases/download/${PINNED_TAG}"
	RELEASE_LABEL="bản $PINNED_TAG"
	log "genh: cài đúng bản $PINNED_TAG (GEN_HARNESS_RELEASE_TAG)"
fi

# FETCH_ATTEMPTS / FETCH_RETRY_DELAY: số lần thử mỗi tệp và số giây chờ giữa hai lần.
FETCH_ATTEMPTS=3
FETCH_RETRY_DELAY=2

# fetch <url> <đích>: dùng curl nếu có, không thì wget; thử lại tối đa
# FETCH_ATTEMPTS lần khi mạng chập chờn. Chỉ coi là hỏng khi RẢNH (không
# nhận dữ liệu) chứ không giới hạn cả tệp: curl dưới 1 KB/s suốt 60 giây ⇒
# mã 28; wget -T 60. Lỗi máy chủ trả về rõ ràng (curl 22 với HTTP 4xx như
# 404, wget 8) KHÔNG thử lại — trả mã ngay để fetch_failed báo lỗi dễ hiểu.
# curl 22 với HTTP 5xx/408/429 (GitHub/CDN quá tải tạm thời) VẪN thử lại, như
# downloadWithRetry của genh — curl in mã HTTP qua -w kể cả khi -f báo lỗi.
# Không dùng `wget --tries` (busybox wget không có).
fetch() {
	url="$1"
	dest="$2"
	if command -v curl >/dev/null 2>&1; then
		fetch_tool=curl
		fetch_fatal_rc=22
	elif command -v wget >/dev/null 2>&1; then
		fetch_tool=wget
		fetch_fatal_rc=8
	else
		die "cần curl hoặc wget để tải genh, máy này không có cái nào."
	fi
	fetch_attempt=1
	while :; do
		fetch_rc=0
		fetch_http=""
		if [ "$fetch_tool" = curl ]; then
			fetch_http=$(curl -fsSL --connect-timeout 30 --speed-limit 1024 --speed-time 60 -w '%{http_code}' -o "$dest" "$url") || fetch_rc=$?
		else
			wget -q -T 60 -O "$dest" "$url" || fetch_rc=$?
		fi
		if [ "$fetch_rc" -eq 0 ]; then
			return 0
		fi
		fetch_fatal=0
		if [ "$fetch_rc" -eq "$fetch_fatal_rc" ]; then
			case "$fetch_http" in
			5?? | 408 | 429) ;;
			*) fetch_fatal=1 ;;
			esac
		fi
		if [ "$fetch_fatal" -eq 1 ] || [ "$fetch_attempt" -ge "$FETCH_ATTEMPTS" ]; then
			return "$fetch_rc"
		fi
		fetch_attempt=$((fetch_attempt + 1))
		log "genh: tải ${url##*/} bị ngắt ($fetch_tool mã lỗi $fetch_rc${fetch_http:+, HTTP $fetch_http}) — thử lại lần $fetch_attempt/$FETCH_ATTEMPTS sau $FETCH_RETRY_DELAY giây…"
		sleep "$FETCH_RETRY_DELAY"
	done
}

# fetch_failed <tệp>: báo lỗi tải dễ hiểu thay cho dòng 404 trơ trọi của curl/wget.
fetch_failed() {
	if [ -n "$PINNED_TAG" ]; then
		die "không tải được $1 của bản $PINNED_TAG (tag không tồn tại hoặc Release thiếu asset) — kiểm lại GEN_HARNESS_RELEASE_TAG, hoặc bỏ biến này để cài bản chính thức mới nhất."
	fi
	die "không tải được $1 từ ${RELEASE_LABEL} — kiểm tra mạng (mở được github.com không) rồi chạy lại lệnh cài."
}

detect_os() {
	case "$(uname -s)" in
	Linux) echo "linux" ;;
	Darwin) echo "darwin" ;;
	*) die "hệ điều hành $(uname -s) chưa được hỗ trợ (chỉ Linux/macOS — Windows dùng install.ps1)." ;;
	esac
}

detect_arch() {
	case "$(uname -m)" in
	x86_64 | amd64) echo "amd64" ;;
	arm64 | aarch64) echo "arm64" ;;
	*) die "kiến trúc $(uname -m) chưa được hỗ trợ." ;;
	esac
}

sha256_of() {
	file="$1"
	if command -v sha256sum >/dev/null 2>&1; then
		sha256sum "$file" | awk '{print $1}'
	elif command -v shasum >/dev/null 2>&1; then
		shasum -a 256 "$file" | awk '{print $1}'
	else
		die "cần sha256sum hoặc shasum để kiểm chữ ký binary, máy này không có cái nào."
	fi
}

verify_checksum() {
	bin_file="$1"
	bin_name="$2"
	checksums_file="$3"

	# So khớp theo tên tệp cuối cùng (basename), vì checksums.txt có thể ghi
	# "genh-linux-amd64" hoặc "./genh-linux-amd64" tuỳ công cụ tạo ra nó.
	want=$(awk -v n="$bin_name" '{ f=$2; sub(/^\.\//, "", f); if (f == n) print $1 }' "$checksums_file")
	if [ -z "$want" ]; then
		die "không tìm thấy $bin_name trong checksums.txt của bản phát hành — dừng, không chạy."
	fi

	got=$(sha256_of "$bin_file")
	if [ "$got" != "$want" ]; then
		die "SHA-256 của $bin_name không khớp checksums.txt (muốn $want, được $got) — dừng, không chạy."
	fi
}

# add_to_path <dòng export>: ghi vào rc file của shell hiện có (idempotent —
# không thêm trùng), không cần quyền admin vì chỉ ghi vào $HOME.
add_to_path() {
	export_line="export PATH=\"${BIN_DIR}:\$PATH\""
	rc_file="$HOME/.profile"
	case "${SHELL:-}" in
	*/zsh) rc_file="$HOME/.zshrc" ;;
	*/bash) rc_file="$HOME/.bashrc" ;;
	esac

	if [ -f "$rc_file" ] && grep -qF "$BIN_DIR" "$rc_file" 2>/dev/null; then
		return 0
	fi
	printf '\n# Gen-Harness (genh)\n%s\n' "$export_line" >>"$rc_file" 2>/dev/null ||
		log "genh: không tự thêm PATH được vào $rc_file — tự thêm: $export_line"
}

main() {
	os=$(detect_os)
	arch=$(detect_arch)
	asset="genh-${os}-${arch}"

	log "genh: đang tải ${asset} từ ${RELEASE_LABEL}…"
	mkdir -p "$BIN_DIR"
	tmp_dir=$(mktemp -d "${TMPDIR:-/tmp}/genh-install.XXXXXX")
	trap 'rm -rf "$tmp_dir"' EXIT

	fetch "${RELEASE_BASE}/${asset}" "${tmp_dir}/${asset}" || fetch_failed "$asset"
	fetch "${RELEASE_BASE}/checksums.txt" "${tmp_dir}/checksums.txt" || fetch_failed checksums.txt

	verify_checksum "${tmp_dir}/${asset}" "$asset" "${tmp_dir}/checksums.txt"

	chmod +x "${tmp_dir}/${asset}"
	mv "${tmp_dir}/${asset}" "${BIN_DIR}/genh"

	add_to_path
	PATH="${BIN_DIR}:$PATH"
	export PATH

	log "genh: đã cài vào ${BIN_DIR}/genh — mở phiên shell mới để PATH có hiệu lực lâu dài."

	# SỬA LỖI (HANDOFF-v0.1.1.md "Lỗi cần sửa" #1, v0.1.3): `genh install` trên
	# máy ĐÃ CÀI sẽ dựng lại container, BỎ QUA backup + di trú → mất dữ liệu.
	# Nên: máy chưa cài (chưa có secrets.json) → install; máy đã cài → từ
	# v0.1.5 chạy luôn `genh update` (tự backup + rollback khi lỗi, xem
	# internal/ops.RunUpdate; tự tải genh mới qua internal/selfupdate).
	if [ -f "$SECRETS_FILE" ]; then
		if [ -n "$PINNED_TAG" ]; then
			# Ghim tag: KHÔNG để genh tự thay binary bằng releases/latest (selfupdate) — sẽ âm thầm bỏ bản ghim.
			log "genh: máy này đã cài Gen-Harness từ trước — đang chạy 'genh update --no-self-update' để nâng cấp dịch vụ lên đúng $PINNED_TAG…"
			exec "${BIN_DIR}/genh" update --no-self-update
		fi
		log "genh: máy này đã cài Gen-Harness từ trước — đang chạy 'genh update' để nâng cấp dịch vụ lên đúng bản mới…"
		exec "${BIN_DIR}/genh" update
	fi

	exec "${BIN_DIR}/genh" install
}

main "$@"
