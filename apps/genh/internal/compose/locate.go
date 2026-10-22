package compose

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/hostlink"
)

// NoticeWriter là nơi Locate (biến thể KHÔNG đồng bộ, xem doc-comment của
// Locate/LocateAndSync bên dưới) in dòng nhắc một-dòng khi phát hiện
// compose.yaml GENH QUẢN LÝ đã lệch bản nhúng của binary đang chạy — mặc
// định os.Stderr, đổi được trong test để bắt output mà không cần ghi ra
// stderr thật.
var NoticeWriter io.Writer = os.Stderr

// EnvOverrideVar cho phép chỉ thẳng đường dẫn compose.yaml, bỏ qua dò tìm —
// dùng khi test hoặc khi Owner cài từ một bản sao repo không theo cấu trúc
// mặc định.
const EnvOverrideVar = "GENH_COMPOSE_FILE"

// SearchCandidates liệt kê các đường dẫn Locate sẽ thử theo đúng thứ tự ưu
// tiên — hàm thuần (không đụng đĩa), test được độc lập với hệ thống tệp
// thật. installDir/cwd/exeDir đều có thể rỗng (Locate tự điền giá trị thật
// khi gọi Locate, test tự truyền cố định để có kết quả xác định).
func SearchCandidates(installDir, cwd, exeDir string) []string {
	var candidates []string

	if v := os.Getenv(EnvOverrideVar); v != "" {
		candidates = append(candidates, v)
	}

	if installDir != "" {
		candidates = append(candidates, filepath.Join(installDir, "deploy", "compose.yaml"))
	}

	// Dò lên tối đa 8 cấp cha từ thư mục làm việc và từ thư mục chứa binary
	// genh — đủ sâu cho cấu trúc repo/monorepo thông thường
	// (apps/genh/cmd/genh -> gốc repo là 3 cấp), dư dả cho cả trường hợp
	// build ở thư mục con sâu hơn.
	for _, start := range []string{cwd, exeDir} {
		if start == "" {
			continue
		}
		candidates = append(candidates, ascendingCandidates(start, 8)...)
	}

	return candidates
}

func ascendingCandidates(start string, maxUp int) []string {
	var out []string
	dir := start
	for i := 0; i <= maxUp; i++ {
		out = append(out, filepath.Join(dir, "deploy", "compose.yaml"))
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
		dir = parent
	}
	return out
}

// Locate tìm deploy/compose.yaml thật trên máy theo thứ tự SearchCandidates,
// trả về đường dẫn đầu tiên tồn tại — dùng cho MỌI lệnh vận hành ĐỌC compose
// (status/open/logs/backup/…). KHÔNG tự ghi đè compose.yaml GENH QUẢN LÝ nếu
// đã lệch bản nhúng của binary đang chạy — xem SỬA LỖI ở doc-comment
// LocateAndSync; ở đây chỉ in một dòng nhắc qua NoticeWriter nếu phát hiện
// lệch, để Owner biết chạy `genh update`. Nếu không tìm thấy ở đâu cả
// (trường hợp thật: genh chạy độc lập trên máy Owner, không có checkout repo
// gần đó), rơi về ghi compose.yaml NHÚNG SẴN trong binary (embeddedComposeYAML,
// xem embed.go) ra "<installDir>/deploy/compose.yaml" rồi trả về đường dẫn đó
// — CHỈ khi installDir khác rỗng VÀ tệp đó CHƯA từng tồn tại (không phải
// đồng bộ lại — đó là việc của LocateAndSync).
func Locate(installDir string) (string, error) {
	return locate(installDir, false)
}

