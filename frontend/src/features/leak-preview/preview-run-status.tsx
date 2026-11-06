import { CheckCircle2, CircleAlert, LoaderCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

export function PreviewRunStatus({ busy, validating, message, phase, progress, terminal, failed }: {
  busy: boolean; validating: boolean; message: string; phase?: string; progress?: number; terminal?: string | null; failed?: boolean
}) {
  const running = busy || validating
  const percent = Math.max(0,Math.min(100,Math.round((progress ?? 0)*100)))
  return <div aria-label="Preview 실행 상태" aria-busy={running} className={cn('rounded-lg border p-3 shadow-sm', failed ? 'border-red-400 bg-red-50 dark:bg-red-950' : running ? 'border-sky-400 bg-sky-50 dark:bg-sky-950' : 'border-border bg-background')}>
    <div className="flex items-start gap-2">
      {running ? <LoaderCircle role="img" aria-label={validating ? '경로 검증 진행 중' : 'Preview 진행 중'} className="size-5 shrink-0 animate-spin text-sky-600 motion-reduce:animate-none" /> : failed ? <CircleAlert className="size-5 shrink-0 text-destructive" /> : <CheckCircle2 className="size-5 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1 space-y-1"><div className="text-sm font-semibold">{validating ? '경로 검증 중' : busy ? `Preview 실행 중 · ${percent}%` : failed ? '실행 실패' : terminal ? 'Preview 상태' : '대기'}</div>
        <div role={failed ? 'alert' : 'status'} aria-live="polite" className="break-words text-xs leading-5">{failed && terminal ? `${terminal} · ${message}` : message}</div>
        {busy && phase && phase !== message ? <div className="break-words text-xs">{phase}</div> : null}
        {!running && !failed && terminal && terminal !== message ? <div className="break-words text-xs text-muted-foreground">{terminal}</div> : null}
      </div>
    </div>
    {busy ? <div role="progressbar" aria-label="Preview 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} className="mt-2 h-2 overflow-hidden rounded-full bg-sky-200 dark:bg-sky-900"><div className="h-full bg-sky-600 transition-[width]" style={{ width: `${percent}%` }} /></div> : null}
  </div>
}
