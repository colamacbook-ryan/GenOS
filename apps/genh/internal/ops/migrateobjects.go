package ops

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// Di trú dữ liệu /tmp/gh-objects (v0.1.0) -> volume gh_objects (v0.1.1+) —
// xem docs/reports/HANDOFF-v0.1.1.md mục "Lỗi cần sửa" #2 của v0.1.2: một
// máy v0.1.0 lưu tài liệu upload + backup do `genh backup`/job định kỳ tạo
// TRONG container api VÀ worker, ở /tmp/gh-objects — KHÔNG có volume, mỗi
// container một bản khác nhau (worker giữ backup định kỳ, api giữ tài liệu
// upload + backup do `genh backup` tạo). `genh update` (RunUpdate ở
// update.go) tạo lại container ở bước "4/4 Khởi động lại dịch vụ…" (`docker
// compose up -d` với compose.yaml MỚI đã mount volume `gh_objects` ở
// /var/lib/gh/objects) — nếu không di trú trước, TOÀN BỘ dữ liệu ở
// /tmp/gh-objects của container CŨ mất trắng khi container bị thay.
const (
	legacyObjectsDir = "/tmp/gh-objects"
	volumeObjectsDir = "/var/lib/gh/objects"

	// objectsManifestRelPath là danh mục backup (apps/api/gh/backup.py
	// MANIFEST_KEY) — MỘT khoá đặc biệt trong ObjectStore, không phải một
	// tài liệu Owner thường: api và worker có thể có bản KHÁC NHAU (mỗi bên
	// tự thêm entry của backup do CHÍNH nó tạo), nên phải GỘP theo "key" của
	// từng BackupEntry (xem mergeBackupManifests) thay vì coi như một tệp
	// thường (generic file merge ở mergeObjectSources sẽ báo XUNG ĐỘT nếu
	// coi đây là tệp thường, vì rất có thể hai bản manifest.json khác byte
	// nhau dù không có gì THẬT SỰ mâu thuẫn).
	objectsManifestRelPath = "backups/manifest.json"
)

// legacyObjectsServices là các service container v0.1.0 từng giữ
// /tmp/gh-objects riêng — api (tài liệu upload + backup do `genh backup`
// tạo) và worker (backup định kỳ, xem docs/reports/HANDOFF-v0.1.1.md).
var legacyObjectsServices = []string{"api", "worker"}

