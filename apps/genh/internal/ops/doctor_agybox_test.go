package ops

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

const doctorAgyCompose = doctorTestCompose + `  agy-box:
    image: ghcr.io/o/gen-harness-api:v0.1.63
`

func doctorAgyLine(t *testing.T, composeYAML, psJSON string) (diagLine, bool) {
	t.Helper()
	composePath := testComposePath(t, composeYAML)
	env := testEnv(t, composePath)
	r := &fake.Runner{Responses: []fake.Response{
		{Match: fake.MatchArgsContain("ps", "agy-box"), Output: []byte(psJSON)},
		{Output: nil},
	}}
	lines, _ := collectDoctorLines(context.Background(), env, doctorTestDeps(r, time.Now()), r)
	for _, l := range lines {
		if strings.Contains(l.Check, "agy-box") {
			return l, true
		}
	}
	return diagLine{}, false
}

// v0.1.63 (H3): genh doctor cho biết hộp riêng agy-box có chạy khoẻ không.
func TestDoctor_AgyBoxKhoe(t *testing.T) {
	l, ok := doctorAgyLine(t, doctorAgyCompose, `[{"Service":"agy-box","State":"running","Health":"healthy"}]`)
	if !ok || !l.OK || !strings.Contains(l.Info, "healthy") {
		t.Fatalf("agy-box healthy phải ✓: %+v (có dòng: %v)", l, ok)
	}
}

func TestDoctor_AgyBoxHong(t *testing.T) {
	for name, ps := range map[string]string{
		"unhealthy":         `[{"Service":"agy-box","State":"running","Health":"unhealthy"}]`,
		"đã thoát":          `[{"Service":"agy-box","State":"exited","Health":""}]`,
		"chưa có container": `[]`,
	} {
		t.Run(name, func(t *testing.T) {
			l, ok := doctorAgyLine(t, doctorAgyCompose, ps)
			if !ok || l.OK {
				t.Fatalf("phải có dòng agy-box và ✕: %+v (có dòng: %v)", l, ok)
			}
			if !strings.Contains(l.Info, "genh logs agy-box") {
				t.Errorf("dòng lỗi phải chỉ cách xem log: %q", l.Info)
			}
		})
	}
}

// compose cũ (v0.1.62) chưa có agy-box ⇒ doctor không bịa dòng nào.
func TestDoctor_KhongCoAgyBoxTrongCompose(t *testing.T) {
	if l, ok := doctorAgyLine(t, doctorTestCompose, `[]`); ok {
		t.Fatalf("compose không có agy-box thì không được có dòng agy-box: %+v", l)
	}
}

// Gói sửa review v0.1.63 (P2-2): hộp chỉ ra Internet qua proxy agy-egress ⇒ doctor báo cả proxy.
const doctorAgyEgressCompose = doctorAgyCompose + `  agy-egress:
    image: ghcr.io/o/gen-harness-api:v0.1.63
`

func TestDoctor_AgyEgressHong(t *testing.T) {
	for name, ps := range map[string]string{
		"đã thoát": `[{"Service":"agy-box","State":"running","Health":"healthy"},{"Service":"agy-egress","State":"exited","Health":""}]`,
		"chưa có":  `[{"Service":"agy-box","State":"running","Health":"healthy"}]`,
	} {
		t.Run(name, func(t *testing.T) {
			l, ok := doctorAgyLine(t, doctorAgyEgressCompose, ps)
			if !ok || l.OK || !strings.Contains(l.Info, "genh logs agy-egress") {
				t.Fatalf("proxy hỏng phải ✕ và chỉ cách xem log: %+v (có dòng: %v)", l, ok)
			}
		})
	}
}

func TestDoctor_AgyEgressKhoe(t *testing.T) {
	l, ok := doctorAgyLine(t, doctorAgyEgressCompose,
		`[{"Service":"agy-box","State":"running","Health":"healthy"},{"Service":"agy-egress","State":"running","Health":"healthy"}]`)
	if !ok || !l.OK || !strings.Contains(l.Info, "proxy lối ra") {
		t.Fatalf("hộp + proxy khoẻ phải ✓ và nhắc proxy: %+v (có dòng: %v)", l, ok)
	}
}
