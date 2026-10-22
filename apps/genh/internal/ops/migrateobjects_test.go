package ops

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("MkdirAll: %v", err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("WriteFile: %v", err)
	}
}

func TestMergeObjectSources_CopiesFilesUniqueToEachSource(t *testing.T) {
	root := t.TempDir()
	apiDir := filepath.Join(root, "api")
	workerDir := filepath.Join(root, "worker")
	dest := filepath.Join(root, "merged")

	writeFile(t, filepath.Join(apiDir, "docs", "a.pdf"), "nội dung a")
	writeFile(t, filepath.Join(workerDir, "backups", "20260101-x.pgcustom.enc"), "nội dung backup")

	res, err := mergeObjectSources([]string{apiDir, workerDir}, dest)
	if err != nil {
		t.Fatalf("mergeObjectSources: %v", err)
	}
	if len(res.Conflicts) != 0 {
		t.Fatalf("không muốn xung đột, được %v", res.Conflicts)
	}

	got, err := os.ReadFile(filepath.Join(dest, "docs", "a.pdf"))
	if err != nil || string(got) != "nội dung a" {
		t.Errorf("docs/a.pdf = %q, %v", got, err)
	}
	got2, err := os.ReadFile(filepath.Join(dest, "backups", "20260101-x.pgcustom.enc"))
	if err != nil || string(got2) != "nội dung backup" {
		t.Errorf("backups/20260101-x.pgcustom.enc = %q, %v", got2, err)
	}
}

func TestMergeObjectSources_SameContentAtSamePathIsNotAConflict(t *testing.T) {
	root := t.TempDir()
	apiDir := filepath.Join(root, "api")
	workerDir := filepath.Join(root, "worker")
	dest := filepath.Join(root, "merged")

	writeFile(t, filepath.Join(apiDir, "docs", "shared.txt"), "y hệt nhau")
	writeFile(t, filepath.Join(workerDir, "docs", "shared.txt"), "y hệt nhau")

	res, err := mergeObjectSources([]string{apiDir, workerDir}, dest)
	if err != nil {
		t.Fatalf("mergeObjectSources: %v", err)
	}
	if len(res.Conflicts) != 0 {
		t.Fatalf("nội dung giống nhau không được coi là xung đột, được %v", res.Conflicts)
	}
	got, err := os.ReadFile(filepath.Join(dest, "docs", "shared.txt"))
	if err != nil || string(got) != "y hệt nhau" {
		t.Errorf("docs/shared.txt = %q, %v", got, err)
	}
}

func TestMergeObjectSources_DifferentContentAtSamePathIsReportedAndNotCopied(t *testing.T) {
	root := t.TempDir()
	apiDir := filepath.Join(root, "api")
	workerDir := filepath.Join(root, "worker")
	dest := filepath.Join(root, "merged")

	writeFile(t, filepath.Join(apiDir, "docs", "clash.txt"), "bản của api")
	writeFile(t, filepath.Join(workerDir, "docs", "clash.txt"), "bản của worker — KHÁC")
	// Một tệp không xung đột khác vẫn phải được chép, dù có xung đột ở tệp kia.
	writeFile(t, filepath.Join(apiDir, "docs", "ok.txt"), "ổn")

	res, err := mergeObjectSources([]string{apiDir, workerDir}, dest)
	if err != nil {
		t.Fatalf("mergeObjectSources: %v", err)
	}
	if len(res.Conflicts) != 1 || res.Conflicts[0] != "docs/clash.txt" {
		t.Fatalf("muốn đúng 1 xung đột docs/clash.txt, được %v", res.Conflicts)
	}
	if _, err := os.Stat(filepath.Join(dest, "docs", "clash.txt")); err == nil {
		t.Error("tệp xung đột KHÔNG được chép vào đích")
	}
	got, err := os.ReadFile(filepath.Join(dest, "docs", "ok.txt"))
	if err != nil || string(got) != "ổn" {
		t.Errorf("docs/ok.txt vẫn phải được chép dù có xung đột ở tệp khác: %q, %v", got, err)
	}
}