// captureLegacyObjectsIfAny kiểm các container api/worker ĐANG CHẠY (compose
// CŨ, trước khi pull/up) có /tmp/gh-objects không rỗng hay không, và nếu có,
// `docker compose cp` bản của MỖI container đó ra một thư mục trên host
// (installDir/data/migrate-objects-<dấu thời gian>/raw/<service>/), rồi gộp
// lại (mergeObjectSources) vào .../merged/ — trả về đường dẫn thư mục ĐÃ GỘP
// đó, hoặc "" nếu không có gì để di trú (bản cài mới từ v0.1.1 trở lên,
// hoặc chưa từng chạy container cũ nào — cả hai trường hợp /tmp/gh-objects
// không tồn tại/rỗng, KHÔNG coi là lỗi).
//
// Chỉ trả lỗi khi ĐÃ XÁC NHẬN có dữ liệu (kiểm thấy không rỗng) mà bước
// COPY RA/GỘP sau đó thất bại — dừng lại còn hơn tiếp tục `genh update`
// (pull/up sẽ tạo lại container, xoá luôn dữ liệu chưa kịp lấy ra).
func captureLegacyObjectsIfAny(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, installDir string, out io.Writer) (string, error) {
	var found []string
	for _, svc := range legacyObjectsServices {
		checkArgs := compose.BaseArgs(composePath, "exec", "-T", svc, "sh", "-c",
			"test -d "+legacyObjectsDir+" && find "+legacyObjectsDir+" -mindepth 1 -print -quit")
		res, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: checkArgs, Env: envOverlay, Dir: dir})
		// LỖI Ở ĐÂY (container không tồn tại/không chạy, "sh"/"test" không có
		// trong image, …) nghĩa là "không có gì để di trú ở container này" —
		// PHỔ BIẾN NHẤT là bản cài chưa từng chạy container v0.1.0 nào, không
		// phải lỗi thật cần dừng `genh update` lại.
		if err != nil || len(strings.TrimSpace(string(res))) == 0 {
			continue
		}
		found = append(found, svc)
	}
	if len(found) == 0 {
		return "", nil
	}

	ts := time.Now().UTC().Format("20060102T150405Z")
	hostDir := filepath.Join(installDir, "data", "migrate-objects-"+ts)
	rawDir := filepath.Join(hostDir, "raw")
	mergedDir := filepath.Join(hostDir, "merged")
	if err := os.MkdirAll(rawDir, 0o755); err != nil {
		return "", fmt.Errorf("tạo thư mục di trú %s: %w", rawDir, err)
	}

	var srcDirs []string
	for _, svc := range found {
		svcDir := filepath.Join(rawDir, svc)
		if err := os.MkdirAll(svcDir, 0o755); err != nil {
			return "", fmt.Errorf("tạo thư mục %s: %w", svcDir, err)
		}
		// "svc:/tmp/gh-objects/." (có dấu chấm cuối) — chép NỘI DUNG bên
		// trong thư mục nguồn vào svcDir, không lồng thêm một cấp
		// "gh-objects" nữa (đúng ngữ nghĩa `docker cp a/. b` của Docker).
		cpArgs := compose.BaseArgs(composePath, "cp", svc+":"+legacyObjectsDir+"/.", svcDir)
		if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: cpArgs, Env: envOverlay, Dir: dir}); err != nil {
			return "", fmt.Errorf("chép %s ra khỏi container %s (đã xác nhận có dữ liệu, KHÔNG được tiếp tục `genh update`): %w", legacyObjectsDir, svc, err)
		}
		srcDirs = append(srcDirs, svcDir)
	}

	result, err := mergeObjectSources(srcDirs, mergedDir)
	if err != nil {
		return "", fmt.Errorf("gộp dữ liệu di trú tại %s: %w", hostDir, err)
	}
	if len(result.Conflicts) > 0 {
		return "", fmt.Errorf(
			"%d tệp trùng tên nhưng KHÁC NỘI DUNG giữa %s (%s) — dữ liệu THÔ vẫn còn nguyên tại %s, tự so sánh/gộp tay các tệp đó rồi chạy lại `genh update` (KHÔNG tự ý ghi đè)",
			len(result.Conflicts), strings.Join(found, "/"), strings.Join(result.Conflicts, ", "), rawDir)
	}

	_, _ = fmt.Fprintln(out, "     đã di trú dữ liệu "+legacyObjectsDir+" ("+strings.Join(found, ", ")+") ra "+mergedDir)
	return mergedDir, nil
}

// seedObjectsVolume chép mergedDir (kết quả captureLegacyObjectsIfAny) vào
// volume gh_objects của container api (docker compose cp), rồi chown lại
// đúng user `gh` chạy bên trong container (container api KHÔNG chạy bằng
// root — xem deploy/images/api.Dockerfile — chép vào volume qua `docker cp`
// từ host luôn tạo tệp thuộc root, cần chown lại để process trong container
// đọc/ghi được). CHỈ gọi SAU KHI container api mới (đã mount volume
// gh_objects) đã lên VÀ healthy — gọi sớm hơn sẽ chép vào một container sắp
// bị "up -d" thay thế, vô nghĩa.
func seedObjectsVolume(ctx context.Context, runner dockercli.Runner, composePath string, envOverlay []string, dir, mergedDir string) error {
	cpArgs := compose.BaseArgs(composePath, "cp", mergedDir+"/.", "api:"+volumeObjectsDir)
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: cpArgs, Env: envOverlay, Dir: dir}); err != nil {
		return fmt.Errorf("chép %s vào volume (api:%s): %w", mergedDir, volumeObjectsDir, err)
	}
	chownArgs := compose.BaseArgs(composePath, "exec", "-u", "root", "-T", "api", "chown", "-R", "gh:gh", volumeObjectsDir)
	if _, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: chownArgs, Env: envOverlay, Dir: dir}); err != nil {
		return fmt.Errorf("chown lại %s (user gh) trong container api: %w", volumeObjectsDir, err)
	}
	return nil
}

