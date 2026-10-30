import { apiClient, type ReceiverSpec, type ScenePayload, type RayTraceResult } from '@/api'
import { createWorkspaceStore, snapshotCadCasesForRayTrace, type WorkspaceStoreApi, type CadCase } from '@/stores'
import { createBatchRayTracingController, type BatchRayTraceState, type BatchRayTracingControllerOptions } from './batch-ray-tracing'
import { expandReceiverView } from './receiver-view'

export interface ReceiverViewReport {
  caseId: string
  name: string
  cadName: string
  result: RayTraceResult
}
export interface ReceiverViewSweepOptions {
  store: WorkspaceStoreApi
  client?: BatchRayTracingControllerOptions['client']
  pollIntervalMs?: number
  onStateChange?(state: BatchRayTraceState, reports: ReceiverViewReport[]): void
}

/** Isolated virtual Cases reuse the existing serialized, cancellable Batch runner. */
export function createReceiverViewSweepController(options: ReceiverViewSweepOptions) {
  let controller: ReturnType<typeof createBatchRayTracingController> | null = null
  let running = false
  return {
    isRunning: () => running,
    cancel: async () => { await controller?.cancel() },
    async run(receiverId: string, scene: ScenePayload) {
      const state = options.store.getState()
      if (running || state.singleRayTraceRun || state.batchRayTraceActive) throw new Error('다른 해석이 실행 중입니다.')
      const [source] = snapshotCadCasesForRayTrace(state, state.activeCadCaseId ? [state.activeCadCaseId] : [])
      if (!source) throw new Error('활성 Case가 없습니다.')
      const enabled = source.workspaceState.receivers.filter((receiver) => receiver.enabled)
      const template = enabled.find((receiver) => receiver.receiver_id === receiverId)
      if (!template?.view_angles || enabled.length !== 1) throw new Error('각도별 해석에는 시야각 Receiver 하나만 활성화하세요.')
      const views = expandReceiverView(template, scene, source.workspaceState.transformRules)
      const staged = createWorkspaceStore()
      const sweepId = crypto.randomUUID()
      const cases: CadCase[] = views.map((receiver: ReceiverSpec, index) => ({
        caseId: `view-${sweepId}-${index}`, order: index, cad: structuredClone(source.cad), visible: true,
        name: `${receiver.view_angles!.horizontal_deg}° / ${receiver.view_angles!.vertical_deg}°`,
        accessoryCads: structuredClone(source.accessoryCads), leakPreviewState: structuredClone(source.leakPreviewState),
        workspaceState: { ...structuredClone(source.workspaceState), receivers: [receiver] },
      }))
      staged.setState({ cadCases: cases, activeCadCaseId: null, activeCad: null })
      const client = options.client ?? apiClient
      const reports = (): ReceiverViewReport[] => staged.getState().cadCases.flatMap((item) => item.latestResult ? [{
        caseId: item.caseId, name: item.name!, cadName: source.cad.displayName, result: item.latestResult,
      }] : [])
      controller = createBatchRayTracingController({
        store: staged, pollIntervalMs: options.pollIntervalMs,
        client: { ...client, startRayTrace: async (request, requestOptions) => {
          if (options.store.getState().actions.validateCadCaseRayTraceSetup(source.caseId, source.setupHash) !== 'current') throw new Error('Case 조건이 변경되어 이후 각도 실행을 중단합니다.')
          return client.startRayTrace(request, requestOptions)
        } },
        onStateChange: (next) => options.onStateChange?.(next, reports()),
      })
      running = true
      state.actions.setBatchRayTraceActive(true)
      try { return await controller.run(cases.map((item) => item.caseId)) }
      finally { running = false; controller = null; options.store.getState().actions.setBatchRayTraceActive(false) }
    },
  }
}
