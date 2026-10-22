package autoupdate

import (
	"context"
	"errors"
	"fmt"
	"html"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
)

// ─── Lịch dùng chung giữa các bản cài (v0.1.53, F-98) ───────────────────────
//
// Tên unit systemd --user (gen-harness-update.timer/.service,
// gen-harness-update-request.path/.service, gen-harness-watchdog.*,
// gen-harness-offsite.*), Label LaunchAgent và marker crontab là CHUNG cho mọi
// bản cài của cùng người dùng. Trước v0.1.53 `genh uninstall --install-dir
// <bản khác>` gỡ luôn lịch đêm + trình nhận yêu cầu của bản cài CHÍNH, và
// `genh install|update --install-dir <bản khác>` ghi đè .path sang hộp thư bản
// kia. Từ nay mỗi lịch ghi rõ bản cài chủ (Environment=GEN_HARNESS_HOME= /
// --install-dir) và bản cài khác KHÔNG gỡ, KHÔNG ghi đè lịch của bản còn sống.

// ErrScheduleOwnedByOther: lịch thuộc một bản cài khác còn sống — bản đang thao
// tác không đổi lịch. Dùng errors.Is(err, ErrScheduleOwnedByOther).
var ErrScheduleOwnedByOther = errors.New("lịch thuộc bản cài khác")

// OwnedByOtherError là lỗi cụ thể (Other: bản cài đang giữ lịch; Self: bản
// cài vừa thao tác). Is(ErrScheduleOwnedByOther) = true.
type OwnedByOtherError struct {
	Other string
	Self  string
}

func (e *OwnedByOtherError) Error() string {
	return fmt.Sprintf("Máy này có bản cài khác đang giữ lịch đêm/nút Cập nhật ngay (%s) — bản cài %s không đổi lịch.", e.Other, e.Self)
}

func (e *OwnedByOtherError) Is(target error) bool { return target == ErrScheduleOwnedByOther }

// OwnerOf trả bản cài đang giữ lịch nếu err (hoặc lỗi bọc) là OwnedByOtherError.
func OwnerOf(err error) (other string, ok bool) {
	var oe *OwnedByOtherError
	if errors.As(err, &oe) {
		return oe.Other, true
	}
	return "", false
}

// keptMessage là câu Disable* trả khi KHÔNG gỡ vì lịch thuộc bản cài khác.
func keptMessage(what, other string) string {
	return fmt.Sprintf("Giữ nguyên %s của bản cài %s (không phải bản đang gỡ).", what, other)
}

// samePath so sánh hai đường dẫn: filepath.Clean + EvalSymlinks (lỗi ⇒ dùng
// chính chuỗi đã Clean).
func samePath(a, b string) bool {
	ca, cb := filepath.Clean(a), filepath.Clean(b)
	if ca == cb {
		return true
	}
	if ea, err := filepath.EvalSymlinks(ca); err == nil {
		ca = ea
	}
	if eb, err := filepath.EvalSymlinks(cb); err == nil {
		cb = eb
	}
	return ca == cb
}

// installAlive: bản cài còn sống = còn <dir>/config/secrets.json.
func installAlive(dir string) bool {
	if dir == "" {
		return false
	}
	fi, err := os.Stat(filepath.Join(dir, "config", "secrets.json"))
	return err == nil && fi.Mode().IsRegular()
}

// unitPath: unitFile là tên tệp trong ~/.config/systemd/user (hoặc đường dẫn tuyệt đối).
func unitPath(home, unitFile string) string {
	if filepath.IsAbs(unitFile) {
		return unitFile
	}
	return filepath.Join(systemdUserDir(home), unitFile)
}

// UnitInstallDir đọc bản cài chủ của một unit đã cài: `Environment=GEN_HARNESS_HOME=`
// hoặc `--install-dir <x>` trong ExecStart. Unit có mà không ghi cả hai (bản
// genh trước v0.1.53) ⇒ gốc mặc định theo HOME (defaultOwner). found=false khi
// không có tệp (hoặc không đọc được).
func UnitInstallDir(home, unitFile string) (dir string, found bool) {
	b, err := os.ReadFile(unitPath(home, unitFile))
	if err != nil {
		return "", false
	}
	return installDirOfText(string(b))
}

