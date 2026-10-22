import type { RefObject } from 'react';
import { ApiError, type SetupState, type SetupStepStatus } from '@gen-harness/contracts';
import { MODEL_UNAVAILABLE_TEXT, detailToText } from '../lib/friendlyError';
import type { StepMeta } from './steps';

export interface StepProps {
  meta: StepMeta;
  description: string;
  status: SetupStepStatus;
  token: string;
  setToken: (t: string) => void;
  onBack?: () => void;
  /** Server accepted the step; wizard moves to state.current_step. */
  onSaved: (s: SetupState) => void;
  /** Move on without saving (step already done). */
  onNext: () => void;
  formRef: RefObject<HTMLFormElement>;
  /** Bước tuỳ chọn (10–11): `POST /setup/steps/{n}/skip`, cùng cơ chế `ComingSoonStep` dùng. */
  onSkip?: () => void;
  skipping?: boolean;
  skipError?: string | null;
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 0) return 'Không kết nối được máy chủ. Kiểm tra dịch vụ api rồi thử lại.';
    // Phase 2: a step whose precondition fails says what is missing, in Vietnamese — show it as is.
    if (e.code === 'STEP_INCOMPLETE' || e.code === 'STEP_ORDER') return e.message;
    if (e.status === 409) return 'Thiết lập đã hoàn tất hoặc bước này không còn sửa được ở đây.';
    if (e.status === 422) return 'Kiểm tra lại các trường được đánh dấu.';
    if (e.code === 'MODEL_UNAVAILABLE') return MODEL_UNAVAILABLE_TEXT;
    return detailToText(e.message) || 'Có lỗi không xác định.';
  }
  return (e instanceof Error ? detailToText(e.message) : detailToText(e)) || 'Có lỗi không xác định.';
}

export const TOKEN_INVALID = 'Mã thiết lập không hợp lệ hoặc đã hết hạn. Mở lại đường dẫn từ trình cài.';