// LocateAndSync là biến thể của Locate CHỈ dùng trong `genh update` (và
// `genh install`, xem internal/install) — hai lệnh có TRÁCH NHIỆM đưa máy
// lên đúng phiên bản mới, khác mọi lệnh vận hành khác (status/open/logs/
// backup/doctor/…) chỉ ĐỌC trạng thái hiện có.
//
// ĐỒNG BỘ tệp GENH QUẢN LÝ (SỬA LỖI docs/reports/HANDOFF-v0.1.1.md mục "Lỗi
// cần sửa" #5): candidate "<installDir>/deploy/compose.yaml" khác mọi
// candidate khác ở chỗ CHÍNH genh đã ghi ra nó (lần cài đầu, hoặc lần chạy
// trước của một bản genh cũ hơn) — không phải một checkout repo Owner tự
// quản lý. Vì vậy, mỗi lần candidate này đã tồn tại, LocateAndSync tự đối
// chiếu với bản nhúng CỦA CHÍNH BINARY ĐANG CHẠY và ghi lại (giữ bản cũ ở
// "compose.yaml.bak") nếu khác.
//
// SỬA LỖI TIẾP (v0.1.2, mục #3): bản v0.1.1 gọi hành vi đồng bộ này từ
// NGAY TRONG Locate — chạy ở MỌI lệnh `genh ...` (kể cả `genh status`/`genh
// logs`/`genh backup`…, vì mọi lệnh đều LocatePath() để biết compose.yaml ở
// đâu), nên bất kỳ lệnh vận hành nào cũng có thể ÂM THẦM ghi đè compose.yaml
// của Owner (kể cả compose.yaml Owner đã tự sửa tay) ngoài ý muốn, chỉ vì
// binary genh đang chạy mang một bản nhúng khác. Từ bản này, CHỈ
// LocateAndSync (update/install) mới thật sự ghi; Locate (mọi lệnh khác)
// chỉ in một dòng nhắc.
func LocateAndSync(installDir string) (string, error) {
	return locate(installDir, true)
}

// locate là phần thân dùng chung của Locate/LocateAndSync — sync=true đồng
// bộ compose.yaml GENH QUẢN LÝ đã lệch bản nhúng (ghi lại + giữ .bak),
// sync=false chỉ in một dòng nhắc qua NoticeWriter nếu lệch, không đụng tệp.
func locate(installDir string, sync bool) (string, error) {
	path, err := locateInner(installDir, sync)
	if err != nil {
		return "", err
	}
	exportSeccompEnv(path)
	return path, nil
}

// SeccompEnv là biến compose.yaml dùng để chọn profile seccomp của container browser.
const SeccompEnv = "GH_BROWSER_SECCOMP"

// exportSeccompEnv đặt GH_BROWSER_SECCOMP thành đường dẫn TUYỆT ĐỐI của profile cạnh compose.yaml
// (mọi lệnh docker compose genh chạy kế thừa os.Environ) — không phụ thuộc compose bản đang dùng có
// phân giải "./browser/…" theo thư mục compose hay theo thư mục hiện tại. Owner đã tự đặt biến thì giữ nguyên.
func exportSeccompEnv(composePath string) {
	if os.Getenv(SeccompEnv) != "" {
		return
	}
	p := filepath.Join(filepath.Dir(composePath), "browser", "chromium-seccomp.json")
	if info, err := os.Stat(p); err == nil && !info.IsDir() {
		if abs, err := filepath.Abs(p); err == nil {
			_ = os.Setenv(SeccompEnv, abs)
		}
	}
}