// objectMergeResult là kết quả của mergeObjectSources.
type objectMergeResult struct {
	// Conflicts liệt kê các đường dẫn tương đối xuất hiện ở NHIỀU hơn một
	// srcDir với nội dung KHÁC NHAU — các tệp này CỐ Ý không được chép vào
	// destDir (mergeObjectSources không tự chọn giữ bản nào, tránh mất dữ
	// liệu Owner trong im lặng).
	Conflicts []string
}

// mergeObjectSources gộp một hay nhiều thư mục nguồn (mỗi thư mục là một
// bản chép ra từ /tmp/gh-objects của MỘT container — xem
// captureLegacyObjectsIfAny) vào đúng MỘT destDir:
//   - Một tệp chỉ xuất hiện ở một nguồn: chép thẳng.
//   - Cùng đường dẫn tương đối xuất hiện ở nhiều nguồn với NỘI DUNG GIỐNG
//     NHAU (so theo sha256): chép một bản, không lỗi (dữ liệu chung, ví dụ
//     cùng một tài liệu upload thấy được từ cả api lẫn worker — thực tế hiếm
//     nhưng vô hại nếu có).
//   - Cùng đường dẫn tương đối, nội dung KHÁC NHAU: KHÔNG chép (tránh ghi
//     đè một tệp Owner bằng một tệp khác cùng tên), ghi vào Conflicts để gọi
//     nơi gọi dừng lại và báo rõ.
//   - objectsManifestRelPath ("backups/manifest.json") là NGOẠI LỆ: luôn
//     được GỘP theo BackupEntry.key (mergeBackupManifests) thay vì so khớp
//     byte-for-byte như tệp thường — api và worker THƯỜNG XUYÊN có manifest
//     khác nhau (mỗi bên tự thêm entry của backup do chính nó tạo), coi đó
//     là "xung đột" sẽ chặn `genh update` một cách sai lầm ở gần như MỌI máy
//     v0.1.0 nâng cấp.
//
// Hàm THUẦN về mặt logic gộp (không gọi Docker) — chỉ đụng hệ thống tệp
// thật, nên test được đầy đủ bằng t.TempDir() mà không cần dockercli/fake.
func mergeObjectSources(srcDirs []string, destDir string) (objectMergeResult, error) {
	// Luôn tạo destDir, kể cả khi không có gì để gộp (mọi nguồn rỗng/không
	// tồn tại) — người gọi (captureLegacyObjectsIfAny) trả CHÍNH destDir này
	// làm "thư mục đã gộp" cho `docker compose cp` sau đó (seedObjectsVolume,
	// ở bước "5/5" của genh update); `docker compose cp <destDir>/.` phải có
	// một thư mục thật để chép từ, kể cả khi rỗng.
	if err := os.MkdirAll(destDir, 0o755); err != nil {
		return objectMergeResult{}, err
	}

	type fileInfo struct {
		absPath string
		sha     string
	}
	seen := make(map[string]fileInfo)
	conflictSet := make(map[string]bool)
	var manifestPayloads [][]byte

	for _, src := range srcDirs {
		info, err := os.Stat(src)
		if err != nil {
			if os.IsNotExist(err) {
				continue
			}
			return objectMergeResult{}, err
		}
		if !info.IsDir() {
			continue
		}

		err = filepath.WalkDir(src, func(p string, d fs.DirEntry, err error) error {
			if err != nil {
				return err
			}
			if d.IsDir() {
				return nil
			}
			rel, err := filepath.Rel(src, p)
			if err != nil {
				return err
			}
			rel = filepath.ToSlash(rel)

			data, err := os.ReadFile(p)
			if err != nil {
				return err
			}

			if rel == objectsManifestRelPath {
				manifestPayloads = append(manifestPayloads, data)
				return nil
			}

			sum := sha256.Sum256(data)
			hexSum := hex.EncodeToString(sum[:])
			if prev, ok := seen[rel]; ok {
				if prev.sha != hexSum {
					conflictSet[rel] = true
				}
				return nil
			}
			seen[rel] = fileInfo{absPath: p, sha: hexSum}
			return nil
		})
		if err != nil {
			return objectMergeResult{}, err
		}
	}

	for rel, fi := range seen {
		if conflictSet[rel] {
			continue
		}
		dst := filepath.Join(destDir, filepath.FromSlash(rel))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return objectMergeResult{}, err
		}
		data, err := os.ReadFile(fi.absPath)
		if err != nil {
			return objectMergeResult{}, err
		}
		if err := os.WriteFile(dst, data, 0o644); err != nil {
			return objectMergeResult{}, err
		}
	}

	if len(manifestPayloads) > 0 {
		merged, err := mergeBackupManifests(manifestPayloads)
		if err != nil {
			return objectMergeResult{}, fmt.Errorf("gộp %s: %w", objectsManifestRelPath, err)
		}
		dst := filepath.Join(destDir, filepath.FromSlash(objectsManifestRelPath))
		if err := os.MkdirAll(filepath.Dir(dst), 0o755); err != nil {
			return objectMergeResult{}, err
		}
		if err := os.WriteFile(dst, merged, 0o644); err != nil {
			return objectMergeResult{}, err
		}
	}

	conflicts := make([]string, 0, len(conflictSet))
	for rel := range conflictSet {
		conflicts = append(conflicts, rel)
	}
	sort.Strings(conflicts)
	return objectMergeResult{Conflicts: conflicts}, nil
}

