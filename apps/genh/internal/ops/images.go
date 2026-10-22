package ops

import (
	"context"
	"fmt"
	"io"
	"regexp"
	"strings"

	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/compose"
	"github.com/Genesis-ryan-84-0567536339/gen-harness/apps/genh/internal/dockercli"
)

// ghImageRepoRe khớp ĐÚNG các repo ảnh của Gen-Harness trên ghcr.io — dọn ảnh
// cũ (F-11) dọn mọi bản cũ của các repo này. Ảnh NGOÀI (caddy, redis…) chỉ được
// dọn khi compose giữ GHIM DIGEST cho đúng repo đó (xem pruneOldImages); không
// bao giờ đụng postgres hay ảnh ghcr.io của dự án khác.
var ghImageRepoRe = regexp.MustCompile(`^ghcr\.io/[^/]+/gen-harness-[a-z0-9-]+$`)

// imageRefsFromCompose lấy mọi "image:" không rỗng trong một compose.yaml (lỗi
// cú pháp → không có ref nào).
func imageRefsFromCompose(data []byte) []string {
	if len(data) == 0 {
		return nil
	}
	cf, err := compose.Parse(data)
	if err != nil {
		return nil
	}
	var refs []string
	for _, svc := range cf.Services {
		if svc.Image != "" {
			refs = append(refs, svc.Image)
		}
	}
	return refs
}

// splitImageRef tách một ref ảnh thành repo + digest (nếu có "@") hoặc tag.
// "ghcr.io/o/r@sha256:x" → ("ghcr.io/o/r", "sha256:x", ""); "ghcr.io/o/r:v1"
// → ("ghcr.io/o/r", "", "v1"); "ghcr.io/o/r:v1@sha256:x" → digest thắng.
// Không có tag → "latest".
func splitImageRef(ref string) (repo, digest, tag string) {
	name := ref
	if i := strings.Index(name, "@"); i >= 0 {
		digest = name[i+1:]
		name = name[:i]
	}
	slash := strings.LastIndex(name, "/")
	if c := strings.LastIndex(name, ":"); c > slash {
		tag = name[c+1:]
		name = name[:c]
	}
	if digest == "" && tag == "" {
		tag = "latest"
	}
	return name, digest, tag
}

// familiarRepo đưa tên repo Docker Hub về dạng `docker images` in ra:
// "docker.io/library/redis" → "redis", "docker.io/pgvector/pgvector" →
// "pgvector/pgvector". Repo ở registry khác giữ nguyên.
func familiarRepo(repo string) string {
	for _, p := range []string{"docker.io/", "index.docker.io/", "registry-1.docker.io/"} {
		if strings.HasPrefix(repo, p) {
			return strings.TrimPrefix(strings.TrimPrefix(repo, p), "library/")
		}
	}
	return repo
}

