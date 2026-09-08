import { useEffect, useRef, useState } from 'react'
import { useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiClient, sceneQueryOptions, type LeakageApiClient } from '@/api'
import { workspaceStore, type WorkspaceStoreApi } from '@/stores'
import { buildRayTraceRequest, createRayTraceResultSourceContext, registerPendingRayTraceResultSourceContext } from '@/features/raytracing'
import { compareBitsamProjectScene, parseBitsamProject } from './bitsam-project'

interface DemoCase { id: string; cadName: string; cadUrl: string; projectUrl: string }
export interface HorizontalSeamDemoRun { caseId: string; jobId: string }
export type HorizontalSeamDemoState =
  | { stage: 'inactive' }
  | { stage: 'loading'; message: string }
  | { stage: 'running'; run: HorizontalSeamDemoRun }
  | { stage: 'failed'; message: string }

export function isHorizontalSeamDemo(search: string): boolean {
  return new URLSearchParams(search).get('demo') === 'horizontal-seam'
}

function readDefaultDemoCase(value: unknown): DemoCase {
  if (!value || typeof value !== 'object') throw new Error('시연 모델 목록을 읽을 수 없습니다.')
  const manifest = value as { schema?: unknown; defaultCase?: unknown; cases?: unknown }
  if (manifest.schema !== 'horizontal-seam-demo.v1' || manifest.defaultCase !== '16x' || !Array.isArray(manifest.cases)) {
    throw new Error('지원하지 않는 시연 모델 목록입니다.')
  }
  const item = manifest.cases.find((entry: unknown) => entry && typeof entry === 'object' && (entry as DemoCase).id === '16x') as DemoCase | undefined
  // This explicit local demo cannot turn manifest contents into arbitrary fetches.
  if (!item || item.cadName !== 'horizontal_gap_0p3_16x.step' ||
    item.cadUrl !== '/outputs/demo-horizontal-gap-0p3-16x.step' ||
    item.projectUrl !== '/outputs/demo-horizontal-gap-0p3-16x.bitsam') {
    throw new Error('시연 모델 파일 경로가 올바르지 않습니다.')
  }
  return item
}

export async function prepareHorizontalSeamDemo({
  queryClient, signal, onMessage,
  store = workspaceStore, client = apiClient, fetchFile = fetch,
}: {
  queryClient: QueryClient
  signal: AbortSignal
  onMessage(message: string): void
  store?: WorkspaceStoreApi
  client?: LeakageApiClient
  fetchFile?: typeof fetch
}): Promise<HorizontalSeamDemoRun> {
  let caseId: string | null = null
  const ensureCurrent = () => {
    signal.throwIfAborted()
    if (caseId && store.getState().activeCadCaseId !== caseId) {
      throw new Error('다른 Case를 선택하여 자동 시연을 중단했습니다.')
    }
  }
  const readFile = async (url: string) => {
    const response = await fetchFile(url, { signal, cache: 'no-store' })
    if (!response.ok) throw new Error('시연 모델을 불러오지 못했습니다. 서버 연결을 확인해 주세요.')
    return response
  }
  onMessage('가로 틈 0.3 mm 시연 모델을 불러오는 중입니다.')
  const item = readDefaultDemoCase(await (await readFile('/outputs/horizontal-seam-demo.json')).json())
  const [cadResponse, projectResponse] = await Promise.all([readFile(item.cadUrl), readFile(item.projectUrl)])
  const [cadBlob, projectText] = await Promise.all([cadResponse.blob(), projectResponse.text()])
  const project = parseBitsamProject(projectText)
  if (project.workspace.rayTraceConfig.compute_backend !== 'cpu' || project.workspace.rayTraceConfig.auto_convergence) {
    throw new Error('시연용 CPU 해석 설정을 확인할 수 없습니다.')
  }
  ensureCurrent()
  const uploaded = await client.uploadCad(cadBlob, item.cadName, { signal })
  ensureCurrent()
  store.getState().actions.addCadCase({ path: uploaded.path, displayName: uploaded.display_name })
  caseId = store.getState().activeCadCaseId
  if (!caseId) throw new Error('시연 Case를 만들지 못했습니다.')
  const scene = await queryClient.fetchQuery(sceneQueryOptions(uploaded.path, client))
  ensureCurrent()
  const compatibility = compareBitsamProjectScene(project, scene, store.getState().activeCad!)
  if (!compatibility.compatible) throw new Error('시연 CAD와 저장된 해석 설정의 형상이 일치하지 않습니다.')
  store.getState().actions.restoreProjectState(project.workspace)
  const state = store.getState()
  const request = buildRayTraceRequest({ scene, projectName: uploaded.display_name,
    emitters: state.emitters, receivers: state.receivers,
    materialAssignments: state.materialAssignments, transformRules: state.transformRules,
    excludedComponentIds: state.excludedComponentIds, deletedComponentIds: state.deletedComponentIds,
    roiScopes: state.roiScopes, config: state.rayTraceConfig })
  if (!request.emitters.some((emitter) => emitter.enabled) || !request.receivers.some((receiver) => receiver.enabled)) {
    throw new Error('시연 광원과 관측면을 확인할 수 없습니다.')
  }
  const context = createRayTraceResultSourceContext(scene, request, caseId)
  onMessage('모델의 광원과 재질 설정으로 빛의 이동을 계산하고 있습니다.')
  // Keep the start response observable: if unmounted during POST, stop its known job.
  const job = await client.startRayTrace(request)
  try { ensureCurrent() } catch (error) {
    await client.stopRayTrace(job.job_id).catch(() => undefined)
    throw error
  }
  registerPendingRayTraceResultSourceContext(job.job_id, context)
  store.getState().actions.setActiveRayTraceJobId(job.job_id)
  return { caseId, jobId: job.job_id }
}

export function useHorizontalSeamDemo(search = window.location.search): HorizontalSeamDemoState {
  const enabled = isHorizontalSeamDemo(search)
  const queryClient = useQueryClient()
  const [state, setState] = useState<HorizontalSeamDemoState>(() => enabled
    ? { stage: 'loading', message: '빛샘 3D 시연을 준비하고 있습니다.' } : { stage: 'inactive' })
  const started = useRef(false)
  const controller = useRef<AbortController | null>(null)
  const run = useRef<HorizontalSeamDemoRun | null>(null)
  const disposeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (!enabled) return
    if (disposeTimer.current !== null) clearTimeout(disposeTimer.current)
    if (!started.current) {
      started.current = true
      const abort = new AbortController()
      controller.current = abort
      void prepareHorizontalSeamDemo({ queryClient, signal: abort.signal,
        onMessage: (message) => { if (!abort.signal.aborted) setState({ stage: 'loading', message }) },
      }).then((prepared) => {
        run.current = prepared
        if (!abort.signal.aborted) setState({ stage: 'running', run: prepared })
      }).catch((error: unknown) => {
        if (!abort.signal.aborted) setState({ stage: 'failed', message: error instanceof Error ? error.message : '시연을 시작하지 못했습니다.' })
      })
    }
    return () => {
      // StrictMode cleanup/setup is synchronous; a real unmount cancels once.
      disposeTimer.current = setTimeout(() => {
        controller.current?.abort()
        if (run.current) void apiClient.stopRayTrace(run.current.jobId).catch(() => undefined)
      }, 0)
    }
  }, [enabled, queryClient])
  return state
}
