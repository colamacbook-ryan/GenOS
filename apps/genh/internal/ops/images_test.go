package ops

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli/fake"
)

// rmiRefs trả mỗi lệnh `docker rmi` đã gọi dưới dạng đối số sau "rmi": "ref" khi
// KHÔNG -f, "-f ref" khi có -f — để test kiểm đúng cả tham chiếu lẫn cờ.
func rmiRefs(fr *fake.Runner) []string {
	var refs []string
	for _, c := range fr.Calls {
		if len(c.Cmd.Args) > 0 && c.Cmd.Args[0] == "rmi" {
			if hasExactArgs(c.Cmd.Args, "--force") {
				panic("chỉ được dùng -f, không dùng --force")
			}
			refs = append(refs, strings.Join(c.Cmd.Args[1:], " "))
		}
	}
	return refs
}

func TestPruneOldImages_KeepsCurrentAndPrevious(t *testing.T) {
	current := ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3")
	previous := ghcrCompose("sha256:a2", "sha256:w2", "sha256:d2")

	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a2\tid-a2",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w3\tid-w3",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w2\tid-w2",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w1\tid-w1",
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d3\tid-d3",
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d2\tid-d2",
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d1\tid-d1",
			// Cùng ID với bản đang giữ nhưng gắn tag — không được xoá.
			"ghcr.io/acme/gen-harness-db\tv0.1.34\tsha256:d3\tid-d3",
			"postgres\t16\tsha256:pg\tid-pg",
			"redis\t7-alpine\tsha256:rd\tid-rd",
			"ghcr.io/x/khac\tlatest\tsha256:kh\tid-kh",
			"ghcr.io/acme/khong-phai-gen-harness\tlatest\tsha256:zz\tid-zz",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}

	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{})
	if err != nil {
		t.Fatalf("pruneOldImages: %v", err)
	}
	got := rmiRefs(fr)
	want := []string{
		"ghcr.io/acme/gen-harness-api@sha256:a1",
		"ghcr.io/acme/gen-harness-web@sha256:w1",
		"ghcr.io/acme/gen-harness-db@sha256:d1",
	}
	if strings.Join(got, ",") != strings.Join(want, ",") || n != 3 {
		t.Errorf("rmi = %v (n=%d), muốn %v", got, n, want)
	}
}

func TestPruneOldImages_TagRefsAndNoneDigest(t *testing.T) {
	keep := []byte("services:\n  api:\n    image: ghcr.io/acme/gen-harness-api:v0.1.34\n")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\tv0.1.34\t<none>\tid-new",
			"ghcr.io/acme/gen-harness-api\tv0.1.32\t<none>\tid-old",
			"ghcr.io/acme/gen-harness-api\t<none>\t<none>\tid-dangling",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	if _, err := pruneOldImages(context.Background(), fr, [][]byte{keep}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); len(got) != 1 || got[0] != "ghcr.io/acme/gen-harness-api:v0.1.32" {
		t.Errorf("rmi = %v, muốn chỉ tag cũ v0.1.32", got)
	}
}

func TestPruneOldImages_NoGhcrRefsInKeep_DoesNothing(t *testing.T) {
	fr := &fake.Runner{}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(updateTestComposeYAML), nil}, &strings.Builder{})
	if err != nil || n != 0 {
		t.Fatalf("muốn 0,nil — được %d,%v", n, err)
	}
	if len(fr.Calls) != 0 {
		t.Errorf("compose build cục bộ (không có ảnh gen-harness) thì không được gọi docker: %+v", fr.Calls)
	}
}

func TestPruneOldImages_RmiErrorContinues(t *testing.T) {
	keep := []byte(ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3"))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w1\tid-w1",
		)},
		{Match: exactArgs("rmi", "ghcr.io/acme/gen-harness-api@sha256:a1"), Err: errors.New("image is being used by running container")},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	var out strings.Builder
	n, err := pruneOldImages(context.Background(), fr, [][]byte{keep}, &out)
	if err != nil {
		t.Fatal(err)
	}
	if n != 1 || len(rmiRefs(fr)) != 2 {
		t.Errorf("rmi lỗi phải đi tiếp: n=%d calls=%v", n, rmiRefs(fr))
	}
	if !strings.Contains(out.String(), "không xoá được ảnh ghcr.io/acme/gen-harness-api@sha256:a1") {
		t.Errorf("phải in một dòng khi rmi lỗi: %q", out.String())
	}
}

