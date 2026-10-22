// Package fake cung cấp một dockercli.Runner giả cho test của các gói phụ
// thuộc dockercli (internal/runtime, internal/pull, internal/compose,
// internal/migrate) — không gọi Docker thật.
package fake

import (
	"context"
	"fmt"
	"io"
	"strings"
	"sync"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// Call ghi lại một lần Runner được gọi, để test kiểm tra đúng lệnh/tham số
// đã dùng (ví dụ compose có truyền đúng -f <path> hay không).
type Call struct {
	Cmd dockercli.Cmd
	// Stdin là toàn bộ bytes RunIO đã đọc từ stdin được đưa vào (nil nếu lần
	// gọi này không phải RunIO, hoặc RunIO gọi với stdin nil) — test dùng để
	// kiểm nội dung thật đã truyền vào tiến trình con (ví dụ `genh import`
	// phải đưa đúng bytes tệp .ghbundle vào stdin).
	Stdin []byte
}

// Response định sẵn kết quả trả về cho một lệnh khớp Match.
type Response struct {
	// Match nhận (name, args) đầy đủ của lệnh, trả true nếu đây là lệnh cần
	// khớp response này. nil Match khớp mọi lệnh chưa có response nào khác
	// khớp trước đó (dùng làm mặc định).
	Match  func(cmd dockercli.Cmd) bool
	Lines  []string // các dòng Stream sẽ phát ra tuần tự qua onLine
	Output []byte   // dữ liệu Output trả về
	Err    error    // lỗi trả về sau khi đã phát hết Lines/Output

	// OutputSeq, nếu khác nil, làm Output đổi theo SỐ LẦN response này đã
	// khớp trước đó trong cùng Runner (0-based, kẹp ở phần tử cuối khi vượt
	// quá) — dùng để giả lập trạng thái đổi qua nhiều lần gọi, ví dụ
	// `docker compose ps` báo "starting" vài lần rồi "healthy" (xem
	// internal/compose.WaitHealthy).
	OutputSeq [][]byte

	// RunIOStdout, nếu khác nil, là bytes RunIO ghi ra Writer stdout được đưa
	// vào (giả lập stdout của tiến trình con, ví dụ nội dung gói .ghbundle mà
	// `python -m gh.bundle export` in ra).
	RunIOStdout []byte
	// ExitCode, khi khác 0, làm RunIO trả về *dockercli.ExitError{Code:
	// ExitCode} thay vì Err — dùng để giả lập mã thoát cụ thể (2 sai mật
	// khẩu/gói hỏng, 3 không tương thích, theo hợp đồng gh.bundle).
	ExitCode int

	// ErrSeq, nếu khác nil, làm lỗi trả về đổi theo SỐ LẦN response này đã
	// khớp (0-based, kẹp phần tử cuối; phần tử nil = thành công) — dùng để
	// giả lập "lỗi, lỗi, rồi được" khi test thử lại (ví dụ `docker compose
	// pull` của genh update). Khi có ErrSeq, Err bị bỏ qua.
	ErrSeq []error
	// WaitCtx làm lệnh CHẶN tới khi ctx.Done() rồi trả ctx.Err() — giả lập
	// lệnh treo để test thời gian chờ tối đa mỗi lần.
	WaitCtx bool
}

// errFor chọn lỗi trả về theo ErrSeq (nếu có) hoặc Err.
func (resp Response) errFor(occurrence int) error {
	if resp.ErrSeq == nil {
		return resp.Err
	}
	idx := occurrence
	if idx >= len(resp.ErrSeq) {
		idx = len(resp.ErrSeq) - 1
	}
	if idx < 0 {
		return nil
	}
	return resp.ErrSeq[idx]
}

// Runner là dockercli.Runner giả, phát Response đã định sẵn theo thứ tự
// khớp đầu tiên, và ghi lại mọi Call để test kiểm tra ngược lại.
type Runner struct {
	mu          sync.Mutex
	Responses   []Response
	Calls       []Call
	matchCounts []int // song song với Responses, đếm số lần mỗi response đã khớp
}

func (r *Runner) record(cmd dockercli.Cmd) {
	r.recordCall(Call{Cmd: cmd})
}

func (r *Runner) recordCall(call Call) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.Calls = append(r.Calls, call)
}

