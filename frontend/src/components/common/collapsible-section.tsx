import { useId, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function CollapsibleSection({ title, count, children, defaultOpen = true }: { title: string; count?: number; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const id = useId()
  return <section className="space-y-2 rounded-lg border border-border p-2">
    <Button type="button" variant="ghost" size="sm" className="h-7 w-full justify-start px-1 font-semibold" aria-label={`${title} ${open ? '접기' : '펼치기'}`} aria-expanded={open} aria-controls={id} onClick={() => setOpen(!open)}>
      {open ? <ChevronDown /> : <ChevronRight />}{title}{count !== undefined ? ` · ${count}` : ''}
    </Button>
    <div id={id} hidden={!open}>{children}</div>
  </section>
}