func locateInner(installDir string, sync bool) (string, error) {
	cwd, _ := os.Getwd()
	exeDir := ""
	if exe, err := os.Executable(); err == nil {
		exeDir = filepath.Dir(exe)
	}

	var managedPath string
	if installDir != "" {
		managedPath = filepath.Join(installDir, "deploy", "compose.yaml")
		// Hộp thư Console ↔ genh (bind mount của api, xem internal/hostlink) phải có
		// TRƯỚC `up` — nếu không Docker tự tạo thư mục với chủ root và api không ghi
		// được yêu cầu cập nhật. Đường dẫn đi qua biến môi trường (mọi lệnh docker
		// compose của genh kế thừa os.Environ) để đúng cả khi compose.yaml nằm chỗ
		// khác (GENH_COMPOSE_FILE, repo) — "../run" chỉ đúng với bản cài genh quản lý.
		if err := hostlink.EnsureDir(installDir); err != nil {
			return "", fmt.Errorf("tạo %s: %w", hostlink.Dir(installDir), err)
		}
		_ = os.Setenv(hostlink.EnvDir, hostlink.Dir(installDir))
	}

	for _, c := range SearchCandidates(installDir, cwd, exeDir) {
		info, err := os.Stat(c)
		if err != nil || info.IsDir() {
			continue
		}
		if managedPath != "" && c == managedPath {
			if err := ensureCaddyfile(filepath.Dir(managedPath), sync); err != nil {
				return "", err
			}
			if err := ensureSeccomp(filepath.Dir(managedPath), sync); err != nil {
				return "", err
			}
			if sync {
				if err := syncEmbeddedCompose(managedPath); err != nil {
					return "", fmt.Errorf("đồng bộ %s với bản nhúng mới: %w", managedPath, err)
				}
			} else {
				noticeIfDrifted(managedPath)
			}
			return managedPath, nil
		}
		return c, nil
	}

	if managedPath != "" {
		if path, err := writeEmbeddedCompose(managedPath); err == nil {
			if err := ensureCaddyfile(filepath.Dir(managedPath), sync); err != nil {
				return "", err
			}
			if err := ensureSeccomp(filepath.Dir(managedPath), sync); err != nil {
				return "", err
			}
			return path, nil
		}
		// Ghi thất bại (ví dụ không có quyền) — rơi xuống lỗi chung bên dưới,
		// không che giấu bằng cách trả lỗi ghi tệp khó hiểu hơn.
	}

	return "", fmt.Errorf(
		"không tìm thấy deploy/compose.yaml (đã thử biến %s, %s/deploy/compose.yaml, dò lên từ thư mục hiện tại/thư mục chứa genh, và ghi bản nhúng sẵn) — đặt %s=/đường/dẫn/compose.yaml rồi chạy lại",
		EnvOverrideVar, installDir, EnvOverrideVar)
}

// noticeIfDrifted in một dòng nhắc qua NoticeWriter nếu compose.yaml tại path
// khác bản nhúng của binary đang chạy — im lặng bỏ qua lỗi đọc tệp (lỗi thật
// nếu có sẽ lộ ra ngay sau đó khi lệnh gọi thật sự dùng compose.yaml).
func noticeIfDrifted(path string) {
	current, err := os.ReadFile(path)
	if err != nil || bytes.Equal(current, embeddedComposeYAML) {
		return
	}
	_, _ = fmt.Fprintf(NoticeWriter,
		"Lưu ý: %s khác bản compose.yaml nhúng trong genh hiện tại — chạy `genh update` để đồng bộ.\n", path)
}

// writeEmbeddedCompose ghi compose.yaml nhúng sẵn ra path (tạo thư mục cha
// nếu cần) — chỉ gọi khi path CHƯA tồn tại (xem Locate); tệp ĐÃ tồn tại đi
// qua syncEmbeddedCompose thay vì hàm này.
func writeEmbeddedCompose(path string) (string, error) {
	deployDir := filepath.Dir(path)
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục %s: %w", deployDir, err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, embeddedComposeYAML, 0o644); err != nil {
		return "", fmt.Errorf("ghi %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return "", fmt.Errorf("đổi tên %s -> %s: %w", tmp, path, err)
	}
	return path, nil
}