func TestSplitImageRef(t *testing.T) {
	cases := []struct{ ref, repo, digest, tag string }{
		{"ghcr.io/o/gen-harness-api@sha256:x", "ghcr.io/o/gen-harness-api", "sha256:x", ""},
		{"ghcr.io/o/gen-harness-api:v1", "ghcr.io/o/gen-harness-api", "", "v1"},
		{"ghcr.io/o/gen-harness-api:v1@sha256:x", "ghcr.io/o/gen-harness-api", "sha256:x", "v1"},
		{"ghcr.io/o/gen-harness-api", "ghcr.io/o/gen-harness-api", "", "latest"},
		{"localhost:5000/gen", "localhost:5000/gen", "", "latest"},
	}
	for _, c := range cases {
		r, d, tg := splitImageRef(c.ref)
		if r != c.repo || d != c.digest || tg != c.tag {
			t.Errorf("splitImageRef(%q) = %q,%q,%q", c.ref, r, d, tg)
		}
	}
}

// v0.1.48: compose ghim digest cho caddy/redis (Renovate nâng digest hằng tuần) —
// mỗi lần update đổi digest để lại một ảnh cũ ~40–60MB nếu không dọn.
func pinnedCompose(api, caddy, redis string) string {
	return "name: gen-harness\nservices:\n" +
		"  api:\n    image: ghcr.io/acme/gen-harness-api@" + api + "\n" +
		"  proxy:\n    image: caddy:2-alpine@" + caddy + "\n" +
		"  redis:\n    image: redis:7-alpine@" + redis + "\n" +
		"  browser-redis:\n    image: docker.io/library/redis:7-alpine@" + redis + "\n"
}

func TestPruneOldImages_PinnedThirdPartyDigests(t *testing.T) {
	current := pinnedCompose("sha256:a3", "sha256:c3", "sha256:r3")
	previous := pinnedCompose("sha256:a2", "sha256:c2", "sha256:r2")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a2\tid-a2",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"caddy\t<none>\tsha256:c3\tid-c3",
			"caddy\t<none>\tsha256:c2\tid-c2",
			"caddy\t<none>\tsha256:c1\tid-c1",
			"redis\t<none>\tsha256:r3\tid-r3",
			"redis\t<none>\tsha256:r2\tid-r2",
			"redis\t<none>\tsha256:r1\tid-r1",
			// Có tag: bản genh cũ kéo theo tag hoặc dự án khác — không bao giờ đụng.
			"caddy\t2-alpine\tsha256:ct\tid-ct",
			"redis\t6\tsha256:r6\tid-r6",
			// Repo compose không ghim digest / không dùng — không đụng.
			"postgres\t<none>\tsha256:pg\tid-pg",
			"nginx\t<none>\tsha256:ng\tid-ng",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	got := rmiRefs(fr)
	want := []string{
		"ghcr.io/acme/gen-harness-api@sha256:a1",
		"caddy@sha256:c1",
		"redis@sha256:r1",
	}
	if strings.Join(got, ",") != strings.Join(want, ",") || n != 3 {
		t.Errorf("rmi = %v (n=%d), muốn %v", got, n, want)
	}
}

// Bản liền trước (≤ v0.1.47) dùng caddy/redis theo TAG, bản hiện tại ghim digest
// ⇒ chỉ dọn bản ghim digest khác của đúng repo; ảnh kéo theo tag vẫn giữ (rollback).
func TestPruneOldImages_PreviousUsesTags_KeepsTagged(t *testing.T) {
	current := pinnedCompose("sha256:a3", "sha256:c3", "sha256:r3")
	previous := "services:\n  api:\n    image: ghcr.io/acme/gen-harness-api@sha256:a2\n" +
		"  proxy:\n    image: caddy:2-alpine\n  redis:\n    image: redis:7-alpine\n"
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"caddy\t<none>\tsha256:c3\tid-c3",
			"caddy\t2-alpine\tsha256:ct\tid-ct",
			"redis\t7-alpine\tsha256:rt\tid-rt",
			"redis\t<none>\tsha256:r3\tid-r3",
			"redis\t<none>\tsha256:rx\tid-rx",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	if _, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "redis@sha256:rx" {
		t.Errorf("rmi = %v, muốn chỉ redis@sha256:rx", got)
	}
}

