import { useEffect, useState } from 'react';
import type { GenModelEffort, GenModelTier } from '@gen-harness/contracts';
import { useIsOwner } from './coachQueries';
import { useGenModelOptions } from './genClient';
import { useGenStore } from './genStore';
import { EFFORT_LABEL, TIER_LABEL, effortsOf, tierAvailable, unavailableReason, visibleTiers, withEffort, withTier } from './modelChoice';

/**
 * v0.1.55 (G3) — dưới ô nhập của khung Gen: "Tự động (chuẩn) · Nhanh · Kỹ hơn" (Cân bằng mặc định ẩn, chỉ hiện khi đang chọn) và
 * "Mức suy nghĩ: Thấp · Vừa · Cao" CHỈ khi tầng đang chọn hỗ trợ. Tầng không dùng được thì nút mờ (`aria-disabled`, KHÔNG
 * `disabled`) kèm tooltip, và bấm vào nút mờ hiện CÂU GIẢI THÍCH ngay dưới hàng nút — màn cảm ứng (Mặt tiền Owner dùng điện
 * thoại là chính) không xem được `title` (bài học ApiScreen v0.1.38 F-22). Lựa chọn nhớ theo hội thoại (genStore.modelChoice); hội thoại mới = Tự động. Không dùng chữ token.
 * Dùng lại các lớp `.gen-rate*` (nút tròn, tự xuống dòng ở màn hẹp 390px) nên không tràn ngang.
 */
export function ModelPicker() {
  const choice = useGenStore((s) => s.modelChoice);
  const setChoice = useGenStore((s) => s.setModelChoice);
  const options = useGenModelOptions();
  const isOwner = useIsOwner();
  const efforts = effortsOf(options, choice.tier);
  // Tầng vừa bấm mà chưa dùng được: câu giải thích hiện dưới hàng nút (biến mất khi chọn tầng khác hoặc tầng đó dùng được).
  const [hintTier, setHintTier] = useState<GenModelTier | null>(null);
  const hint = hintTier && !tierAvailable(options, hintTier) ? unavailableReason(hintTier, isOwner) : null;

  // Lựa chọn đã nhớ mà nay máy chủ báo không dùng được nữa (đổi nguồn model, đăng nhập tài khoản khác) ⇒ về Tự động.
  useEffect(() => {
    if (choice.tier !== 'auto' && !tierAvailable(options, choice.tier)) setChoice({ tier: 'auto' });
  }, [options, choice.tier, setChoice]);

  const pickTier = (tier: GenModelTier) => {
    if (!tierAvailable(options, tier)) {
      setHintTier(tier);
      return;
    }
    setHintTier(null);
    setChoice(withTier(choice, tier, options));
  };
  const pickEffort = (e: GenModelEffort) => setChoice(withEffort(choice, choice.effort === e ? null : e));

  return (
    <div className="gen-model" style={{ padding: '0 12px max(10px, env(safe-area-inset-bottom))', display: 'flex', flexDirection: 'column', gap: 6 }}>
      <div className="gen-rate" role="group" aria-label="Chế độ trả lời của Gen" style={{ alignItems: 'center' }}>
        {visibleTiers(choice).map((t) => {
          const ok = tierAvailable(options, t);
          const reason = ok ? undefined : unavailableReason(t, isOwner);
          return (
            <span key={t} title={reason} style={{ display: 'inline-flex' }}>
              <button
                type="button"
                className="gen-rate__btn"
                aria-pressed={choice.tier === t}
                aria-disabled={ok ? undefined : true}
                aria-describedby={ok || hintTier !== t ? undefined : 'gen-model-reason'}
                title={reason}
                style={ok ? undefined : { opacity: 0.5, cursor: 'not-allowed' }}
                onClick={() => pickTier(t)}
              >
                {TIER_LABEL[t]}
              </button>
            </span>
          );
        })}
      </div>
      {hint ? (
        <p id="gen-model-reason" role="status" data-testid="gen-model-reason" style={{ margin: 0, fontSize: 12, lineHeight: 1.4, color: 'var(--color-neutral-400)' }}>
          {hint}
        </p>
      ) : null}
      {efforts.length > 0 ? (
        <div className="gen-rate" role="group" aria-label="Mức suy nghĩ" style={{ alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--color-neutral-400)' }}>Mức suy nghĩ:</span>
          {efforts.map((e) => (
            <button key={e} type="button" className="gen-rate__btn" aria-pressed={choice.effort === e} onClick={() => pickEffort(e)}>
              {EFFORT_LABEL[e]}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}