// syncEmbeddedCompose đồng bộ compose.yaml GENH QUẢN LÝ tại path với bản
// nhúng của binary genh ĐANG CHẠY — gọi mỗi khi Locate thấy tệp đó đã có sẵn
// (không chỉ lần ghi đầu, xem SỬA LỖI ở doc-comment của Locate). Không đổi
// gì nếu nội dung đã khớp byte-for-byte (tránh ghi tệp không cần thiết mỗi
// lần Locate chạy — trường hợp phổ biến nhất: genh không đổi phiên bản giữa
// hai lần chạy). Nếu khác, giữ nguyên tệp cũ dưới path+".bak" (Owner có thể
// đã tự sửa tay compose.yaml của mình — thêm service, đổi cổng…; genh không
// cố phân biệt "tự sửa" với "phiên bản genh cũ để lại", chỉ đảm bảo không
// mất trắng nội dung cũ) rồi ghi đè bằng bản nhúng mới, qua tệp tạm + rename
// để không để lại compose.yaml nửa vời nếu tiến trình bị ngắt giữa chừng.
func syncEmbeddedCompose(path string) error {
	current, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("đọc %s: %w", path, err)
	}
	if bytes.Equal(current, embeddedComposeYAML) {
		return nil
	}

	bakPath := path + ".bak"
	if err := os.WriteFile(bakPath, current, 0o644); err != nil {
		return fmt.Errorf("ghi bản sao lưu %s: %w", bakPath, err)
	}

	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, embeddedComposeYAML, 0o644); err != nil {
		return fmt.Errorf("ghi %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("đổi tên %s -> %s: %w", tmp, path, err)
	}
	return nil
}

