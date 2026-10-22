package ops

import (
	"context"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// v0.1.63 (H3): `genh status` hiện hàng agy-box (kèm health) và dung lượng volume agy_state
// — E2E dựa vào cột "agy-box running healthy" của bảng này.
func TestRunStatus_HienAgyBoxVaVolumeAgyState(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "--format", "json"),
			Output: []byte(`[{"Service":"api","State":"running","Health":"healthy"},{"Service":"agy-box","State":"running","Health":"healthy"}]`)},
		{Match: fake.MatchArgsContain("system", "df", "-v"),
			Output: []byte("VOLUME NAME    LINKS   SIZE\ngen-harness_agy_state   2  4kB\ngen-harness_other   1  1MB\n")},
	}}
	var out strings.Builder
	if err := RunStatus(context.Background(), env, "v0.1.63-test", StatusDeps{Runner: fr}, &out); err != nil {
		t.Fatalf("RunStatus: %v", err)
	}
	got := out.String()
	var row string
	for _, l := range strings.Split(got, "\n") {
		if f := strings.Fields(l); len(f) >= 3 && f[0] == "agy-box" {
			row = strings.Join(f, " ")
		}
	}
	if row != "agy-box running healthy" {
		t.Errorf("thiếu hàng \"agy-box running healthy\" trong bảng:\n%s", got)
	}
	if !strings.Contains(got, "agy_state") || strings.Contains(got, "gen-harness_other") {
		t.Errorf("phải liệt kê agy_state (và chỉ volume của Gen-Harness):\n%s", got)
	}
}

// `genh logs agy-box -f` truyền đúng tên dịch vụ cho docker compose logs.
func TestRunLogs_AgyBox(t *testing.T) {
	env := testEnv(t, testComposePath(t, ""))
	fr := &fakeInheritRunner{}
	if err := RunLogs(context.Background(), env, LogsOptions{Services: []string{"agy-box"}, Follow: true}, fr, &strings.Builder{}, &strings.Builder{}, strings.NewReader("")); err != nil {
		t.Fatalf("RunLogs: %v", err)
	}
	if n := len(fr.gotArgs); n == 0 || fr.gotArgs[n-1] != "agy-box" {
		t.Fatalf("args phải kết thúc bằng agy-box: %v", fr.gotArgs)
	}
}
