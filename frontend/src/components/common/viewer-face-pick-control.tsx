import { CheckCircle2, MousePointerClick, ScanSearch } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface ViewerFacePickControlProps {
  armed: boolean
  assigned: boolean
  kind: 'surface' | 'datum'
  cadFaceCount?: number
  onToggle(): void
}

export function ViewerFacePickControl({
  armed,
  assigned,
  kind,
  cadFaceCount = 0,
  onToggle,
}: ViewerFacePickControlProps) {
  const completedText =
    kind === 'surface'
      ? `CAD 면 ${cadFaceCount.toLocaleString()}개 선택 완료`
      : '기준면 지정 완료'

  return (
    <div
      className={cn(
        'rounded-lg border p-2.5 transition-colors',
        armed
          ? 'border-orange-300 bg-orange-50'
          : assigned
            ? 'border-emerald-200 bg-emerald-50/70'
            : 'border-blue-200 bg-blue-50/65',
      )}
    >
      <Button
        type="button"
        variant="outline"
        aria-pressed={armed}
        className={cn(
          'w-full',
          armed
            ? 'border-orange-300 bg-orange-100 text-orange-950 hover:bg-orange-200'
            : 'border-blue-200 bg-white text-blue-900 hover:bg-blue-100',
        )}
        onClick={onToggle}
      >
        {armed ? <MousePointerClick /> : <ScanSearch />}
        {armed
          ? '면 선택 모드 종료'
          : assigned
            ? '뷰어에서 CAD Face 다시 선택'
            : '뷰어에서 CAD Face 선택'}
      </Button>
      <div
        role="status"
        className={cn(
          'mt-2 flex items-center gap-1.5 text-xs font-medium',
          armed
            ? 'text-orange-800'
            : assigned
              ? 'text-emerald-700'
              : 'text-muted-foreground',
        )}
      >
        {armed ? (
          <MousePointerClick className="size-3.5" />
        ) : assigned ? (
          <CheckCircle2 className="size-3.5" />
        ) : (
          <ScanSearch className="size-3.5" />
        )}
        {armed
          ? '선택 모드 ON · CAD Viewer에서 면을 클릭하세요'
          : assigned
            ? completedText
            : '위 버튼을 누르면 Viewer에서 면 선택이 가능합니다'}
      </div>
    </div>
  )
}
