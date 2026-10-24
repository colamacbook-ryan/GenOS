# STEP — agent đóng vai model trong pipeline G-KB

`step.py` chạy pipeline thật của GenOS (replay → kho thô → lượt theo luồng → trích xuất → kiểm → ghi facts). Chỗ model do **bạn** làm: mỗi lần pipeline cần
gọi model, nó ghi `prompt.json` rồi dừng chờ bạn.

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
2. **Trả lời đúng định dạng prompt yêu cầu**: một JSON `brain-out/1` (một object, không kèm lời dẫn; khối ```json cũng được bóc). Ghi nguyên văn vào `answer.json`.
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
- Dọn: `dropdb gh_step_<tên>` (DB lần chạy); khuôn `gh_step_tpl_*` giữ làm cache.
