# Gói pipeline-ci (v0.1.48) — bảng "đã kiểm"

Ngày đo: 2026-10-03 (đo lại lúc thi công, trùng số lúc lập kế hoạch). Mọi SHA lấy từ
`git ls-remote https://github.com/<repo>.git refs/tags/<tag> refs/tags/<tag>^{}` (tag có chú thích thì lấy dòng `^{}`),
`runs.using` lấy từ `https://raw.githubusercontent.com/<repo>/<tag>/action.yml`.

Về "đã đọc release notes": trang Releases của GitHub không tải được qua proxy của môi trường thi công, nên thay bằng
đối chiếu trực tiếp `action.yml` của đúng tag (danh sách input/output) với cách gọi hiện có trong 4 workflow. Mọi input
đang dùng đều còn nguyên (cột "input đã đối chiếu"); không có thay đổi phá vỡ nào buộc sửa cách gọi.

## Action (F-71)

| repo | tag | SHA | runs.using | input/output đã đối chiếu |
|---|---|---|---|---|
| actions/checkout | v5.1.0 | fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 | node24 | (không input) |
| actions/setup-node | v5.0.0 | a0853c24544627f65ddf259abe73b1d18a591444 | node24 | node-version, cache (npm) |
| actions/setup-go | v6.5.0 | 924ae3a1cded613372ab5595356fb5720e22ba16 | node24 | go-version |
| actions/upload-artifact | v6.0.0 | b7c566a772e6b6bfb58ed0dc250532a479d7789f | node24 (v5 vẫn node20) | name, path, retention-days, if-no-files-found |
| actions/download-artifact | v7.0.0 | 37930b1c2abaa49bbe596cd826c3c89aef350131 | node24 (v5, v6 vẫn node20) | name, path, pattern, merge-multiple |
| actions/cache | v5.1.0 | caa296126883cff596d87d8935842f9db880ef25 | node24 | path, key |
| astral-sh/setup-uv | v7.6.0 | 37802adc94f370d6bfd71619e3f0bf239e1f3b78 | node24 | python-version, version |
| docker/build-push-action | v7.4.0 | c3c9e263c25d99ce0380d002d59b67737d91b0dc | node24 | context, file, platforms, push, build-args, tags, cache-from, cache-to; output `digest` còn |
| docker/login-action | v4.6.0 | dbcb813823bdd20940b903addbd779551569679f | node24 | registry, username, password |
| docker/setup-buildx-action | v4.4.1 | f87e5991a6d7451dcb8d9637bfbc97413f497069 | node24 | (không input) |
| docker/setup-qemu-action | v4.4.0 | 99012661954931238ded8c8b007157a8430204e1 | node24 | (không input) |
| softprops/action-gh-release | v3.0.3 | efb35369e0ad2afab669f228072c1b0d510eae64 (dòng `^{}`; tag có chú thích trỏ e598afbe…) | node24 | tag_name, name, files, prerelease, make_latest, target_commitish, generate_release_notes |
| sigstore/cosign-installer | v3.10.1 | 7e8b541eb2e61bf99390e1afd4be13a184e9ebc5 | composite (không dùng Node) | giữ major v3 để `.sig`/`.pem` của cosign không đổi định dạng; chỉ ghim SHA |

## Công cụ quét và kiểm (F-13, F-71)

| công cụ | giá trị | nguồn / lệnh | ghi chú |
|---|---|---|---|
| uv | 0.12.23 | `https://pypi.org/pypi/uv/json` (có trong releases) | setup-uv `version: "0.12.23"` — cùng số với gói anh-tai-lap |
| pip-audit | 2.10.1 | `https://pypi.org/pypi/pip-audit/json` | `uvx --from pip-audit==2.10.1`; requires-python >=3.10 |
| govulncheck (golang.org/x/vuln) | v1.8.0 | `https://proxy.golang.org/golang.org/x/vuln/@latest` | go.mod của bản này đòi `go 1.26.0` ⇒ bước đặt `GOTOOLCHAIN=auto` để Go 1.24 (setup-go) tự tải toolchain phù hợp; không đổi Go dùng build genh |
| renovate | 44.132.5 | `https://registry.npmjs.org/renovate/latest` | engines `node ^24.11.0` ⇒ setup-node 24; bin `renovate-config-validator` có. Sửa sau review: chạy ở job riêng `renovate-config` (chỉ khi renovate.json đổi, `--no-global`, npm thử lại 5 lần, không chạy trên đường phát hành) |
| actionlint | v1.7.12 | `https://proxy.golang.org/github.com/rhysd/actionlint/@latest` | đã chạy trên 4 workflow: 0 lỗi |
| redis:7-alpine (index đa kiến trúc) | sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499 | registry-1.docker.io, header `docker-content-digest` của `manifests/7-alpine` | ci.yml services redis (api, browser). PHẢI bằng digest redis trong deploy/compose.yaml do gói anh-tai-lap ghim — nếu gói đó đo ra digest khác (tag trôi) thì sửa ci.yml cho trùng |

## Việc đã làm (map theo hướng dẫn)

- A: 4 workflow đổi mọi `uses:` sang `owner/repo@<sha> # vX.Y.Z` (trừ `./.github/workflows/…`).
- B: `permissions: contents: read` cấp workflow ở ci.yml và installer-matrix.yml; bỏ `packages: write` của job promote.
- C: promote không còn đăng nhập GHCR / setup-buildx / gắn `:latest`; build-images thêm tag `:sha-<commit>`.
- D: build-images `build1` (continue-on-error) → chờ 30 giây → `build2`; cache gha theo dịch vụ; bước lưu digest báo lỗi nếu rỗng. ci.yml job images thử lại build 1 lần; `npm ci` có fetch-retries.
- E: ci.yml api/browser dùng `uv lock --check` + `uv sync --frozen --extra dev`; redis ghim digest; Caddyfile lấy ảnh proxy từ compose.
- F: `.github/scripts/check_image_lock.py` + bước "Tái lập" trong job images.
- G: gọi `check_embedded_sync.py`, `check_workflow_hygiene.py`, `renovate-config-validator`; 3 bước quét báo cáo (pip-audit api/browser, npm audit web/bridge, govulncheck ô ubuntu-24.04); e2e-install.yml thêm `apps/browser/**`, `apps/bridge/**` vào paths.
- H: `check_workflow_hygiene.py` (+ test); `check_release_gate.py` thêm bất biến build1/build2/cache gha.

## Chỉ đánh giá được sau khi tích hợp 3 gói

Trên nhánh riêng của gói này chưa có `apps/*/uv.lock`, `check_embedded_sync.py`, `scan_summary.py`, `renovate.json`,
Dockerfile dùng venv `/opt/venv` — nên các bước CI sau chỉ chạy đúng sau tích hợp: `uv lock --check`/`uv sync --frozen`,
"Bản nhúng genh khớp deploy/", "renovate.json hợp lệ", 3 bước quét, "Tái lập: gói trong ảnh khớp uv.lock".
Ghi chú vận hành: ảnh proxy/redis đổi tham chiếu (thêm digest) ⇒ lần `genh update` kế tiếp compose tạo lại container
proxy/redis (dữ liệu redis giữ ở volume `redis_data`); e2e-upgrade phải xanh để chứng minh.