// match trả về response khớp đầu tiên cùng số lần nó ĐÃ khớp trước lần này
// (0 ở lần khớp đầu tiên) — dùng cho OutputSeq.
func (r *Runner) match(cmd dockercli.Cmd) (Response, int, bool) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.matchCounts) != len(r.Responses) {
		r.matchCounts = make([]int, len(r.Responses))
	}
	for i, resp := range r.Responses {
		if resp.Match == nil || resp.Match(cmd) {
			occurrence := r.matchCounts[i]
			r.matchCounts[i]++
			return resp, occurrence, true
		}
	}
	return Response{}, 0, false
}

func (r *Runner) Output(ctx context.Context, cmd dockercli.Cmd) ([]byte, error) {
	r.record(cmd)
	resp, occurrence, ok := r.match(cmd)
	if !ok {
		return nil, fmt.Errorf("fake.Runner: không có Response khớp lệnh %s %v", cmd.Name, cmd.Args)
	}
	if resp.WaitCtx {
		<-ctx.Done()
		return nil, ctx.Err()
	}
	if resp.OutputSeq != nil {
		idx := occurrence
		if idx >= len(resp.OutputSeq) {
			idx = len(resp.OutputSeq) - 1
		}
		if idx >= 0 {
			return resp.OutputSeq[idx], resp.errFor(occurrence)
		}
	}
	return resp.Output, resp.errFor(occurrence)
}

func (r *Runner) Stream(ctx context.Context, cmd dockercli.Cmd, onLine func(line string)) error {
	r.record(cmd)
	resp, occurrence, ok := r.match(cmd)
	if !ok {
		return fmt.Errorf("fake.Runner: không có Response khớp lệnh %s %v", cmd.Name, cmd.Args)
	}
	if resp.WaitCtx {
		<-ctx.Done()
		return ctx.Err()
	}
	for _, l := range resp.Lines {
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
		onLine(l)
	}
	return resp.errFor(occurrence)
}

// RunIO đọc hết stdin (nếu khác nil, ghi lại vào Call.Stdin để test kiểm
// tra), ghi Response.RunIOStdout ra stdout được đưa vào, rồi trả về
// *dockercli.ExitError{Code: Response.ExitCode} nếu ExitCode khác 0, hoặc
// Response.Err nếu không.
func (r *Runner) RunIO(ctx context.Context, cmd dockercli.Cmd, stdin io.Reader, stdout io.Writer) error {
	var stdinData []byte
	if stdin != nil {
		var err error
		stdinData, err = io.ReadAll(stdin)
		if err != nil {
			return fmt.Errorf("fake.Runner.RunIO: đọc stdin: %w", err)
		}
	}
	r.recordCall(Call{Cmd: cmd, Stdin: stdinData})

	resp, occurrence, ok := r.match(cmd)
	if !ok {
		return fmt.Errorf("fake.Runner: không có Response khớp lệnh %s %v", cmd.Name, cmd.Args)
	}
	if resp.WaitCtx {
		<-ctx.Done()
		return ctx.Err()
	}
	if len(resp.RunIOStdout) > 0 && stdout != nil {
		if _, err := stdout.Write(resp.RunIOStdout); err != nil {
			return fmt.Errorf("fake.Runner.RunIO: ghi stdout: %w", err)
		}
	}
	if resp.ExitCode != 0 {
		return &dockercli.ExitError{Code: resp.ExitCode}
	}
	return resp.errFor(occurrence)
}

// MatchArgsContain trả về một Match khớp khi mọi chuỗi trong want đều xuất
// hiện (liền hoặc là chính xác một phần tử) trong Cmd.Args — tiện dựng test
// mà không cần so khớp toàn bộ danh sách tham số.
func MatchArgsContain(want ...string) func(dockercli.Cmd) bool {
	return func(cmd dockercli.Cmd) bool {
		joined := strings.Join(cmd.Args, " \x00 ")
		for _, w := range want {
			if !strings.Contains(joined, w) {
				return false
			}
		}
		return true
	}
}