// pruneOldImages dọn ảnh cũ, GIỮ ảnh của mọi compose trong keep (bản hiện tại +
// bản liền trước). An toàn:
//   - ảnh Gen-Harness: chỉ xét repo khớp ghImageRepoRe VÀ có mặt trong tập giữ
//     (cùng owner/tên);
//   - ảnh ngoài (caddy, redis… — từ v0.1.48 ghim digest, Renovate nâng digest
//     hằng tuần): chỉ xét repo mà tập giữ GHIM DIGEST, và chỉ dòng KHÔNG gắn tag
//     (kéo theo digest — đúng kiểu genh kéo). Dòng có tag (redis:7-alpine của
//     bản genh cũ kéo theo tag, redis:6 của dự án khác…) không bao giờ đụng;
//   - tập giữ không có ref gen-harness nào (compose dùng build: cục bộ/dev)
//     → không xoá gì, kể cả ảnh ngoài;
//   - QUYẾT ĐỊNH GIỮ/XOÁ THEO CHÍNH THAM CHIẾU của dòng (repo@digest hoặc
//     repo:tag có trong tập giữ), KHÔNG theo IMAGE ID. Mỗi bản phát hành đẩy
//     manifest mới nên digest đổi dù ảnh không đổi nội dung (vd gen-harness-db
//     qua v0.1.48→v0.1.50: 3 digest, CÙNG một ID); nếu giữ theo ID thì tham chiếu
//     digest CŨ nằm ngoài tập giữ cũng được "ké" và không bao giờ bị gỡ → danh sách
//     ảnh dài dần (cổng E2E v0.1.50 đỏ). `docker rmi repo@digest` chỉ GỠ THAM
//     CHIẾU đó; lớp ảnh vẫn còn vì còn tham chiếu khác của bản giữ (không mất dữ
//     liệu, container đang chạy không ảnh hưởng). Ngoại lệ an toàn: dòng giữ nhờ
//     TAG (bản genh cũ chưa ghim digest) không cho biết digest nào là của nó →
//     mọi dòng cùng ID TRONG CÙNG repo ấy cũng giữ như trước; chỉ khi tập giữ có
//     digest cho dòng/repo thì mới so theo digest;
//   - `docker rmi` mặc định KHÔNG -f; ảnh đang dùng (rmi lỗi — kể cả container đã
//     dừng của dự án khác) chỉ in một dòng rồi bỏ qua. NGOẠI LỆ duy nhất: tham
//     chiếu DIGEST mà image ID vẫn còn được ít nhất một dòng trong tập giữ tham
//     chiếu → `rmi -f` (xem chú thích ở vòng xoá);
//
// err chỉ khác nil khi không liệt kê được ảnh.
func pruneOldImages(ctx context.Context, runner dockercli.Runner, keep [][]byte, out io.Writer) (removed int, err error) {
	keepKeys := map[string]bool{}
	ghRepos := map[string]bool{}     // repo gen-harness-* của bản giữ
	pinnedRepos := map[string]bool{} // repo ngoài mà bản giữ ghim digest
	for _, data := range keep {
		for _, ref := range imageRefsFromCompose(data) {
			repo, digest, tag := splitImageRef(ref)
			repo = familiarRepo(repo)
			if ghImageRepoRe.MatchString(repo) {
				ghRepos[repo] = true
			} else if digest != "" {
				pinnedRepos[repo] = true
			}
			if digest != "" {
				keepKeys[repo+"@"+digest] = true
			} else {
				keepKeys[repo+":"+tag] = true
			}
		}
	}
	// Chỉ dọn khi bản giữ là bản cài thật (có ảnh gen-harness) — checkout dev
	// dùng build: cục bộ thì không đụng gì.
	if len(ghRepos) == 0 {
		return 0, nil
	}

	listed, err := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: []string{
		"images", "--digests", "--no-trunc", "--format", "{{.Repository}}\t{{.Tag}}\t{{.Digest}}\t{{.ID}}",
	}})
	if err != nil {
		return 0, fmt.Errorf("liệt kê ảnh Docker: %w", err)
	}

	type row struct {
		repo, tag, digest, id string
		gh                    bool
	}
	var rows []row
	for _, line := range strings.Split(string(listed), "\n") {
		f := strings.Split(strings.TrimSpace(line), "\t")
		if len(f) < 4 {
			continue
		}
		r := row{repo: familiarRepo(f[0]), tag: f[1], digest: f[2], id: f[3]}
		switch {
		case ghImageRepoRe.MatchString(r.repo):
			// Chỉ dọn trong ĐÚNG các repo (owner/tên) mà bản giữ dùng — không đụng ảnh
			// gen-harness-* của owner khác (bản cài thứ hai dùng chung Docker, bản fork/dev).
			if !ghRepos[r.repo] {
				continue
			}
			r.gh = true
		case pinnedRepos[r.repo]:
		default:
			continue
		}
		rows = append(rows, r)
	}

	// Dòng thuộc tập giữ theo CHÍNH tham chiếu của nó (digest hoặc tag).
	byOwnRef := func(r row) (byDigest, byTag bool) {
		byDigest = r.digest != "" && r.digest != "<none>" && keepKeys[r.repo+"@"+r.digest]
		byTag = r.tag != "" && r.tag != "<none>" && keepKeys[r.repo+":"+r.tag]
		return byDigest, byTag
	}
	// tagKeepIDs (khoá theo repo, KHÔNG dùng chung giữa các repo) chỉ để giữ các dòng
	// cùng ID với dòng giữ nhờ TAG: lúc đó không biết digest nào là của ảnh giữ.
	// Dòng giữ nhờ digest KHÔNG kéo theo dòng khác cùng ID — digest cũ phải bị gỡ.
	tagKeepIDs := map[string]bool{}
	for _, r := range rows {
		if _, byTag := byOwnRef(r); byTag {
			tagKeepIDs[r.repo+"\x00"+r.id] = true
		}
	}
	kept := func(r row) bool {
		byDigest, byTag := byOwnRef(r)
		return byDigest || byTag || tagKeepIDs[r.repo+"\x00"+r.id]
	}
	// keptIDs: image ID của MỌI dòng được giữ (khoá theo ID, chung mọi repo). Chỉ để
	// quyết định có dùng `rmi -f` hay không — KHÔNG dùng để giữ/xoá.
	keptIDs := map[string]bool{}
	for _, r := range rows {
		if kept(r) {
			keptIDs[r.id] = true
		}
	}

	tried := map[string]bool{}
	for _, r := range rows {
		if kept(r) {
			continue
		}
		if !r.gh && r.tag != "" && r.tag != "<none>" {
			continue // ảnh ngoài có tag: có thể của dự án khác — không đụng
		}
		var ref string
		digestRef := false
		switch {
		case r.digest != "" && r.digest != "<none>":
			ref = r.repo + "@" + r.digest
			digestRef = true
		case r.gh && r.tag != "" && r.tag != "<none>":
			ref = r.repo + ":" + r.tag
		default:
			continue
		}
		if tried[ref] {
			continue
		}
		tried[ref] = true
		args := []string{"rmi"}
		// Docker (daemon/images/image_delete.go) coi MỌI digest của cùng một repo là
		// MỘT tham chiếu: isSingleReference = true khi mọi tham chiếu của ảnh đều là
		// digest cùng repo và không có tag. Khi đó, nếu có container đang chạy ảnh này,
		// `rmi repo@digest` bị từ chối (conflict "container … is using its referenced
		// image") dù còn digest khác trỏ cùng ảnh — v0.1.51 đỏ vì thế. `-f` bỏ qua
		// kiểm đó, gỡ đúng tham chiếu rồi, vì repoRefs vẫn còn digest của bản giữ nên
		// CHỈ untag và return — không bao giờ xoá lớp ảnh. Nên `-f` an toàn tuyệt đối
		// khi và chỉ khi image ID của dòng còn được ít nhất một dòng trong tập giữ
		// tham chiếu (keptIDs). Ảnh KHÔNG còn tham chiếu giữ thì KHÔNG dùng -f: -f
		// còn xoá được ảnh của container ĐÃ DỪNG (có thể của dự án khác). Tham chiếu
		// TAG cũng không dùng -f: không bao giờ vướng isSingleReference, và -f ở đó
		// có thể kéo theo dọn digest cùng repo (kể cả digest giữ).
		if digestRef && keptIDs[r.id] {
			args = append(args, "-f")
		}
		args = append(args, ref)
		if _, rmErr := runner.Output(ctx, dockercli.Cmd{Name: "docker", Args: args}); rmErr != nil {
			_, _ = fmt.Fprintf(out, "     (không xoá được ảnh %s — có thể đang dùng; bỏ qua)\n", ref)
			continue
		}
		removed++
	}
	return removed, nil
}
