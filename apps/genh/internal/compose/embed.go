package compose

import _ "embed"

// embeddedComposeYAML là compose.yaml nhúng sẵn vào chính binary genh, dùng
// làm phương án cuối khi Locate không tìm thấy deploy/compose.yaml nào trên
// đĩa (trường hợp thật: genh chạy độc lập trên máy Owner, không có checkout
// repo Gen-Harness nào gần đó) — VÀ để đồng bộ lại compose.yaml genh đã ghi
// ra "<installDir>/deploy/compose.yaml" ở một lần cài/chạy trước, mỗi khi
// một bản genh MỚI HƠN (mang theo bản nhúng khác) chạy lại Locate (xem
// syncEmbeddedCompose trong locate.go).
//
// Ở một checkout dev/CI bình thường, Locate luôn tìm thấy compose.yaml thật
// trên đĩa trước (xem SearchCandidates) nên nội dung nhúng ở đây không được
// dùng tới — tệp embedded_compose.yaml chỉ cần TỒN TẠI để `go:embed` biên
// dịch được, và mặc định là một bản sao y hệt deploy/compose.yaml lúc build
// (dùng "build:" cục bộ, không tải được image nào — Bước 3/`genh update`
// vẫn tự báo rõ "chưa có bản phát hành" cho các service đó, không âm thầm
// lỗi).
//
// Bản phát hành THẬT (`.github/workflows/release.yml`, job pin-compose +
// build-genh) ghi ĐÈ tệp embedded_compose.yaml bằng deploy/compose.release.yaml
// đã ghim digest ("image: ghcr.io/...@sha256:...") TRƯỚC KHI build genh cho
// từng nền tảng — nhờ vậy một Owner tải genh về từ GitHub Release sẽ có
// compose.yaml đúng, dùng image đã publish sẵn, không cần checkout repo.
//
//go:embed embedded_compose.yaml
var embeddedComposeYAML []byte

// embeddedCaddyfile là bản sao deploy/proxy/Caddyfile — compose.yaml bind-mount
// "./proxy/Caddyfile" (tương đối với thư mục chứa compose.yaml), nên khi genh
// ghi compose.yaml nhúng ra "<installDir>/deploy/" nó PHẢI ghi kèm tệp này.
// Thiếu tệp, Docker tự tạo một THƯ MỤC rỗng cùng tên rồi proxy không khởi
// động được ("not a directory") — lỗi e2e chế độ release v0.1.7 bắt được.
// TestEmbeddedCaddyfileMatchesRepo giữ bản sao này khớp với deploy/.
//
//go:embed embedded_Caddyfile
var embeddedCaddyfile []byte

// embeddedSeccomp là bản sao deploy/browser/chromium-seccomp.json (F-85, v0.1.47) —
// compose.yaml dịch vụ browser dùng "seccomp=${GH_BROWSER_SECCOMP:-./browser/chromium-seccomp.json}"
// (tương đối với thư mục chứa compose.yaml) để Chromium tạo được user namespace dù cap_drop ALL.
// Thiếu tệp thì container browser không tạo được (Docker không đọc được profile), nên genh
// PHẢI ghi kèm tệp này như Caddyfile. TestEmbeddedSeccompMatchesRepo giữ bản sao khớp deploy/.
//
//go:embed embedded_chromium-seccomp.json
var embeddedSeccomp []byte