// Checkout dev (build: cục bộ) có ghim caddy/redis nhưng không có ảnh gen-harness
// ⇒ không gọi docker, không xoá gì.
func TestPruneOldImages_PinnedThirdPartyWithoutGhRefs_DoesNothing(t *testing.T) {
	dev := "services:\n  api:\n    build: ..\n  proxy:\n    image: caddy:2-alpine@sha256:c3\n  redis:\n    image: redis:7-alpine@sha256:r3\n"
	fr := &fake.Runner{}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(dev)}, &strings.Builder{})
	if err != nil || n != 0 || len(fr.Calls) != 0 {
		t.Fatalf("muốn 0,nil và không gọi docker — được %d,%v calls=%+v", n, err, fr.Calls)
	}
}

// Ảnh ngoài đang dùng (container dự án khác, kể cả đã dừng) ⇒ rmi lỗi ⇒ in một dòng, đi tiếp.
func TestPruneOldImages_PinnedThirdPartyInUseContinues(t *testing.T) {
	keep := []byte(pinnedCompose("sha256:a3", "sha256:c3", "sha256:r3"))
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"caddy\t<none>\tsha256:c1\tid-c1",
			"redis\t<none>\tsha256:r1\tid-r1",
		)},
		{Match: exactArgs("rmi", "redis@sha256:r1"), Err: errors.New("conflict: unable to remove repository reference")},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	var out strings.Builder
	n, err := pruneOldImages(context.Background(), fr, [][]byte{keep}, &out)
	if err != nil || n != 1 || len(rmiRefs(fr)) != 2 {
		t.Fatalf("n=%d err=%v rmi=%v", n, err, rmiRefs(fr))
	}
	if !strings.Contains(out.String(), "không xoá được ảnh redis@sha256:r1") {
		t.Errorf("phải in một dòng khi rmi lỗi: %q", out.String())
	}
}

func TestFamiliarRepo(t *testing.T) {
	cases := map[string]string{
		"redis":                          "redis",
		"docker.io/library/redis":        "redis",
		"index.docker.io/library/caddy":  "caddy",
		"docker.io/pgvector/pgvector":    "pgvector/pgvector",
		"ghcr.io/acme/gen-harness-api":   "ghcr.io/acme/gen-harness-api",
		"mcr.microsoft.com/playwright/x": "mcr.microsoft.com/playwright/x",
	}
	for in, want := range cases {
		if got := familiarRepo(in); got != want {
			t.Errorf("familiarRepo(%q) = %q, muốn %q", in, got, want)
		}
	}
}

// ghDigestCompose: compose chỉ có db (gen-harness, owner "o") ghim digest và redis
// ghim digest — dựng nhanh cho các ca "cùng IMAGE ID, khác digest" bên dưới.
func ghDigestCompose(db, redis string) string {
	return "name: gen-harness\nservices:\n" +
		"  db:\n    image: ghcr.io/o/gen-harness-db@" + db + "\n" +
		"  redis:\n    image: redis:7-alpine@" + redis + "\n"
}

// Cổng phát hành v0.1.50/v0.1.51 đỏ: gen-harness-db không đổi nội dung qua 3 bản
// liên tiếp (CÙNG IMAGE ID) nhưng mỗi bản đẩy manifest mới ⇒ 3 digest khác nhau.
// Digest cũ (ngoài tập giữ) phải bị gỡ dù trùng ID với ảnh giữ; 2 digest của tập
// giữ ở lại. Container db đang chạy ảnh này nên Docker coi 3 digest cùng repo là
// MỘT tham chiếu (isSingleReference) và từ chối `rmi` thường ⇒ phải `rmi -f` (chỉ
// untag, không xoá ảnh vì còn digest giữ).
func TestPruneOldImages_SameIDDifferentDigests_RemovesOldDigestRef(t *testing.T) {
	current := ghDigestCompose("sha256:d3", "sha256:r3")
	previous := ghDigestCompose("sha256:d2", "sha256:r2")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d3\t3653e740d26c",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d2\t3653e740d26c",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d1\t3653e740d26c",
			// Owner khác dùng chung Docker, cùng ID: không thuộc tập giữ ⇒ không đụng.
			"ghcr.io/khac/gen-harness-db\t<none>\tsha256:k1\t3653e740d26c",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "-f ghcr.io/o/gen-harness-db@sha256:d1" || n != 1 {
		t.Errorf("rmi = %v (n=%d), muốn đúng 1 lệnh rmi -f ghcr.io/o/gen-harness-db@sha256:d1", got, n)
	}
}

