import './Ring.css'

type RingProps = { value: number | null; target: number | null; label: string; unit: string }

export function Ring({ value, target, label, unit }: RingProps) {
  const percent = value === null || target === null || target <= 0 ? 0 : Math.min(100, Math.max(0, value / target * 100))
  return <div style={{ background: `conic-gradient(var(--accent) ${percent}%, var(--border) 0)` }} className="ui-ring" role="img" aria-label={`${label} ${value ?? '?'} of ${target ?? '?'} ${unit}`}>
    <div className="ui-ring-content"><strong>{value ?? '?'}</strong><span>{label}</span><span>of {target ?? '?'} {unit}</span></div>
  </div>
}
