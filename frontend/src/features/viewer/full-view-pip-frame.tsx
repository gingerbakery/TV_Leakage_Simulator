import { useRef, type PointerEvent, type ReactNode } from 'react'
import { MoveDiagonal2 } from 'lucide-react'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { fullViewPipMargin, fullViewPipRect, type FullViewPipSize } from './full-view-pip-layout'

interface FullViewPipFrameProps {
  viewport: FullViewPipSize
  size: FullViewPipSize | null
  onSizeChange(size: FullViewPipSize | null): void
  children?: ReactNode
}

export function FullViewPipFrame({ viewport, size, onSizeChange, children }: FullViewPipFrameProps) {
  const drag = useRef<{ pointerId: number; x: number; y: number; size: FullViewPipSize } | null>(null)
  const rect = fullViewPipRect(viewport, size)
  if (!rect) return null
  const applySize = (requested: FullViewPipSize) => {
    const next = fullViewPipRect(viewport, requested)
    if (next) onSizeChange({ width: next.width, height: next.height })
  }
  const finishDrag = (event: PointerEvent<HTMLButtonElement>) => {
    if (drag.current?.pointerId !== event.pointerId) return
    event.stopPropagation()
    drag.current = null
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId)
  }
  return <div data-testid="full-view-pip-frame"
    className="pointer-events-none absolute z-10 overflow-hidden rounded-md border border-border/70 shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
    style={{ right: fullViewPipMargin, bottom: fullViewPipMargin, width: rect.width, height: rect.height }}>
    <button type="button" aria-label="Full View 크기 조절"
      className="pointer-events-auto absolute top-0 left-0 flex size-5 touch-none cursor-nwse-resize items-center justify-center rounded-br border-r border-b border-border/70 bg-background/80 text-muted-foreground hover:bg-background focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
      onPointerDown={(event) => {
        if (event.button !== 0) return
        event.preventDefault()
        event.stopPropagation()
        event.currentTarget.focus()
        drag.current = { pointerId: event.pointerId, x: event.clientX, y: event.clientY, size: rect }
        event.currentTarget.setPointerCapture(event.pointerId)
      }}
      onPointerMove={(event) => {
        const start = drag.current
        if (!start || start.pointerId !== event.pointerId) return
        event.preventDefault()
        event.stopPropagation()
        applySize({ width: start.size.width + start.x - event.clientX, height: start.size.height + start.y - event.clientY })
      }}
      onPointerUp={finishDrag} onPointerCancel={finishDrag}
      onLostPointerCapture={() => { drag.current = null }}
      onDoubleClick={(event) => { event.stopPropagation(); onSizeChange(null) }}
      onKeyDown={(event) => {
        const step = event.shiftKey ? 40 : 10
        const steps: Record<string, [number, number]> = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }
        const delta = steps[event.key]
        if (!delta && event.key !== 'Home') return
        event.preventDefault()
        event.stopPropagation()
        if (event.key === 'Home') onSizeChange(null)
        else if (delta) applySize({ width: rect.width + delta[0], height: rect.height + delta[1] })
      }}>
      <MoveDiagonal2 className="size-3.5" />
    </button>
    <span className="absolute top-1 right-10 left-6 flex min-w-0 items-center gap-1 rounded bg-background/70 px-1 py-0.5 text-xs font-medium tracking-wide text-muted-foreground">
      <span className="truncate">Full View</span>
      <HelpTooltip label="Full View 크기 도움말" triggerClassName="pointer-events-auto size-4">
        왼쪽 위 모서리를 드래그해 크기를 조절합니다. 조절 버튼에 키보드 포커스를 둔 뒤 왼쪽·위 화살표로 확대하고 오른쪽·아래 화살표로 축소합니다. Shift를 함께 누르면 크게 조절합니다. 모서리를 더블클릭하거나 Home을 누르면 기본 크기로 돌아갑니다. 크기는 현재 작업 중 유지되며 창이 작아지면 화면 안으로 제한합니다.
      </HelpTooltip>
    </span>
    {children}
  </div>
}