// Tập giữ chỉ có TAG (bản genh cũ chưa ghim digest) ⇒ không biết digest nào là của
// ảnh giữ ⇒ giữ theo ID cho repo đó như trước: dòng digest cùng ID không bị rmi,
// chỉ tag cũ khác ID bị gỡ.
func TestPruneOldImages_KeepByTag_KeepsSameIDDigestRows(t *testing.T) {
	keep := []byte("services:\n  db:\n    image: ghcr.io/o/gen-harness-db:v0.1.50\n")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\tv0.1.50\t<none>\tid-same",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:x1\tid-same",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:x2\tid-same",
			"ghcr.io/o/gen-harness-db\tv0.1.40\t<none>\tid-old",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{keep}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "ghcr.io/o/gen-harness-db:v0.1.40" || n != 1 {
		t.Errorf("rmi = %v (n=%d), muốn chỉ tag cũ v0.1.40 (dòng digest cùng ID giữ theo ID)", got, n)
	}
}

// Chuyển tiếp: bản hiện tại ghim digest, bản liền trước còn dùng TAG ⇒ dòng giữ nhờ
// tag kéo theo dòng digest cùng ID của repo ấy (có thể chính là digest của nó);
// digest khác ID vẫn bị gỡ.
func TestPruneOldImages_CurrentDigestPreviousTag_KeepsSameIDAsTag(t *testing.T) {
	current := []byte("services:\n  db:\n    image: ghcr.io/o/gen-harness-db@sha256:d3\n")
	previous := []byte("services:\n  db:\n    image: ghcr.io/o/gen-harness-db:v0.1.34\n")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d3\tid-cur",
			"ghcr.io/o/gen-harness-db\tv0.1.34\t<none>\tid-prev",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:dp\tid-prev",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d1\tid-old",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{current, previous}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "ghcr.io/o/gen-harness-db@sha256:d1" || n != 1 {
		t.Errorf("rmi = %v (n=%d), muốn chỉ digest d1 (khác ID)", got, n)
	}
}

// Ảnh ngoài (redis) ghim digest, 2 digest giữ + 1 digest cũ không tag, cùng ID ⇒ gỡ digest
// cũ bằng `rmi -f` (cùng cơ chế isSingleReference với gen-harness-db); dòng có tag cùng ID (redis:7-alpine của bản genh cũ/dự án khác) không bao giờ đụng.
func TestPruneOldImages_PinnedThirdPartySameID_RemovesOldDigestRef(t *testing.T) {
	current := ghDigestCompose("sha256:d3", "sha256:r3")
	previous := ghDigestCompose("sha256:d2", "sha256:r2")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d3\tid-db",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d2\tid-db",
			"redis\t<none>\tsha256:r3\tid-redis",
			"redis\t<none>\tsha256:r2\tid-redis",
			"redis\t<none>\tsha256:r1\tid-redis",
			"redis\t7-alpine\tsha256:rt\tid-redis",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "-f redis@sha256:r1" || n != 1 {
		t.Errorf("rmi = %v (n=%d), muốn chỉ rmi -f redis@sha256:r1 (cùng ID với digest giữ)", got, n)
	}
}

