import { HelpTooltip } from '@/components/common/help-tooltip'

interface ReflectionStep {
  reflection_index: number; component_name: string | null; profile_id: string; scatter_model: string
  effective_reflectance: number; incoming_nominal_packet_lumen: number; outgoing_nominal_packet_lumen: number
  cumulative_log10_ratio: number | null; directional_brdf_per_sr: number; directional_delta_weight: number
}
interface AttenuationReport {
  state: string; reason: string; steps: ReflectionStep[]; expected_reflections?: number
  final_reflectance_ratio?: number | null; final_log10_ratio?: number | null
  effective_min_energy_lumen?: number; first_below_min_energy_reflection?: number | null
  largest_loss_reflection?: number | null
}
const record = (value: unknown): value is Record<string, unknown> => Boolean(value && typeof value === 'object' && !Array.isArray(value))
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value)

function readAttenuation(value: unknown): AttenuationReport | null {
  if (!record(value) || value.contract !== 'reference_reflectance_chain_v1' || typeof value.state !== 'string' ||
    typeof value.reason !== 'string' || !Array.isArray(value.steps)) return null
  for (const step of value.steps) {
    if (!record(step) || !finite(step.reflection_index) || step.reflection_index < 1 ||
      typeof step.profile_id !== 'string' || typeof step.scatter_model !== 'string' ||
      (step.component_name !== null && typeof step.component_name !== 'string') ||
      !['effective_reflectance', 'incoming_nominal_packet_lumen', 'outgoing_nominal_packet_lumen',
        'directional_brdf_per_sr', 'directional_delta_weight'].every(key => finite(step[key]) && step[key] >= 0) ||
      !finite(step.effective_reflectance) || step.effective_reflectance > 1 ||
      (step.cumulative_log10_ratio !== null && !finite(step.cumulative_log10_ratio))) return null
  }
  if (value.state === 'complete' && (!finite(value.expected_reflections) || value.steps.length !== value.expected_reflections ||
    !finite(value.final_reflectance_ratio) || value.final_reflectance_ratio < 0 || value.final_reflectance_ratio > 1 ||
    (value.final_log10_ratio !== null && !finite(value.final_log10_ratio)) ||
    !finite(value.effective_min_energy_lumen) ||
    (value.first_below_min_energy_reflection !== null && !finite(value.first_below_min_energy_reflection)) ||
    (value.largest_loss_reflection !== null && !finite(value.largest_loss_reflection)))) return null
  return value as unknown as AttenuationReport
}

function RemainingRatio({ logRatio }: { logRatio: number | null | undefined }) {
  return logRatio == null ? <>0</> : <>10<sup>{logRatio.toFixed(2)}</sup></>
}

export function ReferenceAttenuationResult({ value }: { value: unknown }) {
  const report = readAttenuation(value)
  if (!report) return <div role="status">반사 손실 기록 없음 · 경로 검증 재실행 필요</div>
  return <section aria-label="대표 경로 반사 손실" className="my-2 space-y-1 rounded border p-2">
    <div className="flex items-center gap-1 font-semibold">대표 경로 반사 손실<HelpTooltip label="대표 경로 반사 손실 도움말">검증한 각 반사점의 실제 재질과 입사각으로 유효 반사율을 계산하고 곱한 잔존비입니다. 반사 전후 기준 패킷은 광원 광속÷발사 Ray 수에 이 비율을 적용한 값입니다. 방향별 분포·센서 면적·MIS·확률적 생존 가중치는 포함하지 않습니다. BRDF는 방향별 값(1/sr)이며 확률이 아닙니다. 최소 에너지 아래로 내려가는 반사는 가중치 보정 전 기준입니다. 실제 재생의 확률적 종료 사유와 함께 확인하세요. 이 검사값은 본 검출·광량에 합산하지 않습니다. 광속을 높여도 잔존비는 같으며, 실물 비교에는 실제 광원 출력을 사용하세요.</HelpTooltip></div>
    {report.state === 'complete' ? <>
      <div role="status">반사 {report.expected_reflections}회 · 반사율 누적 잔존비 <RemainingRatio logRatio={report.final_log10_ratio} /></div>
      {report.largest_loss_reflection != null ? <div>가장 큰 반사 손실 · {report.largest_loss_reflection}회차</div> : null}
      {report.first_below_min_energy_reflection != null ? <div role="status">기준 패킷이 최소 에너지 미만 · 첫 {report.first_below_min_energy_reflection}회차</div> : null}
    </> : <div role="status">반사 손실 판정 보류 · {report.reason}</div>}
    {report.steps.length > 0 ? <details><summary className="cursor-pointer">반사별 재질·광량</summary>
      <table className="mt-1 w-full tabular-nums"><thead><tr><th>반사</th><th>부품·재질</th><th>유효 반사율</th><th>기준 패킷 전 → 후 (lm)</th></tr></thead>
        <tbody>{report.steps.map(step => <tr key={step.reflection_index}>
          <td>{step.reflection_index}</td><td>{step.component_name ?? '부품명 없음'} · {step.profile_id} · {step.scatter_model}</td>
          <td>{(step.effective_reflectance * 100).toFixed(2)}%</td>
          <td>{step.incoming_nominal_packet_lumen.toExponential(3)} → {step.outgoing_nominal_packet_lumen.toExponential(3)}</td>
        </tr>)}</tbody></table>
      {report.steps.map(step => <div key={step.reflection_index}>반사 {step.reflection_index} · BRDF {step.directional_brdf_per_sr.toExponential(3)} /sr · 정반사 방향 가중치 {step.directional_delta_weight.toExponential(3)} · 누적 잔존비 <RemainingRatio logRatio={step.cumulative_log10_ratio} /></div>)}
    </details> : null}
  </section>
}
