-- Gen-Harness · v0.1.24 — Đợt A4 (Gen v2): nhắc việc đến giờ → thông báo chuông (core.notifications, 0017).
-- biz.tasks.remind_at đã có từ baseline nhưng chưa ai xử lý. `reminded_at` đánh dấu đã nhắc (mỗi mốc nhắc một lần);
-- đổi remind_at qua PATCH /tasks/{id} đặt lại NULL để nhắc lại theo mốc mới. Job: gh.biz.queue.jobs.task_reminder_scan.
ALTER TABLE biz.tasks ADD COLUMN IF NOT EXISTS reminded_at timestamptz;
CREATE INDEX IF NOT EXISTS tasks_due_reminder_idx ON biz.tasks (remind_at)
  WHERE remind_at IS NOT NULL AND reminded_at IS NULL;