func TestMergeObjectSources_MergesBackupManifestByKeyInsteadOfTreatingAsPlainFile(t *testing.T) {
	root := t.TempDir()
	apiDir := filepath.Join(root, "api")
	workerDir := filepath.Join(root, "worker")
	dest := filepath.Join(root, "merged")

	// api và worker CỐ Ý có manifest KHÁC NHAU byte-for-byte (mỗi bên tự
	// thêm entry của backup do chính nó tạo) — không được coi là xung đột.
	apiManifest := `[
		{"key": "backups/A.enc", "taken_at": "2026-01-01T00:00:00+00:00", "database": "gen_harness", "size_bytes": 10, "sha256": "aa"},
		{"key": "backups/SHARED.enc", "taken_at": "2026-01-02T00:00:00+00:00", "database": "gen_harness", "size_bytes": 20, "sha256": "bb"}
	]`
	workerManifest := `[
		{"key": "backups/B.enc", "taken_at": "2026-01-03T00:00:00+00:00", "database": "gen_harness", "size_bytes": 30, "sha256": "cc"},
		{"key": "backups/SHARED.enc", "taken_at": "2026-01-02T00:00:00+00:00", "database": "gen_harness", "size_bytes": 20, "sha256": "bb"}
	]`
	writeFile(t, filepath.Join(apiDir, "backups", "manifest.json"), apiManifest)
	writeFile(t, filepath.Join(workerDir, "backups", "manifest.json"), workerManifest)

	res, err := mergeObjectSources([]string{apiDir, workerDir}, dest)
	if err != nil {
		t.Fatalf("mergeObjectSources: %v", err)
	}
	if len(res.Conflicts) != 0 {
		t.Fatalf("manifest khác nhau KHÔNG được coi là xung đột tệp thường, được %v", res.Conflicts)
	}

	raw, err := os.ReadFile(filepath.Join(dest, "backups", "manifest.json"))
	if err != nil {
		t.Fatalf("đọc manifest đã gộp: %v", err)
	}
	var entries []map[string]any
	if err := json.Unmarshal(raw, &entries); err != nil {
		t.Fatalf("manifest đã gộp không phải JSON hợp lệ: %v", err)
	}
	keys := map[string]bool{}
	for _, e := range entries {
		keys[e["key"].(string)] = true
	}
	for _, want := range []string{"backups/A.enc", "backups/B.enc", "backups/SHARED.enc"} {
		if !keys[want] {
			t.Errorf("manifest đã gộp thiếu khoá %q", want)
		}
	}
	if len(entries) != 3 {
		t.Errorf("manifest đã gộp phải có đúng 3 mục (loại trùng SHARED), được %d", len(entries))
	}
}

func TestMergeObjectSources_MissingSourceDirIsSkippedSilently(t *testing.T) {
	root := t.TempDir()
	apiDir := filepath.Join(root, "api") // KHÔNG tạo — mô phỏng container đó không có gì để chép ra
	workerDir := filepath.Join(root, "worker")
	dest := filepath.Join(root, "merged")
	writeFile(t, filepath.Join(workerDir, "docs", "x.txt"), "x")

	res, err := mergeObjectSources([]string{apiDir, workerDir}, dest)
	if err != nil {
		t.Fatalf("mergeObjectSources: %v", err)
	}
	if len(res.Conflicts) != 0 {
		t.Fatalf("không muốn xung đột, được %v", res.Conflicts)
	}
	if _, err := os.Stat(filepath.Join(dest, "docs", "x.txt")); err != nil {
		t.Errorf("tệp từ nguồn còn lại vẫn phải được chép: %v", err)
	}
}

func TestMergeBackupManifests_DedupesByKeyDeterministicOrder(t *testing.T) {
	a := []byte(`[{"key":"z"},{"key":"a"}]`)
	b := []byte(`[{"key":"a"},{"key":"m"}]`)

	merged, err := mergeBackupManifests([][]byte{a, b})
	if err != nil {
		t.Fatalf("mergeBackupManifests: %v", err)
	}
	var entries []map[string]any
	if err := json.Unmarshal(merged, &entries); err != nil {
		t.Fatalf("json.Unmarshal: %v", err)
	}
	if len(entries) != 3 {
		t.Fatalf("muốn 3 mục (a/m/z, loại trùng a), được %d: %s", len(entries), merged)
	}
	var gotKeys []string
	for _, e := range entries {
		gotKeys = append(gotKeys, e["key"].(string))
	}
	want := []string{"a", "m", "z"}
	for i, w := range want {
		if gotKeys[i] != w {
			t.Errorf("thứ tự khoá[%d] = %q, muốn %q (toàn bộ: %v)", i, gotKeys[i], w, gotKeys)
		}
	}
}

func TestCaptureLegacyObjectsIfAny_NoLegacyContainers_ReturnsEmptyNoError(t *testing.T) {
	// Cả hai kiểm tra (api/worker) đều KHÔNG khớp Response nào -> fake.Runner
	// trả lỗi "không có Response khớp" cho mỗi lần -> hàm phải coi là "không
	// có gì để di trú", KHÔNG lỗi (bản cài mới từ v0.1.1 trở lên, phổ biến
	// nhất, không có container v0.1.0 nào đang chạy).
	fr := &fake.Runner{}
	installDir := t.TempDir()
	var out strings.Builder

	hostDir, err := captureLegacyObjectsIfAny(context.Background(), fr, "/x/deploy/compose.yaml", nil, "/x/deploy", installDir, &out)
	if err != nil {
		t.Fatalf("captureLegacyObjectsIfAny: %v", err)
	}
	if hostDir != "" {
		t.Errorf("hostDir = %q, muốn rỗng (không có gì để di trú)", hostDir)
	}
}

