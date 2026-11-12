import type { SensorComparisonOptions, SensorComparisonReport } from '@/api/client'

export interface ValidationAdvice {
  title: string
  steps: string[]
  samples?: Pick<SensorComparisonOptions,'batches'|'forward_samples'|'reverse_samples'>
}

export function pathValidationAdvice(valid: boolean, reason: string): ValidationAdvice {
  if (reason==='api_error') return {title:'실행 오류 확인 후 재검증',steps:[
    '검증 요청이 실패하여 경로의 통과·실패를 판단하지 못했습니다. 표시된 오류와 서버 상태를 확인하세요.',
    '요청 오류를 해결한 뒤 같은 Point에서 경로 검증을 다시 실행하세요. 실행 실패를 차폐 판정이나 빛샘 없음으로 해석하지 않습니다.']}
  if (valid) return {title:'동일 센서 광량 비교',steps:[
    '저장된 대표 경로가 현재 광원·재질·차폐·Receiver 조건을 통과했습니다. 경로 한 개의 유효성이지 순방향 검출 수나 광량 일치를 보장하지 않습니다.',
    '검증한 위치·크기·수용각·ROI를 유지하고 광량 비교를 실행하세요. F/R의 검출 수·유효표본·독립 회차와 차이 구간을 확인합니다.',
    '순방향이 0이거나 표본이 부족하면 해당 방향 표본을 늘립니다. 같은 센서를 유지해도 반복해서 0이면 센서 조건과 표본 추출 방식을 점검하고 검증 JSON을 보존하세요.',
  ]}
  const action: Record<string,string> = {
    occluded_segment:'CAD·Block·해석 제외 설정과 경로의 막힌 구간을 확인한 뒤 Preview를 다시 실행하세요. 화면 Hide는 물리적 차폐를 제거하지 않습니다.',
    source_covered_by_blocker:'광원 면과 겹친 Block 위치를 확인하고 의도한 배치를 복원한 뒤 다시 검증하세요.',
    source_position_or_emission_mismatch:'선택 광원·발광면·법선·방출 방향·양의 출력을 확인하고 Preview와 정밀해석 조건을 맞추세요.',
    reflection_material_mismatch:'해당 반사면의 재질·BSDF·반사 방향 설정을 확인하세요. 통과시키기 위해 반사율을 임의로 높이지 마세요.',
    reflection_depth_mismatch:'저장 경로의 반사 횟수와 정밀해석 Max reflections를 비교하고 필요한 경로를 추적할 수 있게 맞추세요.',
    reflection_disabled:'반사 계산 설정과 선택한 재질을 확인하고 의도한 반사 모델을 켠 뒤 다시 검증하세요.',
    receiver_position_direction_acceptance_or_roi_mismatch:'검출 시야각·Receiver 위치·크기·수용각·ROI를 확인하세요. Point 거리 0mm는 면과 겹칠 수 있습니다. 센서를 이동하거나 확대하면 측정 조건이 달라집니다.',
    reflection_vertex_off_surface:'형상·변환·정밀 Mesh가 바뀌었는지 확인하고 Preview를 다시 실행해 경로를 갱신하세요.',
    visible_endpoint_off_surface:'검출면과 현재 정밀 형상이 일치하는지 확인하고 Preview를 다시 실행하세요.',
  }
  return {title:'경로 조건 수정 후 재검증',steps:[action[reason] ?? '저장 경로·선택 Point·현재 CAD 조건을 확인하고 Preview 결과를 갱신한 뒤 경로를 다시 검증하세요.',
    '경로 검증을 통과한 뒤 동일 센서 광량 비교로 진행하세요. 표본 수를 늘리는 것만으로 경로 불일치를 해결할 수는 없습니다.']}
}

function nextSamples(options: SensorComparisonOptions, f: boolean, r: boolean, moreBatches=false): ValidationAdvice['samples'] {
  const batches = moreBatches ? 16 : options.batches
  const oldF=options.forward_samples, oldR=options.reverse_samples
  if (![4,8,16].includes(batches) || !Number.isSafeInteger(oldF) || !Number.isSafeInteger(oldR) || oldF<32 || oldR<32 || oldF>250000 || oldR>100000) return
  if (moreBatches) return options.batches < 16 && batches*(oldF+oldR)<=4000000
    ? {batches,forward_samples:oldF,reverse_samples:oldR} : undefined
  const df=f ? Math.min(250000,oldF*4)-oldF : 0, dr=r ? Math.min(100000,oldR*4)-oldR : 0
  const free=Math.floor(4000000/batches)-oldF-oldR
  if (df+dr<=0 || free<=0) return
  const scale=Math.min(1,free/(df+dr))
  const forward_samples=oldF+Math.floor(df*scale), reverse_samples=oldR+Math.floor(dr*scale)
  if (forward_samples===oldF && reverse_samples===oldR) return
  return {batches,forward_samples,reverse_samples}
}

