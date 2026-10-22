package install

import (
	"context"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/config"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/secretgen"
)

// secretsStep cài Bước 4 — Sinh bí mật & cấu hình (3%): khoá master, mật
// khẩu DB (superuser + role gh_app hạn quyền), khoá backup, CA TLS nội bộ,
// mã thiết lập một lần. Idempotent qua secretgen.Ensure: chạy lại không sinh
// lại bí mật đã có.
type secretsStep struct{}

func (secretsStep) ID() StepID   { return StepSecrets }
func (secretsStep) Name() string { return "Sinh bí mật & cấu hình" }

func (s secretsStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	dir, err := resolveConfigDir(env)
	if err != nil {
		se := &StepError{
			Code: ErrCodeSecretsWriteFailed,
			What: "Không xác định được thư mục cấu hình",
			Why:  err.Error(),
			Next: "Đặt lại --install-dir hoặc kiểm tra biến môi trường HOME rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	// Phải stat TRƯỚC Ensure (Ensure tạo secrets.json) — xem Env.FreshInstall.
	fresh := !secretgen.Exists(dir)
	res, err := secretgen.Ensure(dir)
	if err != nil {
		se := &StepError{
			Code: ErrCodeSecretsWriteFailed,
			What: "Không ghi được bí mật/cấu hình",
			Why:  err.Error(),
			Next: "Kiểm tra quyền ghi tại " + dir + " rồi bấm r để thử lại.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	if env != nil {
		env.Secrets = res
		env.FreshInstall = fresh
	}

	if res.GeneratedNew {
		rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: "đã sinh bí mật mới"})
	} else {
		rep.Report(Progress{Status: StatusSkipped, Percent: 100, Detail: "đã có sẵn từ lần cài trước"})
	}
	return nil
}

func resolveConfigDir(env *Env) (string, error) {
	root := ""
	if env != nil {
		root = env.InstallDir
	}
	if root == "" {
		r, err := config.DefaultRoot()
		if err != nil {
			return "", err
		}
		root = r
	}
	return config.New(root).ConfigDir(), nil
}