// ensureCaddyfile đảm bảo "<deployDir>/proxy/Caddyfile" tồn tại cạnh
// compose.yaml GENH QUẢN LÝ (xem embeddedCaddyfile). Thiếu tệp → ghi bản
// nhúng. Chỗ đó là THƯ MỤC RỖNG (Docker tự tạo khi thiếu tệp) → xoá rồi ghi.
// Bản ≤ v0.1.7 mount "./caddy/Caddyfile" và Docker đã tạo "caddy/" THUỘC ROOT
// trên máy cài lỗi — genh không xoá được, nên từ v0.1.8 đổi sang "proxy/"
// (đường mới, chưa từng bị Docker chiếm); "caddy/" cũ nằm yên vô hại. sync=true (install/update) và nội dung khác
// bản nhúng → giữ bản cũ ở ".bak" rồi ghi đè, giống syncEmbeddedCompose.
func ensureCaddyfile(deployDir string, sync bool) error {
	path := filepath.Join(deployDir, "proxy", "Caddyfile")
	info, err := os.Stat(path)
	switch {
	case err == nil && info.IsDir():
		if err := os.Remove(path); err != nil {
			return fmt.Errorf("%s là thư mục (Docker tự tạo ở lần cài lỗi trước) và không xoá được: %w — xoá tay rồi chạy lại", path, err)
		}
	case err == nil:
		if !sync {
			return nil
		}
		current, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("đọc %s: %w", path, err)
		}
		if bytes.Equal(current, embeddedCaddyfile) {
			return nil
		}
		if err := os.WriteFile(path+".bak", current, 0o644); err != nil {
			return fmt.Errorf("ghi bản sao lưu %s.bak: %w", path, err)
		}
	case !os.IsNotExist(err):
		return fmt.Errorf("kiểm %s: %w", path, err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("tạo thư mục %s: %w", filepath.Dir(path), err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, embeddedCaddyfile, 0o644); err != nil {
		return fmt.Errorf("ghi %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("đổi tên %s -> %s: %w", tmp, path, err)
	}
	return nil
}

// ensureSeccomp đảm bảo "<deployDir>/browser/chromium-seccomp.json" tồn tại cạnh compose.yaml GENH QUẢN LÝ
// (xem embeddedSeccomp) — cùng quy tắc với ensureCaddyfile: thiếu → ghi; là thư mục (Docker tự tạo) → xoá rồi
// ghi; sync=true và khác bản nhúng → giữ ".bak" rồi ghi đè.
func ensureSeccomp(deployDir string, sync bool) error {
	return ensureEmbeddedFile(filepath.Join(deployDir, "browser", "chromium-seccomp.json"), embeddedSeccomp, sync)
}

func ensureEmbeddedFile(path string, want []byte, sync bool) error {
	info, err := os.Stat(path)
	switch {
	case err == nil && info.IsDir():
		if err := os.Remove(path); err != nil {
			return fmt.Errorf("%s là thư mục (Docker tự tạo khi thiếu tệp) và không xoá được: %w — xoá tay rồi chạy lại", path, err)
		}
	case err == nil:
		if !sync {
			return nil
		}
		current, err := os.ReadFile(path)
		if err != nil {
			return fmt.Errorf("đọc %s: %w", path, err)
		}
		if bytes.Equal(current, want) {
			return nil
		}
		if err := os.WriteFile(path+".bak", current, 0o644); err != nil {
			return fmt.Errorf("ghi bản sao lưu %s.bak: %w", path, err)
		}
	case !os.IsNotExist(err):
		return fmt.Errorf("kiểm %s: %w", path, err)
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("tạo thư mục %s: %w", filepath.Dir(path), err)
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, want, 0o644); err != nil {
		return fmt.Errorf("ghi %s: %w", tmp, err)
	}
	if err := os.Rename(tmp, path); err != nil {
		return fmt.Errorf("đổi tên %s -> %s: %w", tmp, path, err)
	}
	return nil
}

// EmbeddedCompose trả bản sao compose.yaml nhúng trong binary genh đang chạy —
// `genh update` dùng làm "bản đích" để tải ảnh mới TRƯỚC khi đụng compose.yaml
// trên đĩa (xem internal/ops/update.go).
func EmbeddedCompose() []byte {
	return append([]byte(nil), embeddedComposeYAML...)
}

// ManagedComposePath là compose.yaml GENH QUẢN LÝ dưới gốc cài đặt ("" nếu
// installDir rỗng) — cùng đường dẫn locate dùng.
func ManagedComposePath(installDir string) string {
	if installDir == "" {
		return ""
	}
	return filepath.Join(installDir, "deploy", "compose.yaml")
}

// InSyncWithEmbedded báo compose.yaml tại path đã khớp bản nhúng của binary
// đang chạy chưa (chỉ đọc, không ghi). path KHÁC compose.yaml genh quản lý
// (GENH_COMPOSE_FILE, checkout repo) → false: genh không biết tệp ngoài đó đã
// được dựng lên chưa (ảnh `image:` có thể có bản mới, cần migrate…) nên KHÔNG
// được coi là "đã khớp" — `genh update` luôn chạy đủ sao lưu/tải/migrate/khởi
// động lại như trước v0.1.34. Với tệp genh quản lý: true chỉ khi
// compose.yaml trùng từng byte bản nhúng VÀ deploy/proxy/Caddyfile trùng
// embeddedCaddyfile VÀ deploy/browser/chromium-seccomp.json trùng embeddedSeccomp (thiếu tệp = không trùng).
func InSyncWithEmbedded(installDir, path string) (bool, error) {
	managed := ManagedComposePath(installDir)
	if managed == "" || filepath.Clean(path) != filepath.Clean(managed) {
		return false, nil
	}
	current, err := os.ReadFile(managed)
	if err != nil {
		return false, fmt.Errorf("đọc %s: %w", managed, err)
	}
	if !bytes.Equal(current, embeddedComposeYAML) {
		return false, nil
	}
	caddy, err := os.ReadFile(filepath.Join(filepath.Dir(managed), "proxy", "Caddyfile"))
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, fmt.Errorf("đọc Caddyfile: %w", err)
	}
	if !bytes.Equal(caddy, embeddedCaddyfile) {
		return false, nil
	}
	sec, err := os.ReadFile(filepath.Join(filepath.Dir(managed), "browser", "chromium-seccomp.json"))
	if err != nil {
		if os.IsNotExist(err) {
			return false, nil
		}
		return false, fmt.Errorf("đọc chromium-seccomp.json: %w", err)
	}
	return bytes.Equal(sec, embeddedSeccomp), nil
}
