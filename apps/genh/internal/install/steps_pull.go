package install

import (
	"context"
	"fmt"
	"strings"
	"sync"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/pull"
)

// pullServiceOrder là 6 service cần image (đã bỏ "objects" — MinIO không
// còn trong compose.yaml, xem docs/reports/HANDOFF-v0.1.1.md), theo đúng
// thứ tự liệt kê trong docs/handoff/05-installer.md Bước 3 — thứ tự này
// cũng là thứ tự hiển thị SubLines khi nhiều image đang tải cùng lúc.
//
// v0.1.63 (H3): thêm agy-box (hộp riêng Antigravity). Bản phát hành ghim nó CÙNG digest với api
// (chung deploy/images/api.Dockerfile) nên resolveImages gộp ảnh trùng — chỉ tải một lần. Gói sửa review v0.1.63
// (P2-2): thêm agy-egress (proxy lối ra của hộp, cũng cùng ảnh api).
var pullServiceOrder = []string{"db", "redis", "proxy", "api", "agy-box", "agy-egress", "web", "bridge", "browser"}

// pullStep cài Bước 3 — Tải image (50%, trọng số lớn nhất): tải song song
// image của 7 service compose.yaml có sẵn "image:" (build: cục bộ chưa có
// gì để pull — xem resolveImages), % tổng theo BYTE THẬT tổng hợp từ mọi
// layer đang tải (pull.Aggregator), không chia đều theo số image.
type pullStep struct {
	// puller cho phép tiêm pull.Puller giả khi test — nil dùng pull.CLIPuller.
	puller pull.Puller
	// locate cho phép tiêm compose.LocateAndSync giả khi test — nil dùng
	// compose.LocateAndSync (genh install CÓ trách nhiệm đồng bộ compose.yaml
	// nhúng, khác Locate thường dùng ở internal/ops — xem doc-comment
	// compose.LocateAndSync).
	locate func(installDir string) (string, error)
}

func (pullStep) ID() StepID   { return StepPullImages }
func (pullStep) Name() string { return "Tải image" }