func TestCaptureLegacyObjectsIfAny_FoundNonEmpty_CopiesOutAndMergesToHostDir(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{
			Match:  fake.MatchArgsContain("exec", "-T", "api", "sh", "-c"),
			Output: []byte("/tmp/gh-objects/docs/a.pdf"), // không rỗng
		},
		{
			Match:  fake.MatchArgsContain("exec", "-T", "worker", "sh", "-c"),
			Output: []byte(""), // rỗng -> worker không có gì
		},
		{
			Match:  fake.MatchArgsContain("cp", "api:/tmp/gh-objects/."),
			Output: []byte(""),
		},
	}}
	installDir := t.TempDir()
	var out strings.Builder

	hostDir, err := captureLegacyObjectsIfAny(context.Background(), fr, filepath.Join(installDir, "deploy", "compose.yaml"), nil, filepath.Join(installDir, "deploy"), installDir, &out)
	if err != nil {
		t.Fatalf("captureLegacyObjectsIfAny: %v", err)
	}
	if hostDir == "" {
		t.Fatal("hostDir rỗng, muốn có đường dẫn thư mục đã gộp")
	}
	wantPrefix := filepath.Join(installDir, "data")
	if !strings.HasPrefix(hostDir, wantPrefix) {
		t.Errorf("hostDir = %q, muốn nằm dưới %q", hostDir, wantPrefix)
	}
	if info, err := os.Stat(hostDir); err != nil || !info.IsDir() {
		t.Errorf("hostDir %q phải là một thư mục đã tồn tại (kể cả rỗng — fake.Runner không thật sự ghi tệp khi giả lập docker compose cp, việc gộp nội dung thật đã có bộ test riêng ở TestMergeObjectSources_*): %v", hostDir, err)
	}
	if !strings.Contains(out.String(), "đã di trú dữ liệu") {
		t.Errorf("output phải xác nhận đã di trú, được %q", out.String())
	}

	// Chỉ container "api" (found) mới bị docker compose cp — worker rỗng thì
	// không cần chép ra gì cả.
	var sawWorkerCp bool
	for _, c := range fr.Calls {
		if strings.Contains(strings.Join(c.Cmd.Args, " "), "cp") && strings.Contains(strings.Join(c.Cmd.Args, " "), "worker:") {
			sawWorkerCp = true
		}
	}
	if sawWorkerCp {
		t.Error("KHÔNG được docker compose cp container worker khi kiểm thấy rỗng")
	}
}

func TestCaptureLegacyObjectsIfAny_CopyFailsAfterConfirmedNonEmpty_ReturnsErrorStopsUpdate(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("exec", "-T", "api", "sh", "-c"), Output: []byte("/tmp/gh-objects/docs/a.pdf")},
		{Match: fake.MatchArgsContain("exec", "-T", "worker", "sh", "-c"), Output: []byte("")},
		{Match: fake.MatchArgsContain("cp", "api:/tmp/gh-objects/."), Err: errors.New("container biến mất giữa chừng")},
	}}
	installDir := t.TempDir()
	var out strings.Builder

	_, err := captureLegacyObjectsIfAny(context.Background(), fr, filepath.Join(installDir, "deploy", "compose.yaml"), nil, filepath.Join(installDir, "deploy"), installDir, &out)
	if err == nil {
		t.Fatal("muốn lỗi khi ĐÃ xác nhận có dữ liệu nhưng chép ra thất bại — không được im lặng bỏ qua rồi tiếp tục update")
	}
}

func TestSeedObjectsVolume_CopiesIntoVolumeThenChownsAsGh(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("cp", "api:"+volumeObjectsDir), Output: []byte("")},
		{Match: fake.MatchArgsContain("exec", "-u", "root", "-T", "api", "chown", "-R", "gh:gh", volumeObjectsDir), Output: []byte("")},
	}}

	if err := seedObjectsVolume(context.Background(), fr, "/x/deploy/compose.yaml", nil, "/x/deploy", "/x/data/migrate-objects-20260101T000000Z/merged"); err != nil {
		t.Fatalf("seedObjectsVolume: %v", err)
	}

	var sawCp, sawChown bool
	for _, c := range fr.Calls {
		joined := strings.Join(c.Cmd.Args, " ")
		if strings.Contains(joined, "cp") && strings.Contains(joined, "api:"+volumeObjectsDir) {
			sawCp = true
		}
		if strings.Contains(joined, "chown") {
			sawChown = true
		}
	}
	if !sawCp {
		t.Error("phải gọi docker compose cp vào api:" + volumeObjectsDir)
	}
	if !sawChown {
		t.Error("phải chown lại thành gh:gh sau khi cp (container api không chạy bằng root)")
	}
}

func TestSeedObjectsVolume_CpFailure_ReturnsErrorWithoutChowning(t *testing.T) {
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("cp", "api:"+volumeObjectsDir), Err: errors.New("no such container")},
	}}

	err := seedObjectsVolume(context.Background(), fr, "/x/deploy/compose.yaml", nil, "/x/deploy", "/x/merged")
	if err == nil {
		t.Fatal("muốn lỗi khi docker compose cp vào volume thất bại")
	}
	for _, c := range fr.Calls {
		if strings.Contains(strings.Join(c.Cmd.Args, " "), "chown") {
			t.Error("không được chown khi cp vào volume đã thất bại")
		}
	}
}
