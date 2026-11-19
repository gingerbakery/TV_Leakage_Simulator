import { useEffect, useMemo, useState } from 'react'
import { AppDialog } from '@/components/common'
import { Button } from '@/components/ui/button'
import { RayTraceResultWindow } from '@/features/results'
import { formatReceiverViewSweepProgress } from './receiver-view-sweep'
import type { useReceiverViewSweepManager } from './receiver-view-sweep-manager'

function SweepElapsedClock({ startedAt, completedAt }: { startedAt: number; completedAt: number | null }) {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    if (completedAt !== null) return
    setNow(Date.now())
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [startedAt, completedAt])

  const seconds = Math.max(0, Math.floor(((completedAt ?? now) - startedAt) / 1000))
  const elapsed = [Math.floor(seconds / 3600), Math.floor(seconds / 60) % 60, seconds % 60]
    .map((part) => String(part).padStart(2, '0')).join(':')

  return <div className="flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
    <svg aria-hidden="true" viewBox="0 0 24 24" className="size-6 fill-none stroke-current" strokeWidth="1.5" strokeLinecap="round">
      <circle cx="12" cy="12" r="10" />
      <path d="M12 12V7" transform={`rotate(${(seconds % 3600) / 10} 12 12)`} />
      <path d="M12 12V4" transform={`rotate(${(seconds % 60) * 6} 12 12)`} />
    </svg>
    <div>
      <div>경과 시간</div>
      <div role="timer" aria-label="시야각 해석 경과 시간" className="font-mono tabular-nums">{elapsed}</div>
    </div>
  </div>
}

export function ReceiverViewSweepDialog({ manager }: { manager: ReturnType<typeof useReceiverViewSweepManager> }) {
  const { state, reports } = manager
  const latest = reports.at(-1)
  const current = state?.cases.find((item) => item.caseId === state.currentCaseId)
  const angleResults = useMemo(() => new Map(reports.map(({caseId, result}) => {
    const flux = result.receiver_grids.reduce((sum, grid) => sum + grid.flux_lumen.reduce(
      (gridSum, row) => gridSum + row.reduce((rowSum, value) => rowSum + value, 0), 0), 0)
    return [caseId, { flux, hits: result.receiver_hit_count }] as const
  })), [reports])
  const statusLabels = { idle: '대기', running: '진행 중', completed: '완료', completed_with_errors: reports.length ? '일부 실패' : '실패', cancelled: '중단' }
  return <>
    <AppDialog open={manager.open} onOpenChange={manager.setOpen} title="시야각별 정밀해석" size="lg"
      help="각도마다 동일한 광원·재질·Ray 조건으로 Receiver 하나를 독립 배치해 순차 실행합니다. 자동 수렴은 각 각도에 따로 적용됩니다. 창을 닫아도 계산은 계속되며 결과 보기에서 완료된 각도의 Heatmap·경로를 비교하고 결과 파일을 저장할 수 있습니다. 원본 Case·Receiver 설정은 변경하지 않습니다."
      footer={<><Button variant="outline" onClick={() => manager.setOpen(false)}>닫기</Button>{manager.isRunning ? <Button variant="destructive" disabled={manager.stopping} onClick={() => void manager.cancel()}>{manager.stopping ? '중단 처리 중' : '중단'}</Button> : null}<Button disabled={!reports.length} onClick={() => manager.setReportOpen(true)}>결과 보기</Button></>}>
      {state ? <div className="mb-2 flex items-start justify-between gap-3">
        <div role="status" className="min-w-0 space-y-1 text-sm">
          <div>{state.cases.filter((item) => item.status === 'completed').length} / {state.cases.length} 각도 · {formatReceiverViewSweepProgress(state.progress)} · {statusLabels[state.status]}</div>
          {state.status === 'running' ? <div className="text-xs text-muted-foreground">
            {current ? `${current.name} · ${current.phaseDetail}` : '실행 준비 중'}
            {current?.totalRays ? ` · ${(current.processedRays ?? 0).toLocaleString()} / ${current.totalRays.toLocaleString()} Ray` : null}
          </div> : null}
        </div>
        {state.startedAt !== null ? <SweepElapsedClock startedAt={state.startedAt} completedAt={state.completedAt} /> : null}
      </div> : null}
      {manager.error ? <div role="alert" className="text-sm text-destructive">{manager.error}</div> : null}
      <div className="max-h-[55vh] overflow-auto"><table className="w-full text-right text-xs"><thead><tr><th className="text-left">좌우 / 상하</th><th>상태</th><th>Flux (lm)</th><th>Hits</th></tr></thead><tbody>{state?.cases.map((item) => {
        const result = angleResults.get(item.caseId)
        const status = { pending: '대기', loading_scene: 'CAD 준비', queued: '서버 대기', preparing: '준비 중',
          running: formatReceiverViewSweepProgress(item.progress), finalizing: '결과 집계', completed: '완료', failed: '실패', cancelled: '중단' }[item.status]
        return <tr key={item.caseId}><td className="py-2 text-left">{item.name}</td><td>{status}{item.error ? <div role="alert" className="max-w-52 text-destructive">{item.error}</div> : null}</td><td>{result?.flux.toExponential(3) ?? '—'}</td><td>{result?.hits.toLocaleString() ?? '—'}</td></tr>
      })}</tbody></table></div>
    </AppDialog>
    {manager.reportOpen ? <RayTraceResultWindow open scene={manager.scene} result={latest?.result ?? null} reportCases={reports} onOpenChange={manager.setReportOpen} /> : null}
  </>
}
