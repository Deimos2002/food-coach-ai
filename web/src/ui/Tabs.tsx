import './Tabs.css'

type TabsProps = { items: { id: string; label: string }[]; value: string; onChange: (id: string) => void }

export function Tabs({ items, value, onChange }: TabsProps) {
  return <div className="ui-tabs" role="tablist">
    {items.map((item, index) => <button key={item.id} type="button" role="tab" aria-selected={value === item.id}
      tabIndex={value === item.id ? 0 : -1} className="ui-tab" onClick={() => onChange(item.id)}
      onKeyDown={(event) => {
        if (event.key !== 'ArrowRight' && event.key !== 'ArrowLeft') return
        event.preventDefault()
        const next = (index + (event.key === 'ArrowRight' ? 1 : -1) + items.length) % items.length
        const nextItem = items[next]
        if (!nextItem) return
        onChange(nextItem.id)
        const tabs = event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>('[role="tab"]')
        tabs?.[next]?.focus()
      }}>{item.label}</button>)}
  </div>
}
