import { RotateCcw } from 'lucide-react'

import { cn } from '@/lib/utils'

const DISPLAY_COLOR_PALETTE = [
  '#2563eb',
  '#0ea5e9',
  '#14b8a6',
  '#22c55e',
  '#eab308',
  '#f97316',
  '#ef4444',
  '#a855f7',
  '#64748b',
  '#111827',
  '#f8fafc',
  '#ffffff',
]

export interface ComponentColorPaletteProps {
  componentName: string
  value?: string | null
  fallbackColor?: string
  className?: string
  onValueChange(value: string | null): void
}

export function ComponentColorPalette({
  componentName,
  value,
  fallbackColor = '#64748b',
  className,
  onValueChange,
}: ComponentColorPaletteProps) {
  const effectiveColor = value ?? fallbackColor
  const normalizedValue = value?.toLowerCase() ?? null

  return (
    <div
      role="group"
      aria-label={`${componentName} 색상 선택`}
      data-component-color-palette
      className={cn(
        'rounded-xl border border-border bg-popover p-2 shadow-xl',
        className,
      )}
      style={{ width: '11.25rem', maxWidth: 'none' }}
    >
      <div className="grid grid-cols-7 gap-1">
        {DISPLAY_COLOR_PALETTE.map((color) => (
          <button
            key={color}
            type="button"
            aria-label={`표시색 ${color}`}
            aria-pressed={normalizedValue === color}
            title={color}
            className="size-5 rounded-full border border-black/20 ring-offset-1 transition-transform hover:scale-110 hover:ring-2 hover:ring-primary focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
            style={{ backgroundColor: color }}
            onClick={() => onValueChange(color)}
          />
        ))}
        <button
          type="button"
          aria-label={`${componentName} CAD 원본색으로 되돌리기`}
          aria-pressed={value == null}
          title="CAD 원본색"
          className="flex size-5 items-center justify-center rounded-full border border-border bg-background text-muted-foreground ring-offset-1 transition-transform hover:scale-110 hover:text-foreground hover:ring-2 hover:ring-primary focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none"
          onClick={() => onValueChange(null)}
        >
          <RotateCcw className="size-3" />
        </button>
        <label
          title="직접 색상 지정"
          className="relative size-5 cursor-pointer rounded-full border border-black/20 ring-offset-1 transition-transform [background:conic-gradient(#ef4444,#f59e0b,#22c55e,#0ea5e9,#8b5cf6,#ef4444)] hover:scale-110 hover:ring-2 hover:ring-primary focus-within:ring-2 focus-within:ring-primary"
        >
          <input
            type="color"
            value={effectiveColor}
            aria-label={`${componentName} 사용자 정의 표시색`}
            className="absolute inset-0 size-full cursor-pointer opacity-0"
            onChange={(event) =>
              onValueChange(event.currentTarget.value)
            }
          />
        </label>
      </div>
    </div>
  )
}
