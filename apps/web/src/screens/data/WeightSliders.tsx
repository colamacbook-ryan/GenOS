import type { Weight } from '@gen-harness/contracts';

/** Design `weights` rows: label + value, 5px track with fill and knob; a transparent range input drives it. */
export function WeightSliders({
  weights,
  disabled,
  onChange,
  idPrefix = 'w',
}: {
  weights: Weight[];
  disabled?: boolean;
  onChange: (index: number, value: number) => void;
  idPrefix?: string;
}) {
  return (
    <>
      {weights.map((w, i) => (
        <div className="weight" key={w.dimension}>
          <div className="weight__head">
            <label className="weight__label" htmlFor={`${idPrefix}-${w.dimension}`}>
              {w.label}
            </label>
            <span className="weight__value">{w.value}%</span>
          </div>
          <div className="weight__track">
            <span className="weight__fill" style={{ width: `${w.value}%` }} />
            <span className="weight__knob" style={{ left: `${w.value}%` }} />
            <input
              id={`${idPrefix}-${w.dimension}`}
              className="weight__input"
              type="range"
              min={0}
              max={100}
              step={1}
              value={w.value}
              disabled={disabled}
              aria-valuetext={`${w.value}%`}
              onChange={(e) => onChange(i, Number(e.target.value))}
            />
          </div>
        </div>
      ))}
    </>
  );
}