export function comparisonValidationAdvice(report: SensorComparisonReport, sameSensor: boolean): ValidationAdvice {
  if (!sameSensor) return {title:'현재 조건으로 다시 비교',steps:[
    '이전 작업 결과입니다. 현재 선택 센서·장면과 일치하는 결과인지 확인한 뒤 현재 조건으로 비교를 시작하세요. 이전 결과의 표본 진단을 현재 센서의 검증으로 사용하지 않습니다.']}
  if (report.reason==='invalid_reference_path') return pathValidationAdvice(false,'unknown')
  if (report.reason==='stopped' || report.reason==='incomplete_batches') return {title:'독립 반복 완료 후 판정',steps:[
    '중지되거나 반복이 끝나지 않은 결과입니다. 같은 센서와 표본 설정으로 비교를 다시 시작하고 설정된 독립 반복을 완료하세요.']}
  if (report.reason.includes('gpu_execution') || report.gpu_execution_proven !== true) return {title:'GPU 실행 증거 확인',steps:[
    'CUDA 실행이 확인되지 않아 광량 비교를 확정할 수 없습니다. 오류 사유·Compute 상태·CUDA 성공 수를 확인하세요. CPU 대체 실행을 GPU 검증 성공으로 해석하지 않습니다.',
    '실행 문제를 해결한 뒤 같은 센서 조건으로 다시 비교하세요. 표본 수 증가나 허용 오차 확대는 실행 증거를 대신하지 않습니다.']}
  if (report.verdict==='match') return {title:'정밀해석·종료 조건 확인',steps:[
    '이번 센서·표본·허용 범위에서 차이 구간 전체가 허용 범위 안입니다. 실물 밝기 보정이나 모든 빛샘 경로의 검증을 뜻하지 않습니다.',
    '기존 종료 조건 결과도 함께 확인하고 같은 설정으로 정밀해석을 진행하세요. 센서 위치·크기·수용각·ROI·광원·재질을 바꾸면 다시 검증해야 합니다.']}
  if (report.verdict==='mismatch') return {title:'조건·구현 점검 후 재검증',steps:[
    '이번 표본에서는 차이 구간이 허용 범위 밖입니다. 허용 차이를 넓혀 통과시키지 말고 검증 JSON을 보존하세요.',
    '같은 센서·ROI·광원·재질·반사 한도인지 확인하고 경로 검증과 기존 종료 조건 결과를 대조하세요.',
    '조건이 같은데도 차이가 지속되면 구현·표본 추출을 조사해야 합니다. 어느 방향이 실물 정답인지는 이 결과만으로 판단하지 않습니다.']}
  const finite=(value: number|undefined): value is number => value!==undefined && Number.isFinite(value) && value>=0
  const fMissing=!finite(report.forward_effective_samples)||!finite(report.forward_hits)
  const rMissing=!finite(report.reverse_effective_samples)||!finite(report.reverse_positive_samples)
  if (fMissing||rMissing) return {title:'표본 진단 확인 후 재비교',steps:[
    '방향별 유효표본 또는 검출 수가 없어 증가시킬 쪽을 확정할 수 없습니다. 현재 버전으로 다시 비교하고 검증 JSON에서 누락된 진단을 확인하세요.']}
  const fLow=report.forward_effective_samples!<30 || report.forward_hits!<30
  const rLow=report.reverse_effective_samples!<30 || report.reverse_positive_samples!<30
  const sparse=report.reason==='sparse_independent_batches'
  const samples=nextSamples(report.options, fLow||!rLow, rLow||!fLow, sparse)
  if (!samples) return {title:'예산 상한 · 경로·표본 분포 점검',steps:[
    '권장 증가값을 회당 순방향 250,000·역추적 100,000 및 총 4,000,000 표본 안에서 더 설정할 수 없습니다. 무한 반복하거나 판정 기준을 완화하지 않습니다.',
    '회차별 양수 비중·유효표본·차이 구간과 경로 검증을 확인하고 검증 JSON을 저장하세요. 센서나 표본 추출을 바꿔 시험하면 별도 조건의 새 비교로 취급합니다.']}
  const side=sparse ? '독립 반복' : fLow&&rLow ? 'F/R 표본' : fLow ? 'F 표본' : rLow ? 'R 표본' : 'F/R 표본'
  return {title:`${side} 증가 후 재비교`,samples,steps:[
    `현재 F/R 유효표본은 ${report.forward_effective_samples!.toFixed(1)} / ${report.reverse_effective_samples!.toFixed(1)}입니다. 유효표본은 발사 수·Hit 수와 다릅니다. 30은 최소 판정 조건이며 통과나 충분한 정확도를 보장하지 않습니다.`,
    `다음 설정: 독립 반복 ${samples.batches}회 · 회당 F ${samples.forward_samples.toLocaleString()} / R ${samples.reverse_samples.toLocaleString()}. ${sparse ? '양수 회차가 적으므로 독립 반복을 늘립니다.' : '부족한 방향의 표본을 우선 늘립니다.'}`,
    '센서 위치·크기·시야각·수용각·ROI·재질·광원·허용 차이는 유지하세요. 설정 적용 후 비교 시작을 눌러 재실행합니다.',
    '다음 결과에서 유효표본 증가·회차별 양수 분포·추정 광량과 차이 구간의 안정성을 확인하세요. 증가시켜도 개선이 없으면 Ray 수만 계속 늘리지 말고 경로·센서 조건과 표본 추출을 점검하고 검증 JSON을 보존하세요. 0 검출은 빛샘 없음의 증거가 아닙니다.',
  ]}
}
