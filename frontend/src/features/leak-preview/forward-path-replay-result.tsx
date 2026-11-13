import type { ForwardPathReplay } from '@/api/client'
import { HelpTooltip } from '@/components/common/help-tooltip'
import { ReceiverDiagnostics } from '@/features/results/receiver-diagnostics'

const reasons:Record<string,string>={receiver_reached:'센서 도달',energy_cutoff:'에너지 컷오프',depth_limit:'최대 반사 종료',
  roulette_terminated:'확률적 종료',different_sampled_branch:'확률적 반사 분기 변경',first_intersection_differs:'충돌 위치 불일치',
  receiver_not_reached:'센서 미도달',missing_representative_path:'대표 경로 없음',degenerate_first_segment:'출발 구간 길이 부족',
  degenerate_segment:'경로 구간 길이 부족',replay_execution_failed:'재생 실행 오류',
  ambiguous_source:'중첩 광원 식별 불가',
  source_position_or_emission_mismatch:'광원 위치·방출 불일치'}
export function ForwardPathReplayResult({report, defaultOpen = false}:{report:ForwardPathReplay|undefined; defaultOpen?: boolean}) {
  if (!report) return null
  return <details open={defaultOpen || undefined} className="rounded border p-2 text-xs">
    <summary className="cursor-pointer">대표 경로 순방향 재생 · {report.path_reproduced?'동일 경로 도달':reasons[report.reason]??report.reason}</summary>
    <div className="mt-2 flex items-center gap-1">CPU · {report.state==='unavailable'?'재생 미실행':report.state==='failed'?'재생 실패':'진단용 1 Ray'} · 본 광량 미포함<HelpTooltip label="순방향 경로 재생 도움말">저장된 광원 위치·첫 방향만 고정하고 실제 순방향 추적기로 계산합니다. 원래 발사 Ray 수에 따른 패킷 광량·컷오프를 유지합니다. 확률적 반사는 같은 방향을 강제하지 않으므로 분기 변경이 곧 오류는 아닙니다. CPU 재생이며 GPU 일치나 실물 정확도를 증명하지 않습니다. 이 Ray의 검출·광량은 본 해석과 비교 추정에 합산하지 않습니다. 구간 번호는 광원 출발을 1로 표시합니다.</HelpTooltip></div>
    <div>{report.state === 'receiver_reached' ? '센서 도달' : report.state === 'not_reached' ? '센서 미도달' : '센서 도달 미판정'} · 일치 반사점 {report.matched_surface_count??'—'}개</div>
    {report.state==='failed'?<div role="alert">{report.error??'재생 실행 실패 · 경로 재현 판정 불가'}</div>:null}
    {report.diagnostics ? <ReceiverDiagnostics value={report.diagnostics} /> : null}
    {report.first_difference?<div role="status">첫 불일치 구간 {report.first_difference.segment_index+1} · 예상 ({report.first_difference.expected_point.map(v=>v.toFixed(4)).join(', ')}) · 실제 {report.first_difference.actual_point?`(${report.first_difference.actual_point.map(v=>v.toFixed(4)).join(', ')})`:'해당 반사점 미도달'}</div>:null}
    <button type="button" className="mt-2 underline" onClick={()=>{
      const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}))
      const a=document.createElement('a');a.href=url;a.download='forward-path-replay.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)
    }}>재생 JSON 저장</button>
  </details>
}