func (s pullStep) Run(ctx context.Context, env *Env, rep Reporter) error {
	locate := s.locate
	if locate == nil {
		locate = compose.LocateAndSync
	}
	installDir := ""
	if env != nil {
		installDir = env.InstallDir
	}

	composePath, err := locate(installDir)
	if err != nil {
		se := &StepError{
			Code: ErrCodeComposeNotFound,
			What: "Không tìm thấy deploy/compose.yaml",
			Why:  err.Error(),
			Next: "Đặt biến GENH_COMPOSE_FILE trỏ tới compose.yaml rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	cf, err := compose.Load(composePath)
	if err != nil {
		se := &StepError{
			Code: ErrCodeComposeParse,
			What: "Không đọc được compose.yaml",
			Why:  err.Error(),
			Next: "Kiểm compose.yaml có đúng cú pháp YAML rồi bấm r.",
			Err:  err,
		}
		rep.Report(Progress{Status: StatusError, Percent: 100, Err: se})
		return se
	}

	images, skipped := resolveImages(cf)
	if len(images) == 0 {
		rep.Report(Progress{
			Status:  StatusWarn,
			Percent: 100,
			Detail:  "compose.yaml chưa có image sẵn để tải (toàn bộ service dùng build: cục bộ) — bỏ qua, xem docs/PLAN.md mục CI phát hành",
		})
		return nil
	}

	puller := s.puller
	if puller == nil {
		puller = pull.CLIPuller{}
	}

	agg := pull.NewAggregator(images)
	rep.Report(Progress{Status: StatusRunning, Percent: 0, Detail: pullDetail(images, skipped)})

	// safeRep tuần tự hoá Report(): tải song song 7 image nghĩa là nhiều
	// goroutine cùng báo tiến độ — Reporter không có hợp đồng phải an toàn
	// đa luồng (Runner thật khoá bên trong applyProgress, nhưng Step không
	// nên PHỤ THUỘC vào đó), nên tự khoá ở đây.
	var repMu sync.Mutex
	safeRep := ReporterFunc(func(p Progress) {
		repMu.Lock()
		defer repMu.Unlock()
		rep.Report(p)
	})

	var wg sync.WaitGroup
	failures := make(chan string, len(images))
	for _, image := range images {
		wg.Add(1)
		go func(image string) {
			defer wg.Done()
			err := puller.Pull(ctx, image, func(ev pull.Event) {
				agg.Apply(ev)
				reportPullProgress(safeRep, agg, images, skipped)
			})
			if err != nil {
				failures <- fmt.Sprintf("%s: %v", image, err)
			}
		}(image)
	}
	wg.Wait()
	close(failures)

	var errs []string
	for f := range failures {
		errs = append(errs, f)
	}
	for image, msg := range agg.Errors() {
		errs = append(errs, fmt.Sprintf("%s: %s", image, msg))
	}

	if len(errs) > 0 {
		se := &StepError{
			Code: ErrCodePullFailed,
			What: fmt.Sprintf("Không tải được %d/%d image", len(errs), len(images)),
			Why:  strings.Join(errs, "; "),
			Next: "Kiểm kết nối mạng rồi bấm r — image đã tải xong không bị tải lại (idempotent).",
		}
		rep.Report(Progress{Status: StatusError, Percent: agg.Percent(), Err: se})
		return se
	}

	rep.Report(Progress{Status: StatusOK, Percent: 100, Detail: pullDetail(images, skipped)})
	return nil
}

// resolveImages đối chiếu pullServiceOrder với compose.yaml đã đọc: image
// dùng để pull là những service có "image:" cố định; service chỉ có
// "build:" (chưa được publish sẵn — xem giới hạn dưới) bị bỏ qua, liệt vào
// skipped để hiển thị rõ ràng thay vì âm thầm mất tích khỏi tiến độ.
//
// GIỚI HẠN: deploy/compose.yaml của repo hiện tại (giai đoạn dev, trước khi
// có CI phát hành đa kiến trúc lên GHCR — xem docs/PLAN.md "Giai đoạn 6")
// chỉ có "image:" cho proxy/redis; api/web/bridge/db dùng "build:"
// cục bộ nên KHÔNG có gì để Bước 3 tải cho tới khi bản phát hành thật ghim
// digest ảnh đã build sẵn vào compose.yaml (đúng như tài liệu mô tả).
func resolveImages(cf compose.File) (images, skipped []string) {
	seen := map[string]bool{}
	for _, name := range pullServiceOrder {
		svc, ok := cf.Services[name]
		if !ok || svc.Image == "" {
			skipped = append(skipped, name)
			continue
		}
		if seen[svc.Image] { // nhiều service chung một ảnh (api + agy-box): tải một lần
			continue
		}
		seen[svc.Image] = true
		images = append(images, svc.Image)
	}
	return images, skipped
}

func pullDetail(images, skipped []string) string {
	detail := fmt.Sprintf("%d image", len(images))
	if len(skipped) > 0 {
		detail += fmt.Sprintf(" · %d chưa có bản phát hành, bỏ qua", len(skipped))
	}
	return detail
}

// reportPullProgress dựng và gửi một Progress theo trạng thái hiện tại của
// agg — Percent là % TỔNG theo byte thật trên mọi image (không chia đều),
// SubLines liệt kê tối đa 4 image đang tải kèm % riêng, đúng mockup TUI.
func reportPullProgress(rep Reporter, agg *pull.Aggregator, images, skipped []string) {
	var sub []string
	for _, img := range agg.Images() {
		sub = append(sub, fmt.Sprintf("%-24s %3.0f%%", shortImageName(img), agg.ImagePercent(img)))
		if len(sub) == 4 {
			break
		}
	}
	rep.Report(Progress{
		Status:   StatusRunning,
		Percent:  agg.Percent(),
		Detail:   pullDetail(images, skipped),
		SubLines: sub,
	})
}

// shortImageName rút gọn "ghcr.io/org/api:v2.2.0" thành "api" để hiển thị
// gọn trong dòng con (52 cột dành cho cả tên lẫn thanh %, xem internal/tui).
func shortImageName(image string) string {
	name := image
	if i := strings.LastIndex(name, "/"); i >= 0 {
		name = name[i+1:]
	}
	if i := strings.Index(name, ":"); i >= 0 {
		name = name[:i]
	}
	if i := strings.Index(name, "@"); i >= 0 {
		name = name[:i]
	}
	return name
}