// installDirOfText: bản cài chủ của nội dung một tệp lịch (unit systemd, plist
// LaunchAgent).
func installDirOfText(text string) (string, bool) {
	if d, ok := installDirFromLines(text); ok {
		return d, true
	}
	if d, ok := installDirFromPlist(text); ok {
		return d, true
	}
	return defaultOwner()
}

// defaultOwner: bản cài chủ của một lịch do genh TRƯỚC v0.1.53 ghi (không mang
// --install-dir/GEN_HARNESS_HOME) = gốc mặc định theo HOME (config.HomeRoot —
// ~/.gen-harness, Windows %LOCALAPPDATA%\GenHarness). KHÔNG đọc $GEN_HARNESS_HOME của
// người đang chạy lệnh: lịch đó chạy ngoài phiên shell (không có biến này) nên luôn làm
// việc trên gốc theo HOME; đọc biến của người gọi thì `GEN_HARNESS_HOME=/opt/b genh
// uninstall` coi lịch cũ là của /opt/b và gỡ mất lịch của bản chính.
func defaultOwner() (string, bool) {
	d, err := config.HomeRoot()
	if err != nil || d == "" {
		return "", false
	}
	return d, true
}

// installDirFromLines tìm trong các dòng dạng unit systemd/crontab.
func installDirFromLines(text string) (string, bool) {
	var fromEnv, fromArg string
	for _, line := range strings.Split(text, "\n") {
		line = strings.TrimSpace(line)
		switch {
		case strings.HasPrefix(line, "Environment="):
			if d, ok := installDirFromWords(splitWords(strings.TrimPrefix(line, "Environment="))); ok && fromEnv == "" {
				fromEnv = d
			}
		case strings.HasPrefix(line, "ExecStart="):
			if d, ok := installDirFromWords(splitWords(strings.TrimPrefix(line, "ExecStart="))); ok && fromArg == "" {
				fromArg = d
			}
		}
	}
	if fromEnv != "" {
		return fromEnv, true
	}
	return fromArg, fromArg != ""
}

// installDirFromWords: `--install-dir X`, `--install-dir=X` hoặc `GEN_HARNESS_HOME=X`.
func installDirFromWords(words []string) (string, bool) {
	for i, w := range words {
		switch {
		case w == "--install-dir" && i+1 < len(words) && words[i+1] != "":
			return words[i+1], true
		case strings.HasPrefix(w, "--install-dir=") && len(w) > len("--install-dir="):
			return strings.TrimPrefix(w, "--install-dir="), true
		case strings.HasPrefix(w, config.EnvRoot+"=") && len(w) > len(config.EnvRoot)+1:
			return strings.TrimPrefix(w, config.EnvRoot+"="), true
		}
	}
	return "", false
}

var (
	plistEnvRe = regexp.MustCompile(`(?s)<key>` + config.EnvRoot + `</key>\s*<string>([^<]*)</string>`)
	plistArgRe = regexp.MustCompile(`(?s)<string>--install-dir</string>\s*<string>([^<]*)</string>`)
)

func installDirFromPlist(text string) (string, bool) {
	for _, re := range []*regexp.Regexp{plistEnvRe, plistArgRe} {
		if m := re.FindStringSubmatch(text); m != nil && m[1] != "" {
			return html.UnescapeString(m[1]), true
		}
	}
	return "", false
}

// crontabInstallDir: bản cài chủ của dòng cron đứng ngay sau marker trong crontab.
func crontabInstallDir(crontab, marker string) (string, bool) {
	lines := strings.Split(crontab, "\n")
	for i, l := range lines {
		if strings.TrimSpace(l) != marker || i+1 >= len(lines) {
			continue
		}
		cmd := strings.TrimSpace(lines[i+1])
		// Bỏ 5 trường thời gian.
		fields := splitWords(cmd)
		if len(fields) > 5 {
			fields = fields[5:]
		}
		if d, ok := installDirFromWords(fields); ok {
			return d, true
		}
		return defaultOwner()
	}
	return "", false
}

// OwnedByOther: unit `unitFile` thuộc một bản cài KHÁC installDir VÀ bản đó còn
// sống (có <dir>/config/secrets.json) ⇒ (thư mục bản kia, true). Unit không có,
// installDir rỗng, cùng một bản cài, hay bản kia đã gỡ ⇒ ("", false).
func OwnedByOther(home, unitFile, installDir string) (other string, yes bool) {
	dir, found := UnitInstallDir(home, unitFile)
	if !found {
		return "", false
	}
	return otherAlive(dir, installDir)
}

