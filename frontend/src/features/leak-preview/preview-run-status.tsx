import { useEffect, useState } from 'react'
import { CheckCircle2, CircleAlert, LoaderCircle } from 'lucide-react'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { cn } from '@/lib/utils'

export function PreviewRunStatus({ busy, validating, message, phase, progress, terminal, failed }: {
  busy: boolean; validating: boolean; message: string; phase?: string; progress?: number; terminal?: string | null; failed?: boolean
}) {
  const running = busy || validating
  const [unchangedSeconds, setUnchangedSeconds] = useState(0)
  useEffect(() => {
    setUnchangedSeconds(0)
    if (!running) return
    const started = Date.now()
    const timer = setInterval(() => setUnchangedSeconds(Math.floor((Date.now()-started)/1000)), 1000)
    return () => clearInterval(timer)
  }, [running, phase, progress, message])
  const percent = Math.max(0,Math.min(100,Math.round((progress ?? 0)*100)))
  return <div aria-label="Preview 실행 상태" aria-busy={running} className={cn('rounded-lg border p-3 shadow-sm', failed ? 'border-red-400 bg-red-50 dark:bg-red-950' : running ? 'border-sky-400 bg-sky-50 dark:bg-sky-950' : 'border-border bg-background')}>
    <div className="flex items-start gap-2">
      {running ? <LoaderCircle role="img" aria-label={validating ? '경로 검증 진행 중' : 'Preview 진행 중'} className="size-5 shrink-0 animate-spin text-sky-600 motion-reduce:animate-none" /> : failed ? <CircleAlert className="size-5 shrink-0 text-destructive" /> : <CheckCircle2 className="size-5 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1 space-y-1"><div className="text-sm font-semibold">{validating ? '경로 검증 중' : busy ? `Preview 실행 중 · ${percent}%` : failed ? '실행 실패' : terminal ? 'Preview 상태' : '대기'}</div>
        <div role={failed ? 'alert' : 'status'} aria-live="polite" className="break-words text-xs leading-5">{failed && terminal ? `${terminal} · ${message}` : message}</div>
        {busy && phase && phase !== message ? <div className="break-words text-xs">{phase}</div> : null}
        {running && unchangedSeconds >= 60 ? <div role="status" className="flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">진행 갱신 없음 · {unchangedSeconds}초<HelpTooltip label="진행 갱신 지연 도움말">마지막 진행 수치·단계가 바뀐 뒤 경과한 시간입니다. 긴 초기 컴파일·배치 계산·서버 응답 지연일 수 있으며 정지 확정은 아닙니다. 중지 요청은 진행 중 배치가 끝난 경계에서 처리됩니다.</HelpTooltip></div> : null}
        {!running && !failed && terminal && terminal !== message ? <div className="break-words text-xs text-muted-foreground">{terminal}</div> : null}
      </div>
    </div>
    {busy ? <div role="progressbar" aria-label="Preview 진행률" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} className="mt-2 h-2 overflow-hidden rounded-full bg-sky-200 dark:bg-sky-900"><div className="h-full bg-sky-600 transition-[width]" style={{ width: `${percent}%` }} /></div> : null}
  </div>
}
