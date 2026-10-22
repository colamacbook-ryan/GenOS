package compose

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSearchCandidates_IncludesEnvOverrideFirst(t *testing.T) {
	t.Setenv(EnvOverrideVar, "/duong/dan/tuy-chon/compose.yaml")
	got := SearchCandidates("/install", "/cwd", "/exe")
	if len(got) == 0 || got[0] != "/duong/dan/tuy-chon/compose.yaml" {
		t.Errorf("candidate đầu tiên phải là GENH_COMPOSE_FILE, được %v", got)
	}
}

func TestSearchCandidates_IncludesInstallDirAndAscendingParents(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")
	got := SearchCandidates("/install", "/a/b/c", "")

	wantInstall := filepath.Join("/install", "deploy", "compose.yaml")
	found := false
	for _, c := range got {
		if c == wantInstall {
			found = true
		}
	}
	if !found {
		t.Errorf("thiếu candidate dưới installDir: %v", got)
	}

	wantAscend := filepath.Join("/a", "deploy", "compose.yaml")
	found = false
	for _, c := range got {
		if c == wantAscend {
			found = true
		}
	}
	if !found {
		t.Errorf("thiếu candidate dò lên thư mục cha /a: %v", got)
	}
}

func TestLocate_FindsFileAmongAscendingParents(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")

	// Locate dò từ os.Getwd(), mà Getwd trả đường dẫn THẬT của thư mục hiện
	// tại: trên macOS t.TempDir() nằm dưới /var/folders/… nhưng /var là
	// symlink tới /private/var; trên Windows runner TEMP có thể ở dạng tên
	// ngắn 8.3 (RUNNER~1). Chuẩn hoá gốc tạm trước để so đúng một đường dẫn.
	root, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatalf("EvalSymlinks: %v", err)
	}
	deployDir := filepath.Join(root, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	composePath := filepath.Join(deployDir, "compose.yaml")
	if err := os.WriteFile(composePath, []byte(sampleCompose), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	deepCwd := filepath.Join(root, "apps", "genh", "cmd", "genh")
	if err := os.MkdirAll(deepCwd, 0o755); err != nil {
		t.Fatalf("MkdirAll deepCwd: %v", err)
	}

	oldWd, _ := os.Getwd()
	defer os.Chdir(oldWd)
	if err := os.Chdir(deepCwd); err != nil {
		t.Fatalf("Chdir: %v", err)
	}

	got, err := Locate("")
	if err != nil {
		t.Fatalf("Locate: %v", err)
	}
	if got != composePath {
		t.Errorf("Locate = %q, muốn %q", got, composePath)
	}
}

func TestLocate_EnvOverrideWins(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "custom-compose.yaml")
	if err := os.WriteFile(path, []byte(sampleCompose), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
	t.Setenv(EnvOverrideVar, path)

	got, err := Locate("")
	if err != nil {
		t.Fatalf("Locate: %v", err)
	}
	if got != path {
		t.Errorf("Locate = %q, muốn %q (từ %s)", got, path, EnvOverrideVar)
	}
}

func TestLocate_NotFound(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")
	dir := t.TempDir()
	oldWd, _ := os.Getwd()
	defer os.Chdir(oldWd)
	if err := os.Chdir(dir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}
	// installDir RỖNG: không có nơi nào để rơi về ghi bản nhúng sẵn — vẫn
	// phải lỗi rõ ràng như trước.
	if _, err := Locate(""); err == nil {
		t.Fatal("muốn lỗi khi không tìm thấy compose.yaml ở đâu cả và installDir rỗng")
	}
}

func TestLocate_FallsBackToEmbeddedComposeUnderInstallDir(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")
	cwdDir := t.TempDir() // KHÔNG chứa deploy/compose.yaml nào, tách biệt installDir
	oldWd, _ := os.Getwd()
	defer os.Chdir(oldWd)
	if err := os.Chdir(cwdDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}

	installDir := t.TempDir()
	want := filepath.Join(installDir, "deploy", "compose.yaml")

	got, err := Locate(installDir)
	if err != nil {
		t.Fatalf("Locate: %v (muốn rơi về ghi bản nhúng sẵn dưới installDir)", err)
	}
	if got != want {
		t.Errorf("Locate = %q, muốn %q", got, want)
	}

	data, err := os.ReadFile(want)
	if err != nil {
		t.Fatalf("đọc lại tệp vừa ghi: %v", err)
	}
	if len(data) == 0 {
		t.Error("compose.yaml nhúng sẵn ghi ra rỗng")
	}

	// Gọi lại lần hai NGAY, không đổi gì: idempotent, không lỗi, không đổi
	// nội dung, không sinh .bak (nội dung đã khớp bản nhúng).
	got2, err := Locate(installDir)
	if err != nil {
		t.Fatalf("Locate lần 2: %v", err)
	}
	if got2 != want {
		t.Errorf("Locate lần 2 = %q, muốn %q", got2, want)
	}
	data2, _ := os.ReadFile(want)
	if string(data2) != string(data) {
		t.Error("Locate lần 2 (không đổi bản nhúng) không được đổi nội dung tệp")
	}
	if _, err := os.Stat(want + ".bak"); err == nil {
		t.Error("Locate lần 2 (nội dung đã khớp) không được sinh .bak")
	}
}

// TestLocateAndSync_SyncsManagedComposeWhenEmbeddedContentDiffers là test
// cho SỬA LỖI chính của package này (xem docs/reports/HANDOFF-v0.1.1.md mục
// "Lỗi cần sửa" #5): một bản genh MỚI HƠN (mang bản nhúng khác — mô phỏng
// bằng cách tự ghi một nội dung "cũ" khác bản nhúng thật vào đúng vị trí
// genh quản lý) phải TỰ ĐỒNG BỘ lại compose.yaml ở installDir về đúng bản
// nhúng hiện tại của chính nó, giữ bản cũ lại ở compose.yaml.bak — KHÔNG
// được im lặng giữ mãi bản cũ như hành vi trước khi sửa.
//
// Từ v0.1.2 (mục #3), hành vi ĐỒNG BỘ này chỉ còn ở LocateAndSync (dùng bởi
// `genh update`/`genh install`) — xem TestLocate_OnlyWarnsWithoutSyncing bên
// dưới cho hành vi mới của Locate (dùng bởi mọi lệnh vận hành khác).
func TestLocateAndSync_SyncsManagedComposeWhenEmbeddedContentDiffers(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")
	cwdDir := t.TempDir()
	oldWd, _ := os.Getwd()
	defer os.Chdir(oldWd)
	if err := os.Chdir(cwdDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}

	installDir := t.TempDir()
	deployDir := filepath.Join(installDir, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	managedPath := filepath.Join(deployDir, "compose.yaml")

	oldContent := []byte("# compose.yaml cũ (hoặc Owner tự sửa)\nservices: {}\n")
	if err := os.WriteFile(managedPath, oldContent, 0o644); err != nil {
		t.Fatalf("WriteFile bản cũ: %v", err)
	}

	got, err := LocateAndSync(installDir)
	if err != nil {
		t.Fatalf("LocateAndSync: %v", err)
	}
	if got != managedPath {
		t.Errorf("LocateAndSync = %q, muốn %q", got, managedPath)
	}

	newContent, err := os.ReadFile(managedPath)
	if err != nil {
		t.Fatalf("đọc lại sau đồng bộ: %v", err)
	}
	if string(newContent) != string(embeddedComposeYAML) {
		t.Error("compose.yaml phải được đồng bộ về đúng bản nhúng của binary hiện tại")
	}

	bak, err := os.ReadFile(managedPath + ".bak")
	if err != nil {
		t.Fatalf("phải giữ bản cũ ở compose.yaml.bak: %v", err)
	}
	if string(bak) != string(oldContent) {
		t.Error("compose.yaml.bak phải đúng nội dung CŨ trước khi đồng bộ")
	}

	// Gọi lại lần ba: đã khớp bản nhúng, không đổi gì thêm, không ghi lại
	// .bak (giữ nguyên .bak của lần đồng bộ trước, không mất thông tin).
	if _, err := LocateAndSync(installDir); err != nil {
		t.Fatalf("LocateAndSync lần 3: %v", err)
	}
	bak2, _ := os.ReadFile(managedPath + ".bak")
	if string(bak2) != string(oldContent) {
		t.Error("LocateAndSync lần 3 (đã khớp bản nhúng) không được đổi .bak")
	}
}

// TestLocate_OnlyWarnsWithoutSyncing là test cho SỬA LỖI mục #3 của v0.1.2
// (docs/reports/HANDOFF-v0.1.1.md phiên bàn giao tiếp theo): Locate (dùng
// bởi mọi lệnh vận hành TRỪ `genh update`/`genh install`) KHÔNG được tự ghi
// đè compose.yaml của Owner nữa dù phát hiện lệch bản nhúng — chỉ in một
// dòng nhắc chạy `genh update`.
func TestLocate_OnlyWarnsWithoutSyncing(t *testing.T) {
	t.Setenv(EnvOverrideVar, "")
	cwdDir := t.TempDir()
	oldWd, _ := os.Getwd()
	defer os.Chdir(oldWd)
	if err := os.Chdir(cwdDir); err != nil {
		t.Fatalf("Chdir: %v", err)
	}

	installDir := t.TempDir()
	deployDir := filepath.Join(installDir, "deploy")
	if err := os.MkdirAll(deployDir, 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	managedPath := filepath.Join(deployDir, "compose.yaml")

	oldContent := []byte("# compose.yaml Owner tự sửa tay\nservices: {}\n")
	if err := os.WriteFile(managedPath, oldContent, 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}

	var notice strings.Builder
	oldNoticeWriter := NoticeWriter
	NoticeWriter = &notice
	defer func() { NoticeWriter = oldNoticeWriter }()

	got, err := Locate(installDir)
	if err != nil {
		t.Fatalf("Locate: %v", err)
	}
	if got != managedPath {
		t.Errorf("Locate = %q, muốn %q", got, managedPath)
	}

	after, err := os.ReadFile(managedPath)
	if err != nil {
		t.Fatalf("đọc lại: %v", err)
	}
	if string(after) != string(oldContent) {
		t.Error("Locate (không đồng bộ) KHÔNG được đổi nội dung compose.yaml của Owner")
	}
	if _, err := os.Stat(managedPath + ".bak"); err == nil {
		t.Error("Locate (không đồng bộ) không được tạo .bak")
	}
	if notice.Len() == 0 {
		t.Error("Locate phải in dòng nhắc khi phát hiện compose.yaml lệch bản nhúng")
	}

	// Gọi lại lần hai: vẫn chỉ nhắc, vẫn không đụng tệp.
	notice.Reset()
	if _, err := Locate(installDir); err != nil {
		t.Fatalf("Locate lần 2: %v", err)
	}
	after2, _ := os.ReadFile(managedPath)
	if string(after2) != string(oldContent) {
		t.Error("Locate lần 2 vẫn không được đổi nội dung")
	}
	if notice.Len() == 0 {
		t.Error("Locate lần 2 vẫn phải nhắc (vẫn còn lệch)")
	}
}

func TestInSyncWithEmbedded(t *testing.T) {
	writeManaged := func(t *testing.T, composeData, caddy []byte) (installDir, path string) {
		t.Helper()
		installDir = t.TempDir()
		path = ManagedComposePath(installDir)
		if err := os.MkdirAll(filepath.Join(filepath.Dir(path), "proxy"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(path, composeData, 0o644); err != nil {
			t.Fatal(err)
		}
		if err := os.MkdirAll(filepath.Join(filepath.Dir(path), "browser"), 0o755); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(filepath.Dir(path), "browser", "chromium-seccomp.json"), embeddedSeccomp, 0o644); err != nil {
			t.Fatal(err)
		}
		if caddy != nil {
			if err := os.WriteFile(filepath.Join(filepath.Dir(path), "proxy", "Caddyfile"), caddy, 0o644); err != nil {
				t.Fatal(err)
			}
		}
		return installDir, path
	}

	t.Run("managed trùng bản nhúng", func(t *testing.T) {
		dir, path := writeManaged(t, EmbeddedCompose(), embeddedCaddyfile)
		ok, err := InSyncWithEmbedded(dir, path)
		if err != nil || !ok {
			t.Fatalf("muốn true,nil — được %v,%v", ok, err)
		}
	})
	t.Run("compose khác", func(t *testing.T) {
		dir, path := writeManaged(t, []byte("name: cu\n"), embeddedCaddyfile)
		if ok, err := InSyncWithEmbedded(dir, path); err != nil || ok {
			t.Fatalf("muốn false,nil — được %v,%v", ok, err)
		}
	})
	t.Run("Caddyfile khác", func(t *testing.T) {
		dir, path := writeManaged(t, EmbeddedCompose(), []byte("khac\n"))
		if ok, err := InSyncWithEmbedded(dir, path); err != nil || ok {
			t.Fatalf("muốn false,nil — được %v,%v", ok, err)
		}
	})
	t.Run("thiếu Caddyfile", func(t *testing.T) {
		dir, path := writeManaged(t, EmbeddedCompose(), nil)
		if ok, err := InSyncWithEmbedded(dir, path); err != nil || ok {
			t.Fatalf("muốn false,nil — được %v,%v", ok, err)
		}
	})
	t.Run("đường ngoài (GENH_COMPOSE_FILE/checkout) → không bao giờ coi là đã khớp", func(t *testing.T) {
		dir := t.TempDir()
		other := filepath.Join(t.TempDir(), "deploy", "compose.yaml")
		if err := os.MkdirAll(filepath.Dir(other), 0o755); err != nil {
			t.Fatal(err)
		}
		// Kể cả tệp ngoài trùng từng byte bản nhúng: genh không quản lý nó.
		if err := os.WriteFile(other, EmbeddedCompose(), 0o644); err != nil {
			t.Fatal(err)
		}
		if ok, err := InSyncWithEmbedded(dir, other); err != nil || ok {
			t.Fatalf("muốn false,nil — được %v,%v", ok, err)
		}
		if ok, err := InSyncWithEmbedded("", other); err != nil || ok {
			t.Fatalf("installDir rỗng: muốn false,nil — được %v,%v", ok, err)
		}
	})
}

func TestEmbeddedCompose_IsCopy(t *testing.T) {
	a := EmbeddedCompose()
	if len(a) == 0 {
		t.Fatal("bản nhúng rỗng")
	}
	a[0] ^= 0xff
	if EmbeddedCompose()[0] == a[0] {
		t.Error("EmbeddedCompose phải trả bản sao, sửa bản trả về không được đổi bản nhúng")
	}
	if ManagedComposePath("") != "" {
		t.Error("installDir rỗng → \"\"")
	}
}
