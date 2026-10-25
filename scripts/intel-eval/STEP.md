# STEP — agent đóng vai model trong pipeline G-KB

`step.py` chạy pipeline thật của GenOS (replay → kho thô → lượt theo luồng → trích xuất → kiểm → ghi facts). Chỗ model do **bạn** làm: mỗi lần pipeline cần
gọi model, nó ghi `prompt.json` rồi dừng chờ bạn.
Từ Đợt 2 có thêm job đồng hồ của Tình báo chạy theo giờ dữ liệu; job lừa đảo có thể gọi model kiểm chéo (d2) — prompt `kind = "d2"`, khuôn trả lời khác
`brain-out/1` (đọc trong chính prompt).

## Việc của bạn (lặp tới `DONE`)
```
PY=apps/api/.venv/bin/python        # Python của venv API
$PY scripts/intel-eval/step.py next   --out <run_dir>                      # in đường dẫn prompt.json đang chờ, hoặc DONE
# đọc prompt.json → viết câu trả lời vào cùng thư mục: answer.json
$PY scripts/intel-eval/step.py submit <run_dir>/turn_NNN/answer.json       # in kết quả lượt + đường dẫn lượt kế
```

1. **Chỉ đọc `prompt.json`.** `system` là kỹ năng tình báo (chỉ dẫn + khuôn đầu ra), `messages` là dữ liệu của lượt (Hồ sơ, chính sách, trí nhớ, tin ngữ cảnh,
   tin mới). Không đọc tệp nào khác trong `<run_dir>`, không đọc bộ dữ liệu gốc, đáp án, mã nguồn pipeline, lượt trước… — model thật cũng chỉ có đúng prompt.
   Mọi thứ cần biết nằm trong prompt, kể cả khuôn JSON.
