import { useEffect, useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import type { RefinerySchedule } from '@gen-harness/contracts';
import { Button, Dialog, TextField } from '@gen-harness/ui';
import { api } from '../../lib/api';
import { qk2 } from '../../lib/dataQueries';
import { queryClient } from '../../lib/queryClient';
import { toast } from '../../lib/toast';
import { errorText } from '../../lib/errorText';
import { InlineError } from '../common';
import { validateSchedule } from './dataModel';

interface Draft {
  minutes: string;
  threshold: string;
  batch: string;
  confidence: string;
}

const num = (s: string) => (s.trim() === '' ? Number.NaN : Number(s.replace(',', '.')));

/** Sửa lịch kích hoạt sàng lọc — PUT /refinery/schedule (data.manage). */
export function ScheduleDialog({ open, onClose, schedule }: { open: boolean; onClose: () => void; schedule: RefinerySchedule }) {
  const [d, setD] = useState<Draft>(() => toDraft(schedule));
  useEffect(() => {
    if (open) setD(toDraft(schedule));
  }, [open, schedule]);

  const values = {
    interval_seconds: Math.round(num(d.minutes) * 60),
    count_threshold: num(d.threshold),
    batch_size: num(d.batch),
    min_confidence: num(d.confidence),
  };
  const errors = validateSchedule(values);
  const valid = Object.keys(errors).length === 0;

  const save = useMutation({
    mutationFn: () => api.refinery.setSchedule(values),
    onSuccess: (s) => {
      queryClient.setQueryData(qk2.schedule, s);
      void queryClient.invalidateQueries({ queryKey: qk2.pipeline });
      toast('Đã lưu lịch sàng lọc');
      onClose();
    },
  });

  return (
    <Dialog
      open={open}
      onClose={onClose}
      width={460}
      title="Kích hoạt sàng lọc"
      kicker="Theo chu kỳ hoặc theo số lượng — cái nào đến trước"
      actions={
        <>
          <Button variant="secondary" onClick={onClose}>
            Huỷ
          </Button>
          <Button variant="primary" icon="ph ph-check" disabled={!valid} loading={save.isPending} onClick={() => save.mutate()}>
            Lưu lịch
          </Button>
        </>
      }
    >
      <form
        className="dlg-fields"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid) save.mutate();
        }}
      >
        <div className="dlg-grid2">
          <TextField
            label="Chu kỳ thời gian (phút)"
            inputMode="decimal"
            value={d.minutes}
            onChange={(e) => setD({ ...d, minutes: e.target.value })}
            error={errors.interval_seconds ?? null}
            data-autofocus
          />
          <TextField
            label="Ngưỡng số lượng (bản ghi)"
            inputMode="numeric"
            value={d.threshold}
            onChange={(e) => setD({ ...d, threshold: e.target.value })}
            error={errors.count_threshold ?? null}
          />
          <TextField
            label="Số bản ghi mỗi lượt"
            inputMode="numeric"
            value={d.batch}
            onChange={(e) => setD({ ...d, batch: e.target.value })}
            error={errors.batch_size ?? null}
          />
          <TextField
            label="Ngưỡng tin cậy vào kho sạch (0–1)"
            inputMode="decimal"
            value={d.confidence}
            onChange={(e) => setD({ ...d, confidence: e.target.value })}
            error={errors.min_confidence ?? null}
          />
        </div>
        <p className="muted-note">Lượt sàng lọc chạy khi hết chu kỳ hoặc khi số bản ghi chờ vượt ngưỡng — cái nào đến trước.</p>
        <InlineError>{save.isError ? errorText(save.error) : null}</InlineError>
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

function toDraft(s: RefinerySchedule): Draft {
  return {
    minutes: String(Math.round((s.interval_seconds / 60) * 10) / 10),
    threshold: String(s.count_threshold),
    batch: String(s.batch_size),
    confidence: String(s.min_confidence),
  };
}
