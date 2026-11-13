import { useEffect, useRef, useState } from 'react'
import { apiClient, type RayTraceRequest } from '@/api'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { SensorComparisonJob } from '@/api/client'
import { SensorBatchDiagnostics } from './sensor-batch-diagnostics'
import { comparisonConclusion, comparisonValidationAdvice } from './validation-guidance'
import { ValidationNextStep } from './validation-next-step'
import { ForwardDetectionDiagnostics } from './forward-detection-diagnostics'
import { ReceiverDiagnostics } from '@/features/results/receiver-diagnostics'
import { ForwardPathReplayResult } from './forward-path-replay-result'
import { sameComparisonInput } from './comparison-input-identity'

const flux = (value?: number) => value === undefined ? '—' : value.toExponential(5)

export function SensorComparisonDialog({ request, onClose }: { request: RayTraceRequest; onClose(): void }) {
  const content = useRef<HTMLDivElement>(null)
  const [batches, setBatches] = useState(8)
  const [forward, setForward] = useState(16384)
  const [reverse, setReverse] = useState(1024)
  const [tolerance, setTolerance] = useState(5)
  const [job, setJob] = useState<SensorComparisonJob | null>(null)
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [settingsNotice, setSettingsNotice] = useState('')
  const receiver = request.receivers[0]
  const active = job?.status === 'queued' || job?.status === 'running'
  useEffect(() => {
    let disposed = false
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      try {
        const next = await apiClient.getSensorComparison()
        if (disposed) return
        setJob(next)
        if (next?.status === 'queued' || next?.status === 'running') timer = setTimeout(poll, 750)
      } catch (e) { if (!disposed) setError(String(e)) }
    }
    void poll()
    return () => { disposed = true; clearTimeout(timer) }
  }, [pending])
  const report = job?.report
  const executions = report?.batches?.flatMap(row => [row.forward_execution, row.reverse_execution]) ?? []
  const count = (key: string) => executions.reduce((sum, row) => sum + (typeof row[key] === 'number' ? row[key] as number : 0), 0)
  const sameSensor = Boolean(report && sameComparisonInput(job?.input_request,request))
  const displayedReport = job?.status === 'stopped' && report
    ? { ...report, reason: 'stopped' }
    : report
  const advice = displayedReport ? comparisonValidationAdvice(displayedReport, sameSensor) : null
  return <Dialog open onOpenChange={(open) => { if (!open) onClose() }}><DialogContent ref={content} tabIndex={-1} onOpenAutoFocus={event => { event.preventDefault(); content.current?.focus() }} className="max-h-[85vh] overflow-y-auto" aria-describedby={undefined}>
    <DialogHeader><DialogTitle className="flex items-center gap-1">동일 센서 광량 비교<HelpTooltip label="광량 비교 도움말">같은 유한 면적·수용각·ROI의 광량(lm)을 독립 순방향·역추적으로 적분합니다. Preview의 한 방향 휘도(nit)를 직접 비교하지 않습니다. NVIDIA CUDA GPU로 계산하며 양방향 모두 실제 CUDA 실행 증거가 있어야 비교를 완료합니다. CPU 보조가 있으면 혼합 실행으로 표시합니다. 검증 복사본에서 결정적 에너지 컷오프를 제거하며 원래 설정은 보존합니다. 원래 종료 조건도 같은 검증 표본 예산으로 한 번 별도 계산합니다. 기존 100만 Ray 결과의 재현은 아닙니다. 차이의 근사 95% 구간 전체가 허용 범위 안일 때만 일치입니다. 0 Ray·표본 부족은 미확정이며 실물 정확도를 보장하지 않습니다. 창을 닫아도 실행은 계속되며, 다시 열면 마지막 작업이 표시됩니다.</HelpTooltip></DialogTitle></DialogHeader>
    {error || job?.status === 'failed' ? <div role="status" className="rounded border border-destructive p-3 text-sm font-semibold">{job?.status === 'failed' ? '실행 실패 · 오류 확인 후 재비교' : '판정 불가 · 요청 오류 확인'}</div> : null}
    {report && !active && !pending && !error && job?.status !== 'failed' ? <section aria-label="광량 비교 결론" className="space-y-2 rounded border p-3">
      <div role="status" className="text-sm font-semibold">{comparisonConclusion(displayedReport!, sameSensor)}</div>
      {sameSensor ? <div className="text-xs">순방향 검출 {report.forward_hits ?? '—'} · 역추적 양수 {report.reverse_positive_samples ?? '—'}</div> : null}
      {advice ? <div className="flex flex-wrap items-center gap-2">
        <ValidationNextStep advice={advice} label="광량 비교 다음 단계" />
        {advice.samples ? <span className="tabular-nums">{advice.samples.batches}회 · F {advice.samples.forward_samples.toLocaleString()} / R {advice.samples.reverse_samples.toLocaleString()}</span> : null}
        {advice.samples ? <Button size="sm" variant="outline" onClick={() => {
          setBatches(advice.samples!.batches); setForward(advice.samples!.forward_samples); setReverse(advice.samples!.reverse_samples)
          setSettingsNotice('권장 표본 설정 적용 · 비교 시작 필요')
        }}>권장 표본 적용</Button> : null}
      </div> : null}
      {settingsNotice ? <div role="status">{settingsNotice}</div> : null}
    </section> : null}
    <div className="text-xs">NVIDIA CUDA GPU · {receiver.width_mm} × {receiver.height_mm} mm · 수용각 {receiver.acceptance_angle_deg}°</div>
    <div className="grid grid-cols-2 gap-3 text-xs">
      <label>독립 반복<select aria-label="독립 반복" value={batches} disabled={active || pending} onChange={e => setBatches(Number(e.target.value))} className="block w-full rounded border p-1">{[4, 8, 16].map(n => <option key={n}>{n}</option>)}</select></label>
      <label>허용 차이 (%)<NumberInput aria-label="허용 차이" value={tolerance} onValueChange={setTolerance} min={.1} max={25} disabled={active || pending} /></label>
      <label>순방향 표본 / 회<NumberInput aria-label="순방향 표본" value={forward} onValueChange={setForward} min={32} max={250000} disabled={active || pending} /></label>
      <label>역추적 표본 / 회<NumberInput aria-label="역추적 표본" value={reverse} onValueChange={setReverse} min={32} max={100000} disabled={active || pending} /></label>
    </div>
    <div className="flex gap-2"><Button disabled={active || pending} onClick={async () => {
      setPending(true); setError(''); setSettingsNotice('')
      try { setJob(await apiClient.startSensorComparison(request, { receiver_id: receiver.receiver_id, batches, forward_samples: forward, reverse_samples: reverse, tolerance_percent: tolerance })) }
      catch (e) { setError(e instanceof Error ? e.message : String(e)) }
      finally { setPending(false) }
    }}>비교 시작</Button><Button variant="outline" disabled={!active || job?.stop_requested} onClick={async () => {
      try { if (job) setJob(await apiClient.stopSensorComparison(job.job_id)) } catch (e) { setError(String(e)) }
    }}>{job?.stop_requested ? '중지 요청됨' : '중지'}</Button></div>
    {job ? <div role="status" className="text-xs">{({ queued: '대기', running: '계산 중', completed: '완료', stopped: '중지', failed: '실패' })[job.status]} · {Math.round(job.progress * 100)}% · {job.batch}/{report?.options.batches ?? batches}회 · {({ preparing: '형상 준비', forward: '순방향', reverse: '역추적', original_policy: '기존 종료 조건', finished: '종료' } as Record<string, string>)[job.phase] ?? job.phase}</div> : null}
    {error || job?.error ? <div role="alert" className="text-xs text-destructive">{error || job?.error}</div> : null}
    {(error || job?.status === 'failed') && !report ? <ValidationNextStep label="광량 비교 실행 오류 다음 단계" advice={{title:'실행 오류 확인 후 재비교',steps:[
      '오류 문구와 서버 로그의 발생 위치를 확인하세요. 실행이 실패했으므로 광량 일치·불일치·빛샘 없음은 판정할 수 없습니다.',
      'GPU 실행 오류라면 production 사전 검사와 해당 run의 CUDA 실행 증거를 확인한 뒤 같은 센서 조건으로 다시 시작하세요. CPU 대체 실행이나 표본 증가로 실행 오류를 성공 처리하지 않습니다.',
    ]}} /> : null}
    {report ? <div className="space-y-2 text-xs">
      <details className="rounded border p-2">
        <summary className="cursor-pointer">상세 결과</summary>
        <div className="mt-2 space-y-2">
          <div>최근 작업 · {report.sensor.width_mm} × {report.sensor.height_mm} mm · {report.sensor.acceptance_angle_deg}° · 허용 {report.options.tolerance_percent}%</div>
          {!sameSensor ? <div role="alert">이전 작업 결과 · 현재 조건과 동일한지 확인 필요</div> : null}
          {sameSensor ? <div>전체 요청 입력 일치</div> : null}
          {report.physical_input_sha256 ? <div className="flex items-center gap-1">물리 입력 {report.physical_input_sha256.slice(0,12)}<HelpTooltip label="비교 입력 동일성 도움말">전체 요청 스냅샷의 위치·방향·광원·재질·ROI·형상 변환·해석 설정을 비교합니다. 물리 입력 SHA-256은 서버가 준비한 형상·재질 배치와 측정 조건의 식별자이며 검증 JSON에 기록됩니다. 해시 일치는 실물 정확도의 증명이 아닙니다.</HelpTooltip></div> : null}
          <div>{report.gpu_execution_proven ? report.compute_execution_state === 'gpu_mixed' ? 'GPU 실행 확인 · CPU 보조 포함' : 'GPU 실행 확인' : 'GPU 실행 미확인'}</div>
          {executions.some(row => row.observer_bsdf_evaluation_backend === 'cpu') ? <div>BSDF CPU 평가 · CAD 교차 GPU</div> : null}
          <div>CUDA 성공/시도 {count('gpu_cuda_gpu_success_count')}/{count('gpu_cuda_gpu_attempt_count')} · CPU 보조 {count('gpu_cuda_hybrid_cpu_success_count')} · 대체 {count('fallback_count')}</div>
          <ForwardDetectionDiagnostics report={report} />
          <ForwardPathReplayResult report={report.reference_replay} />
          {report.forward_diagnostics?.map(row=><details key={row.index} className="rounded border p-2"><summary className="cursor-pointer">순방향 상세 진단 · {row.index}회</summary><ReceiverDiagnostics value={row.report} requested /></details>)}
          {report.original_termination_probe?.diagnostics ? <details className="rounded border p-2"><summary className="cursor-pointer">기존 종료 조건 상세 진단</summary><ReceiverDiagnostics value={report.original_termination_probe.diagnostics} requested /></details> : null}
          <table className="w-full"><tbody>
            <tr><th className="text-left">순방향 (lm)</th><td>{flux(report.forward_flux_lumen)}</td></tr>
            <tr><th className="text-left">역추적 (lm)</th><td>{flux(report.reverse_flux_lumen)}</td></tr>
            <tr><th className="text-left">차이 구간 (lm)</th><td>{report.difference_interval_lumen?.map(flux).join(' ~ ') ?? '—'}</td></tr>
            <tr><th className="text-left">허용 차이 (lm)</th><td>±{flux(report.tolerance_lumen)}</td></tr>
            <tr><th className="text-left">순방향 검출 / 역추적 양수</th><td>{report.forward_hits ?? '—'} / {report.reverse_positive_samples ?? '—'}</td></tr>
            <tr><th className="text-left">유효 표본 F / R</th><td>{report.forward_effective_samples?.toFixed(1) ?? '—'} / {report.reverse_effective_samples?.toFixed(1) ?? '—'}</td></tr>
          </tbody></table>
          {report.batches?.length ? <SensorBatchDiagnostics rows={report.batches} /> : null}
          {report.original_termination_probe ? <div>기존 종료 조건 · {flux(report.original_termination_probe.forward_flux_lumen)} lm · {report.original_termination_probe.forward_hits} hits</div> : null}
        </div>
      </details>
      <Button size="sm" variant="outline" onClick={() => { const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = 'sensor-comparison.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }}>검증 JSON 저장</Button>
    </div> : null}
  </DialogContent></Dialog>
}
