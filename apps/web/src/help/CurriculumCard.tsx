import { useId, useState } from 'react';
import type { CurriculumLesson } from '@gen-harness/contracts';
import { Button, Card } from '@gen-harness/ui';
import {
  COACH_CURRICULUM_TITLE,
  canRelearn,
  curriculumDone,
  lessonItemKey,
  lessonStatusText,
  tryMe,
  type CoachStep,
} from '../gen/coachModel';
import { useCoachCurriculum, useCoachItemAction, useIsOwner } from '../gen/coachQueries';
import { executeUiAction } from '../gen/director';
import { errorDetail, errorText } from '../lib/errorText';
import { navigateTo } from '../lib/navigation';
import { useMe } from '../lib/queries';
import { CardError, InlineError, SkeletonLines } from '../screens/common';

/**
 * v0.1.54 — Trợ giúp › "Lộ trình học cùng Gen": đủ 19 bài của Gen hướng dẫn kèm trạng thái (Chưa học · Đã gặp · Đã hiểu ·
 * Đang hoãn · Đã làm). Bấm tên bài để xem nội dung; "Làm thử" chỉ đường bằng director; "Học lại" đặt bài về chưa học
 * (`POST /gen/coach/items/lesson:<id>` restore). CHỈ Owner thấy thẻ (vai trò khác không vẽ, không gọi API).
 */
export function CurriculumCard() {
  if (!useIsOwner()) return null;
  return <CurriculumPanel />;
}

function CurriculumPanel() {
  const q = useCoachCurriculum();
  const lessons = Array.isArray(q.data?.lessons) ? q.data.lessons : [];
  const total = q.data?.total ?? lessons.length;
  return (
    <Card
      title={COACH_CURRICULUM_TITLE}
      kicker={q.data ? `${curriculumDone(lessons)}/${total} bài đã xong — bài nào cũng học lại được` : 'Các bài Gen dạy Sếp dùng hệ thống'}
      data-gen-target="help.curriculum"
      data-testid="help-curriculum"
    >
      {q.isPending ? (
        <SkeletonLines rows={4} />
      ) : q.isError ? (
        <CardError error={q.error} onRetry={() => void q.refetch()} retrying={q.isFetching} />
      ) : lessons.length === 0 ? (
        <p className="help-text">Chưa có bài học nào.</p>
      ) : (
        <ul className="curr">
          {lessons.map((l) => (
            <LessonRow key={l.id} lesson={l} total={total} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function LessonRow({ lesson, total }: { lesson: CurriculumLesson; total: number }) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const genOn = !!useMe().data?.features?.gen;
  const restore = useCoachItemAction();

  // Gen tắt ⇒ không có lớp làm sáng: chỉ mở đúng màn.
  const run = (steps: CoachStep[]) => {
    for (const s of steps) {
      if (s.type === 'go') navigateTo(s.to);
      else if (genOn || s.type !== 'highlight') void executeUiAction(s);
    }
  };

  return (
    <li className="curr__item" data-testid="curriculum-lesson" data-status={lesson.status}>
      <div className="curr__row">
        <span className="curr__k mono" aria-hidden>
          {lesson.k}/{total}
        </span>
        <button type="button" className="curr__title" aria-expanded={open} aria-controls={bodyId} onClick={() => setOpen((v) => !v)}>
          {lesson.title}
        </button>
        <span className="curr__status" data-status={lesson.status}>
          {lessonStatusText(lesson.status)}
        </span>
      </div>
      {open ? (
        <div className="curr__body" id={bodyId}>
          <p className="curr__text">{lesson.body}</p>
          {restore.isError ? <InlineError detail={errorDetail(restore.error)}>{errorText(restore.error)}</InlineError> : null}
          <div className="curr__actions">
            {lesson.try?.target ? (
              <Button variant="primary" size="sm" icon="ph ph-play" onClick={() => run(tryMe(lesson.try))}>
                Làm thử
              </Button>
            ) : null}
            {canRelearn(lesson.status) ? (
              <Button
                variant="secondary"
                size="sm"
                icon="ph ph-arrow-counter-clockwise"
                loading={restore.isPending}
                onClick={() => {
                  restore.reset();
                  restore.mutate({ itemKey: lessonItemKey(lesson), body: { action: 'restore' } });
                }}
              >
                Học lại
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </li>
  );
}
