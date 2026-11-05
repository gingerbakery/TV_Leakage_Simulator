import { useId, useState } from 'react'
import { CheckCircle2, ChevronDown, CircleAlert, Cpu, Gauge, TriangleAlert } from 'lucide-react'

import type { ComputeBackend } from '@/api'
import { Badge } from '@/components/ui/badge'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { resolveComputeExecution } from './compute-execution-model'

export function ComputeExecutionStatus({
  configuredBackend,
  performance,
  samplingScope = 'primary',
}: {
  configuredBackend: ComputeBackend
  performance: Record<string, unknown>
  samplingScope?: 'primary' | 'observer'
}) {
  const [detailsOpen, setDetailsOpen] = useState(false)
  const detailsId = useId()
  const summary = resolveComputeExecution(configuredBackend, performance)
  const observer = samplingScope === 'observer'
  const receiverEvidence = Array.isArray(performance.observer_receiver_validation) ? performance.observer_receiver_validation.filter(
    (item): item is { receiver_id: string; path_valid: boolean; forward_hit_count: number; diagnosis: string; forward_energy_cutoff_count?: number } => Boolean(item && typeof item === 'object' && typeof item.receiver_id === 'string' && typeof item.path_valid === 'boolean' && typeof item.forward_hit_count === 'number'),
  ) : []
  const warning = summary.state === 'gpu-fallback' || summary.state === 'gpu-zero'
  const active = summary.state === 'gpu-active' || summary.state === 'gpu-mixed'
  const Icon = active
    ? CheckCircle2
    : warning
      ? TriangleAlert
      : summary.state === 'cpu'
        ? Cpu
        : CircleAlert

  return (
    <section
      aria-label="Compute execution status"
      role={warning ? 'alert' : 'status'}
      className={
        active
          ? 'rounded-lg border border-emerald-500/40 bg-emerald-500/8 p-3'
          : warning
            ? 'rounded-lg border border-orange-400/50 bg-orange-500/8 p-3'
            : 'rounded-lg border border-border bg-muted/20 p-3'
      }
    >
      <button
        type="button"
        aria-expanded={detailsOpen}
        aria-controls={detailsId}
        title={detailsOpen ? '연산 장치 상세 정보 닫기' : '연산 장치 상세 정보 열기'}
        className="flex w-full min-w-0 items-center gap-2 rounded-sm text-left outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
        onClick={() => setDetailsOpen((open) => !open)}
      >
        <Icon
          className={active ? 'size-5 shrink-0 text-emerald-600' : warning ? 'size-5 shrink-0 text-orange-600' : 'size-5 shrink-0 text-primary'}
          aria-hidden="true"
        />
        <span className="min-w-0 flex-1 font-semibold">Compute device · {summary.title}</span>
        <ChevronDown
          className={`size-4 shrink-0 text-muted-foreground transition-transform motion-reduce:transition-none ${detailsOpen ? 'rotate-180' : ''}`}
          aria-hidden="true"
        />
      </button>
      {receiverEvidence.filter((item) => !item.path_valid || item.forward_hit_count === 0).map((item) => <div key={item.receiver_id} role="status" className="mt-2 flex items-center gap-1 text-xs text-orange-800 dark:text-orange-200">{item.receiver_id} · {item.path_valid ? '대표 경로 유효 · 순방향 0 Ray' : '대표 경로·Receiver 조건 불일치'}<HelpTooltip label={`${item.receiver_id} 역추적 검증 도움말`}>{item.diagnosis} · 실제 형상·광원 방향·반사 지원·최대 반사·Receiver 위치·방향·Acceptance·ROI를 대표 경로로 검사합니다. 유효한 한 경로가 있다는 사실은 무작위 순방향 추적의 검출 횟수나 같은 광량을 보장하지 않습니다. 에너지 컷오프 {item.forward_energy_cutoff_count ?? 0}회. 역추적 휘도 추정은 결정적 에너지 컷오프를 사용하지 않습니다. 검증 경로를 Ray hit나 광량에 더하지 않습니다.</HelpTooltip></div>)}
      <div id={detailsId} hidden={!detailsOpen} role="region" aria-label="연산 장치 상세 정보">
        <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
          {typeof performance.api_preparation_sec === 'number' && Number.isFinite(performance.api_preparation_sec) ? <Badge variant="outline">{performance.api_geometry_cache_hit ? '형상·BVH 재사용' : '형상·BVH 준비'} · 준비 {performance.api_preparation_sec.toFixed(2)} s</Badge> : null}
          <Badge variant={active ? 'default' : warning ? 'destructive' : 'outline'}>
            {summary.requested === 'gpu_cuda' ? 'GPU requested' : 'CPU requested'}
          </Badge>
          {summary.deviceName ? <Badge variant="outline">{summary.deviceName}</Badge> : null}
          <Badge variant="outline">Provider · {summary.provider}</Badge>
          {summary.requested === 'gpu_cuda' ? (
            <Badge variant={summary.gpuSuccesses > 0 ? 'secondary' : 'destructive'}>
              CUDA batches · {summary.gpuSuccesses}/{summary.gpuAttempts}
            </Badge>
          ) : null}
          {summary.requested === 'gpu_cuda' && !observer ? (
            <Badge
              variant={
                summary.gpuResidentMode === 'gpu_resident' &&
                summary.gpuResidentSuccesses > 0
                  ? 'secondary'
                  : 'destructive'
              }
            >
              GPU Resident · {summary.gpuResidentSuccesses}/
              {summary.gpuResidentAttempts}
            </Badge>
          ) : null}
          {summary.cpuSmallWaveSuccesses > 0 ? (
            <Badge variant="outline">CPU small waves · {summary.cpuSmallWaveSuccesses}</Badge>
          ) : null}
          {summary.accuracyParityVerified ? (
            <Badge variant="secondary">CPU/GPU 동일 샘플 계약</Badge>
          ) : null}
          {observer && typeof performance.observer_sample_contract === 'string' ? <Badge variant="outline">역추적 샘플 계약<HelpTooltip label="역추적 샘플 계약 도움말">{performance.observer_sample_contract} · 샘플 생성 규칙의 기록입니다. 일반 Ray Tracing의 동일 샘플 계약이나 GPU 실기 정확도 검증 완료를 뜻하지 않습니다.</HelpTooltip></Badge> : null}
          {observer && Number(performance.observer_resident_success_count) > 0 ? <Badge variant="secondary">GPU 역추적 · {Number(performance.observer_resident_success_count)}묶음</Badge> : null}
        </div>
        {summary.reason ? (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-orange-800 dark:text-orange-200">
            <Gauge className="size-3.5" aria-hidden="true" />
            {summary.reason}
          </p>
        ) : null}
        {active && !summary.accuracyParityVerified ? (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-orange-800 dark:text-orange-200">
            <TriangleAlert className="size-3.5" aria-hidden="true" />
            {observer ? '역추적 CPU/GPU 전체 정합 검증 정보가 없습니다.' : 'CPU/GPU 동일 샘플 계약을 확인할 수 없습니다.'}
          </p>
        ) : null}
        {observer && typeof performance.observer_resident_fallback_reason === 'string' ? <p className="mt-2 text-xs text-orange-800 dark:text-orange-200">GPU 역추적 대체 사유 · {performance.observer_resident_fallback_reason}</p> : null}
      </div>
    </section>
  )
}