2. **Trả lời đúng định dạng prompt yêu cầu** (`kind` extract/retry ⇒ `brain-out/1`; `d2` ⇒ khuôn kiểm chéo ghi trong prompt): một JSON `brain-out/1` (một object, không kèm lời dẫn; khối ```json cũng được bóc). Ghi nguyên văn vào `answer.json`.
   Trích dẫn (`quotes[].text`) phải chép từng chữ từ tin; mã tin (`m_…`) chỉ dùng mã có trong prompt.
3. Chạy `submit`. Có 3 kết quả:
   - in `tiếp: …/prompt.json` → lượt kế (hoặc `DONE` → xong);
   - in `gọi lại …` → câu trả lời sai hợp đồng/JSON hỏng; pipeline thật gọi lại **MỘT lần** (thư mục `turn_NNN_c2`) kèm lỗi trong `messages`; hãy sửa theo chính `prompt.json` mới. Hỏng lần hai ⇒ lượt tính là lỗi hợp đồng (G-KB yêu cầu 0), tin quay lại hàng chờ;
   - lỗi `step: …` → đọc thông báo (vd chưa ghi `answer.json`, nhầm lượt).
4. Mỗi lượt là một lần gọi **mới, độc lập**: đừng mang trí nhớ riêng từ lượt trước sang lượt sau (trí nhớ hợp lệ duy nhất là khối "Sự thật đã biết"/"Lịch sử" mà pipeline đưa vào prompt).
   Không sửa/ghi tệp ngoài `answer.json`. Không chạy lại `init` giữa chừng (xoá lần chạy).

## Việc của người điều hành
```
$PY scripts/intel-eval/step.py init --dataset research/eval-tinh-bao/v2/kbN --out <run_dir ngoài repo> [--contexts ctx.json]
$PY scripts/intel-eval/step.py stats --out <run_dir>                       # số lượt, token ước tính
$PY scripts/intel-eval/step.py export --out <run_dir>/signals.json --run <run_dir>
$PY scripts/intel-eval/gkb_score.py --data .../kbN --key <đáp án ngoài repo> --runs <run_dir>    # chấm (run_dir có outputs.jsonl, idmap.json… đúng khuôn replay)
```
- `init` dựng DB `gh_step_*` (nhân từ khuôn đã migrate, cần Postgres) + Redis DB riêng (`--redis-url`, mặc định DB 10, bị xoá sạch), rồi mở tiến trình nền;
  `--auto-stub` tự trả brain-out rỗng hợp lệ (đếm lượt/kích thước prompt, kiểm hạ tầng; **không dùng để chấm**).
- `--contexts ctx.json` = `{"G1": "giao_thuong", …}` (nhãn bối cảnh Sếp đã xác nhận); không có và `context.md` không có bảng nhóm ⇒ bối cảnh **chưa xác nhận**. Hồ sơ Sếp lấy từ mục "Hồ sơ Sếp" của `context.md` (kb1 không có mục này ⇒ Hồ sơ rỗng).
- "Bây giờ" của mỗi lượt = giờ của DỮ LIỆU (`clockplan.data_time`: giờ gốc tin cuối đã đến), không phải đồng hồ nén — mốc "quá hạn/hôm nay/dưới 24 giờ",
  lùi nhịp và cửa sổ truy hồi 90 ngày chạy theo ngày thật của tin.
- Sau lượt kế hoạch cuối, tin còn chờ (lượt ghi lỗi/lùi nhịp, tin đuôi) được **xả** như scheduler thật: bản ghi `plan_turn = null`, `reason = "drain:…"`.
- Lượt model trả đúng nhưng pipeline ghi lỗi (cả lượt hoặc từng đơn vị) ⇒ `write_error` trong `state.last`/`outputs.jsonl`; `submit` in cảnh báo ra stderr,
  `export` đếm "lượt lỗi ghi" + số tin còn chờ (`write_errors` trong signals.json, gkb_score cũng đếm). Không còn `error: null` cho lượt mất dữ liệu.
- Một lượt kế hoạch của replay có thể thành nhiều lượt pipeline (an toàn/cổng chạy ngay, rồi lượt thường): `turn_NNN` đếm theo **lượt pipeline**; `outputs.jsonl` có thêm `plan_turn`.
- Tiến trình nền chết (khởi động lại máy…) ⇒ `init --force` làm lại từ đầu (không có resume: id tin/sự thật là uuid sinh mới mỗi lần).
- Token trong `stats`/`tokens.jsonl` là **ước tính** (ký tự / 3, cùng hệ số `retrieve.py`; sai số ±25%), không phải số đếm của tokenizer thật.
- **Đợt 2 — job đồng hồ** (`ClockJobs`): job cron production của Tình báo (danh sách `CLOCK_JOBS`, lịch lấy từ `gh.biz.jobs()` — không chép tay) chạy
  với `gh.refinery.clock` = giờ dữ liệu: trước mỗi tin (tới giờ gửi gốc), trước mỗi lượt, mỗi vòng xả, một lượt ở `now` cuối, rồi chạy tiếp tới bản
  tin sáng kế (07:30). Mỗi mốc = một phút cron; job dày (mỗi phút/5 phút) gộp về mốc đầu + cuối khoảng, job giờ cố định chạy đủ từng lần; thêm mốc
  ở `next_eval_at` của tín hiệu mở và ít nhất mỗi 60 phút khi im lặng (`GH_STEP_TICK_MAX_MIN`). Mỗi mốc gọi `ensure_scanned` (quét luật (a) của scheduler) trước.
  Sau mỗi lượt pipeline: hook `market_signal_capture` (cầu → `catalog_match`). Khác production có chủ ý: `intel_fraud` bỏ trần 240 s/30 s và (d1)/(d2)
  bỏ trần 60–90 s (agent chậm hơn model); `mem_heat_weekly` truyền `now=clock.now()`. Không chạy: giao tin (`intel_delivery`), watchdog/backup, duty,
  `mem_consolidate`. Giới hạn: `received_at`/`created_at`/`now()` SQL vẫn là giờ thật (cửa sổ 30 phút theo `received_at` của lừa đảo/raw_tail ⇒ "mọi
  tin đã vào"; cài mới: chưa cổng nào qua M1, tuổi cài đặt = 0 ⇒ cổng không bypass ra 🔴 bóng `digest_top`). `--intel-settings '{"ramp_gates": "push"}'` để đo chế độ đẩy thật.
- **signals.json `gkb-step-signals/2`** (giữ nguyên mọi khoá của /1): `alerts` (trạng thái cuối lúc bản tin sáng: kind, level, gate/candidate, delivery,
  lane, status, case_key, reasons, pushed_at, `raw_ids` = mã tin dữ liệu, `first_seen_at` giờ dữ liệu), `alerts_end` (lúc `now` cuối replay),
  `pushed`/`shadow_pushed` (🔴 đã đẩy / 🔴 bóng, giờ dữ liệu), `alert_events` (mới/đổi mức-ngăn-trạng thái), `digest` (`digest.build` owner, only_new=false),
  `clock` (số mốc, số lần mỗi job chạy, lỗi job, giây). Tệp gốc trong run_dir: `alerts_log.jsonl`, `alerts_end.json`, `alerts_final.json`, `digest.json`.
- Dọn: `dropdb gh_step_<tên>` (DB lần chạy); khuôn `gh_step_tpl_*` giữ làm cache.

## Đợt 2 sau KQ-dot2 — sửa harness (H1–H5)
- **H1 · trần độ trễ theo giờ dữ liệu** (`init --max-lag-min N`, mặc định **30**, `0` = khuôn cũ). Chế độ nén làm lô của luồng thưa trải 5–12 ngày và `data_time`
  lấy giờ tin cuối trên MỌI luồng ⇒ alert trễ trung vị 32–44 giờ ⇒ cổng có cửa sổ 24–48 giờ bị `window_expired` giả. Nay mỗi luồng có thêm đồng hồ hẹn ở
  giờ dữ liệu = tin chờ cũ nhất + trần (lý do lượt `lag`) và worker bận tính theo giờ dữ liệu (`clockplan.PlanConfig.max_lag_s`, `lag_deadline`, `busy_until`).
  Độ trễ tin → lượt ≤ trần + một khoảng nén (vài giây). Đổi lại **số lượt tăng nhiều** (đo bằng `--auto-stub`, số lượt pipeline):

  | bộ | lượt kế hoạch cũ → 30 phút | lượt pipeline cũ → 30 phút | trung vị/p90 độ trễ tin→lượt (phút) cũ → mới |
  |---|---|---|---|
  | kb2 | 15 → 223 | 17 → 218 | 3840 / 12970 → 7 / 27 |
  | kb3 | 11 → 189 | 12 → 180 | 4568 / 12976 → 9 / 28 |

  Lượt kế hoạch theo trần khác (kb2 / kb3): 1 giờ 187 / 157 · 2 giờ 160 / 128 · 6 giờ 110 / 85 — chọn `--max-lag-min` theo ngân sách số lượt (mỗi lượt nhỏ 1–3 tin).
  Cổng phân phối L6 ("trung vị ≥ 10 tin/lượt cho nhóm sôi nổi") không áp dụng khi có trần (lượt nhỏ là hệ quả của trần): `distribution_check.ok = false` là bình thường ở step.
  `replay.py` CLI và L6 vẫn dùng `PlanConfig()` mặc định (không trần) — phân phối lượt không đổi.
  **Chỉ số lag**: `signals.json` → `clock.lag.alerts` (giờ dữ liệu alert xuất hiện lần đầu − giờ tin mới nhất mà alert dẫn: `n`, `median_min`, `p90_min`, `max_min`,
  `over_24h`, `over_48h`) và `clock.lag.plan` (tin → lượt phân tích, từ `summary.json.lag`).
- **H2 · danh tính + Hồ sơ từ `context.md`** (tương đương Sếp khai lúc cài; KHÔNG dùng đáp án; tắt bằng `init --no-identity`). Ghi lại ở `<run_dir>/identity.json`:
  - kênh quản trị Sếp: phiên Zalo `account_kind = 'bot'` + `owner_channel.pairing.start_pairing/try_pair` (hàm production) với ID Sếp ở dòng "Chủ: …"/"Sếp là …"
    (`u_…`, ID phụ `fb_…`); tin đầu của Sếp trong nhóm ⇒ `ingest._owner_triggers` đặt `core.persons.attrs.is_owner` (đường production, theo ID). Không nối thêm sang
    `mem.entities.is_owner` — đúng như production (nghi vấn P3 vẫn hiện nguyên);
  - nhân viên/khách: bảng có cột ID dưới mục "Nhân sự" / "Khách…"/"Đại lý…" ⇒ `PATCH /profile/{id} {person_type}` ngay khi người đó có định danh (hành động "Đánh dấu"
    của Sếp, khoá `person_type_locked`) — nguồn của `hard_owner_side`/`hard_customer`. Không ghi fact `staff_of`/`customer_of` (không có đường Sếp-khai ghi fact trực tiếp);
  - `core.groups.member_count`: chỉ nhóm mà context.md nêu rõ số ("~5.000 thành viên", tên trong ngoặc kép hoặc hàng bảng mã nhóm). kb2: G4 = 5000; kb3: không nhóm nào (NULL).
- **H3 · media**: `write_media` nay quét luật (a) trên chữ media (`prefilter_port.scan_text`, như `Deps.scan`) và gọi `jobs._escalate_fast` khi trúng (kb2: 11 ảnh + 4 thoại lên đường nhanh).
- **H4 · hai nhánh có / không bối cảnh** (tách (D) khởi động lạnh khỏi lỗi khác): cùng bộ dữ liệu, hai thư mục chạy riêng:
  ```
  # không bối cảnh (chưa xác nhận):
  $PY scripts/intel-eval/step.py init --dataset .../kb2 --out <run>/kb2-noctx --redis-url redis://localhost:6379/3
  # có bối cảnh Sếp đã xác nhận (tệp ngoài repo, vd {"G1":"kinh_doanh","G2":"kinh_doanh","G3":"nhan_vien","G4":"giao_thuong"}):
  $PY scripts/intel-eval/step.py init --dataset .../kb2 --out <run>/kb2-ctx --contexts <đường dẫn>/kb2-ctx.json --redis-url redis://localhost:6379/3
  ```
  Mỗi nhánh một DB `gh_step_*` riêng (tên theo thư mục chạy) nhưng dùng chung Redis DB ⇒ chạy lần lượt hoặc cho mỗi nhánh `--redis-url` khác DB (`/3`, `/4`). Tệp ctx kb2/kb3 để ngoài repo.
- **H5 · bot không bao giờ gửi tin**: harness không chạy giao tin (`intel_delivery`) và không có tin trả lời của bot ⇒ "câu hỏi chưa trả lời" chỉ đo được qua tin `inbound`
  của người phía Sếp (P4 trong KQ-dot2); số liệu 🔴 là mức/ngăn quyết định, không phải tin đã gửi. Ghi rõ khi báo cáo.