// mergeBackupManifests gộp nhiều bản backups/manifest.json (mỗi bản là một
// mảng JSON các BackupEntry.to_json(), xem apps/api/gh/backup.py) thành MỘT
// mảng JSON duy nhất, DUY NHẤT theo trường "key" (khoá backup, đã bao gồm
// dấu thời gian + hash ngắn — hai entry cùng "key" trong thực tế luôn là
// cùng một bản backup). Giữ nguyên toàn bộ nội dung JSON gốc của mỗi entry
// (không diễn giải các trường khác) — genh không cần hiểu định dạng
// BackupEntry đầy đủ, chỉ cần gộp đúng danh mục để `gh.backup`/`gh.bundle`
// phía Python (việc của agent khác) đọc lại đúng.
//
// Thứ tự phần tử trong mảng ra KHÔNG quan trọng về mặt đúng-sai — phía
// Python luôn tự sắp lại theo taken_at khi đọc (_read_manifest/
// list_backups) — ở đây sắp theo "key" tăng dần chỉ để kết quả xác định
// (test dễ so sánh), không mang ý nghĩa nghiệp vụ.
func mergeBackupManifests(payloads [][]byte) ([]byte, error) {
	type keyed struct {
		Key string `json:"key"`
	}
	seen := make(map[string]json.RawMessage)
	var order []string

	for _, p := range payloads {
		var entries []json.RawMessage
		if err := json.Unmarshal(p, &entries); err != nil {
			return nil, fmt.Errorf("phân tích %s: %w", objectsManifestRelPath, err)
		}
		for _, e := range entries {
			var k keyed
			if err := json.Unmarshal(e, &k); err != nil {
				return nil, fmt.Errorf("phân tích một mục trong %s: %w", objectsManifestRelPath, err)
			}
			if k.Key == "" {
				continue
			}
			if _, ok := seen[k.Key]; !ok {
				order = append(order, k.Key)
			}
			seen[k.Key] = e
		}
	}

	sort.Strings(order)
	out := make([]json.RawMessage, 0, len(order))
	for _, k := range order {
		out = append(out, seen[k])
	}
	return json.Marshal(out)
}
