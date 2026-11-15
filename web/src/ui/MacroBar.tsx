import './MacroBar.css'

type MacroBarProps = { label: string; value: number | null; target: number | null; unit: string; tone: 'protein' | 'carbs' | 'fat' }

export function MacroBar({ label, value, target, unit, tone }: MacroBarProps) {
  const percent = value === null || target === null || target <= 0 ? 0 : Math.min(100, Math.max(0, value / target * 100))
  return <div className={`ui-macro ui-macro--${tone}`}>
    <div className="ui-macro-label"><span>{label}</span><span>{value ?? '?'} / {target ?? '?'} {unit}</span></div>
    <div className="ui-macro-track" aria-hidden="true"><div style={{ width: `${percent}%` }} /></div>
  </div>
}