// otherAlive: chủ lịch dir khác installDir và còn sống.
func otherAlive(owner, installDir string) (string, bool) {
	if owner == "" || installDir == "" || samePath(owner, installDir) || !installAlive(owner) {
		return "", false
	}
	return owner, true
}

// guardDir là bản cài đang thao tác (để bảo vệ lịch dùng chung); rỗng = không bảo vệ.
func (d Deps) guardDir() string {
	if d.InstallDir != "" {
		return d.InstallDir
	}
	return d.Nightly.InstallDir
}

// guardUnit: nil nếu bản đang thao tác được đổi lịch của unit này; ngược lại
// *OwnedByOtherError.
func (d Deps) guardUnit(home, unitFile string) error {
	self := d.guardDir()
	if self == "" {
		return nil
	}
	if other, yes := OwnedByOther(home, unitFile, self); yes {
		return &OwnedByOtherError{Other: other, Self: self}
	}
	return nil
}

// ownerOnLinux tìm chủ của một lịch Linux: tệp unit trước, không có unit thì
// dòng cron theo marker (đọc crontab hiện tại). self rỗng ⇒ không bảo vệ.
func (d Deps) ownerOnLinux(ctx context.Context, home, unitFile, marker string) (other string, yes bool) {
	self := d.guardDir()
	if self == "" {
		return "", false
	}
	if _, err := os.Stat(unitPath(home, unitFile)); err == nil {
		return OwnedByOther(home, unitFile, self)
	}
	out, err := d.runner().Output(ctx, "crontab", []string{"-l"})
	if err != nil {
		return "", false
	}
	if dir, found := crontabInstallDir(string(out), marker); found {
		return otherAlive(dir, self)
	}
	return "", false
}

// guardLinux = ownerOnLinux trả *OwnedByOtherError.
func (d Deps) guardLinux(ctx context.Context, home, unitFile, marker string) error {
	if other, yes := d.ownerOnLinux(ctx, home, unitFile, marker); yes {
		return &OwnedByOtherError{Other: other, Self: d.guardDir()}
	}
	return nil
}

// guardDarwin: plist đã cài thuộc bản cài khác còn sống?
func (d Deps) guardDarwin(plistPath string) error {
	self := d.guardDir()
	if self == "" {
		return nil
	}
	b, err := os.ReadFile(plistPath)
	if err != nil {
		return nil
	}
	dir, ok := installDirOfText(string(b))
	if !ok {
		return nil
	}
	if other, yes := otherAlive(dir, self); yes {
		return &OwnedByOtherError{Other: other, Self: self}
	}
	return nil
}

// splitWords tách chuỗi kiểu shell/systemd: khoảng trắng ngăn từ, '…' và "…"
// gom thành một từ (nối liền với phần kề), \ thoát ký tự kế (ngoài '…') — CHỈ khi
// ký tự kế là khoảng trắng, dấu nháy hoặc \; còn lại \ giữ nguyên là ký tự thường để
// đường dẫn kiểu Windows (C:\Users\…, genh ghi unit không thoát) đọc lại đúng.
func splitWords(s string) []string {
	var words []string
	var cur strings.Builder
	inWord := false
	var quote rune
	runes := []rune(s)
	escapes := func(i int, set string) bool { return i+1 < len(runes) && strings.ContainsRune(set, runes[i+1]) }
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		switch {
		case quote == '\'':
			if r == '\'' {
				quote = 0
			} else {
				cur.WriteRune(r)
			}
		case quote == '"':
			switch {
			case r == '"':
				quote = 0
			case r == '\\' && escapes(i, "\"\\"):
				i++
				cur.WriteRune(runes[i])
			default:
				cur.WriteRune(r)
			}
		case r == '\'' || r == '"':
			quote = r
			inWord = true
		case r == '\\' && escapes(i, " \t'\"\\"):
			i++
			cur.WriteRune(runes[i])
			inWord = true
		case r == ' ' || r == '\t' || r == '\r':
			if inWord {
				words = append(words, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}
	if inWord {
		words = append(words, cur.String())
	}
	return words
}