// Ảnh cũ KHÁC image ID với mọi dòng giữ (api/web) ⇒ rmi thường, KHÔNG -f: -f còn xoá
// được ảnh của container đã dừng (có thể của dự án khác), nên chỉ dùng khi ảnh còn
// tham chiếu giữ. Cùng lúc, db cùng ID với dòng giữ ⇒ -f; ghi rõ cả hai trong một ca.
func TestPruneOldImages_ForceOnlyWhenIDStillReferencedByKept(t *testing.T) {
	current := ghcrCompose("sha256:a3", "sha256:w3", "sha256:d3")
	previous := ghcrCompose("sha256:a2", "sha256:w2", "sha256:d2")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a3\tid-a3",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a2\tid-a2",
			"ghcr.io/acme/gen-harness-api\t<none>\tsha256:a1\tid-a1",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w3\tid-w3",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w2\tid-w2",
			"ghcr.io/acme/gen-harness-web\t<none>\tsha256:w1\tid-w1",
			// db: 3 digest CÙNG ID với 2 digest giữ ⇒ chỉ digest cũ d1 bị gỡ, bằng -f.
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d3\tid-d",
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d2\tid-d",
			"ghcr.io/acme/gen-harness-db\t<none>\tsha256:d1\tid-d",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &strings.Builder{})
	if err != nil {
		t.Fatal(err)
	}
	want := []string{
		"ghcr.io/acme/gen-harness-api@sha256:a1", // khác ID ⇒ không -f
		"ghcr.io/acme/gen-harness-web@sha256:w1", // khác ID ⇒ không -f
		"-f ghcr.io/acme/gen-harness-db@sha256:d1",
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != strings.Join(want, ",") || n != 3 {
		t.Errorf("rmi = %v (n=%d), muốn %v", got, n, want)
	}
}

// `rmi -f` thất bại (Docker vẫn từ chối vì lý do khác) ⇒ in đúng một dòng, bỏ qua, đi
// tiếp; removed không tăng cho lệnh lỗi.
func TestPruneOldImages_ForceRmiFailureContinues(t *testing.T) {
	current := ghDigestCompose("sha256:d3", "sha256:r3")
	previous := ghDigestCompose("sha256:d2", "sha256:r2")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d3\tid-db",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d2\tid-db",
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d1\tid-db",
			"redis\t<none>\tsha256:r3\tid-redis",
			"redis\t<none>\tsha256:r2\tid-redis",
			"redis\t<none>\tsha256:r1\tid-redis",
		)},
		{Match: exactArgs("rmi", "-f", "ghcr.io/o/gen-harness-db@sha256:d1"), Err: errors.New("conflict: unable to remove repository reference")},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	var out strings.Builder
	n, err := pruneOldImages(context.Background(), fr, [][]byte{[]byte(current), []byte(previous)}, &out)
	if err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "-f ghcr.io/o/gen-harness-db@sha256:d1,-f redis@sha256:r1" || n != 1 {
		t.Errorf("rmi = %v (n=%d), muốn 2 lệnh -f và removed=1 (chỉ redis thành công)", got, n)
	}
	lines := strings.Split(strings.TrimSpace(out.String()), "\n")
	if len(lines) != 1 || !strings.Contains(lines[0], "không xoá được ảnh ghcr.io/o/gen-harness-db@sha256:d1") {
		t.Errorf("phải in đúng một dòng cho lệnh -f lỗi: %q", out.String())
	}
}

// Dòng giữ ở repo KHÁC nhưng cùng image ID vẫn là tham chiếu giữ của ảnh (repoRefs của
// Docker gom mọi repo) ⇒ -f an toàn; còn tham chiếu TAG cũ thì KHÔNG bao giờ -f (Docker
// dọn digest cùng repo khi gỡ tag cuối, nên -f ở đó có thể kéo cả digest giữ).
func TestPruneOldImages_TagRefNeverForced(t *testing.T) {
	keep := []byte("services:\n  db:\n    image: ghcr.io/o/gen-harness-db@sha256:d3\n  api:\n    image: ghcr.io/o/gen-harness-api:v0.1.50\n")
	fr := &fake.Runner{Responses: []fake.Response{
		{Match: exactArgs("images"), Output: imagesListing(
			"ghcr.io/o/gen-harness-db\t<none>\tsha256:d3\tid-db",
			"ghcr.io/o/gen-harness-api\tv0.1.50\t<none>\tid-api",
			// Tag cũ của api trùng ID với một dòng giữ ở repo db (cùng ID, khác repo).
			"ghcr.io/o/gen-harness-api\tv0.1.40\t<none>\tid-db",
		)},
		{Match: exactArgs("rmi"), Output: []byte("")},
	}}
	if _, err := pruneOldImages(context.Background(), fr, [][]byte{keep}, &strings.Builder{}); err != nil {
		t.Fatal(err)
	}
	if got := rmiRefs(fr); strings.Join(got, ",") != "ghcr.io/o/gen-harness-api:v0.1.40" {
		t.Errorf("rmi = %v, muốn rmi thường (không -f) cho tag cũ", got)
	}
}
