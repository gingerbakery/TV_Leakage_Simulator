import { AppDialog } from '@/components/common'
import { Button } from '@/components/ui/button'
import { RayTraceResultWindow } from '@/features/results'
import type { useReceiverViewSweepManager } from './receiver-view-sweep-manager'

export function ReceiverViewSweepDialog({ manager }: { manager: ReturnType<typeof useReceiverViewSweepManager> }) {
  const { state, reports } = manager
  const latest = reports.at(-1)
  const statusLabels = { idle: '대기', running: '진행 중', completed: '완료', completed_with_errors: '일부 실패', cancelled: '중단' }
  return <>
    <AppDialog open={manager.open} onOpenChange={manager.setOpen} title="시야각별 정밀해석" size="lg"
      help="각도마다 동일한 광원·재질·Ray 조건으로 Receiver 하나를 독립 배치해 순차 실행합니다. 자동 수렴은 각 각도에 따로 적용됩니다. 창을 닫아도 계산은 계속되며 결과 보기에서 완료된 각도의 Heatmap·경로를 비교하고 결과 파일을 저장할 수 있습니다. 원본 Case·Receiver 설정은 변경하지 않습니다."
      footer={<><Button variant="outline" onClick={() => manager.setOpen(false)}>닫기</Button>{manager.isRunning ? <Button variant="destructive" disabled={manager.stopping} onClick={() => void manager.cancel()}>{manager.stopping ? '중단 처리 중' : '중단'}</Button> : null}<Button disabled={!reports.length} onClick={() => manager.setReportOpen(true)}>결과 보기</Button></>}>
      {state ? <div role="status" className="mb-2 text-sm">{state.cases.filter((item) => item.status === 'completed').length} / {state.cases.length} 각도 · {Math.round(state.progress*100)}% · {statusLabels[state.status]}</div> : null}
      {manager.error ? <div role="alert" className="text-sm text-destructive">{manager.error}</div> : null}
      <div className="max-h-[55vh] overflow-auto"><table className="w-full text-right text-xs"><thead><tr><th className="text-left">좌우 / 상하</th><th>상태</th><th>Flux (lm)</th><th>Hits</th></tr></thead><tbody>{state?.cases.map((item) => {
        const result = reports.find((report) => report.caseId === item.caseId)?.result
        const flux = result?.receiver_grids.reduce((sum, grid) => sum + grid.flux_lumen.flat().reduce((subtotal, value) => subtotal+value, 0), 0)
        return <tr key={item.caseId}><td className="py-2 text-left">{item.name}</td><td>{item.status === 'completed' ? '완료' : item.status === 'failed' ? '실패' : item.status === 'cancelled' ? '중단' : `${Math.round(item.progress*100)}%`}{item.error ? <div role="alert" className="max-w-52 text-destructive">{item.error}</div> : null}</td><td>{flux?.toExponential(3) ?? '—'}</td><td>{result?.receiver_hit_count.toLocaleString() ?? '—'}</td></tr>
      })}</tbody></table></div>
    </AppDialog>
    {manager.reportOpen ? <RayTraceResultWindow open scene={manager.scene} result={latest?.result ?? null} reportCases={reports} onOpenChange={manager.setReportOpen} /> : null}
  </>
}
