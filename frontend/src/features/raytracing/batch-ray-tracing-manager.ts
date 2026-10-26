import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import {
  useWorkspaceStore,
  workspaceSelectors,
  workspaceStore,
} from '@/stores'

import {
  createBatchRayTracingController,
  type BatchRayTraceState,
} from './batch-ray-tracing'

const initialBatchState: BatchRayTraceState = {
  batchId: null,
  status: 'idle',
  currentCaseId: null,
  progress: 0,
  startedAt: null,
  completedAt: null,
  cases: [],
}

export interface BatchRayTracingManager {
  open: boolean
  state: BatchRayTraceState
  selectedCaseIds: string[]
  error: string | null
  cancelPending: boolean
  isRunning: boolean
  openDialog(caseIds: string[]): void
  setOpen(open: boolean): void
  setCaseSelected(caseId: string, selected: boolean): void
  selectCases(caseIds: string[]): void
  run(): Promise<void>
  cancel(): Promise<void>
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}

/**
 * Owns a batch controller for the lifetime of SimulatorShell. Keeping this
 * hook above the Workflow accordion lets the run continue while the Ray
 * Tracing section or its progress dialog is closed.
 */
export function useBatchRayTracingManager(): BatchRayTracingManager {
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const caseUniverse = useMemo(
    () => [...cadCases]
      .sort((left, right) => left.order - right.order)
      .map((item) => item.caseId),
    [cadCases],
  )
  const caseUniverseKey = JSON.stringify(caseUniverse)
  const [open, setOpen] = useState(false)
  const [state, setState] = useState<BatchRayTraceState>(initialBatchState)
  const [selectedCaseIds, setSelectedCaseIds] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [cancelPending, setCancelPending] = useState(false)
  const controllerRef = useRef<ReturnType<
    typeof createBatchRayTracingController
  > | null>(null)
  const currentCaseUniverseKeyRef = useRef(caseUniverseKey)
  const batchCaseUniverseKeyRef = useRef(caseUniverseKey)
  currentCaseUniverseKeyRef.current = caseUniverseKey

  if (!controllerRef.current) {
    controllerRef.current = createBatchRayTracingController({
      store: workspaceStore,
      onStateChange: setState,
    })
  }

  useEffect(() => {
    const available = new Set(caseUniverse)
    setSelectedCaseIds((current) =>
      current.filter((caseId) => available.has(caseId)),
    )
    if (
      state.status !== 'idle' &&
      state.status !== 'running' &&
      batchCaseUniverseKeyRef.current !== caseUniverseKey
    ) {
      setState(initialBatchState)
      setError(null)
      setCancelPending(false)
    }
  }, [caseUniverse, caseUniverseKey, state.status])

  const openDialog = useCallback((caseIds: string[]) => {
    setSelectedCaseIds((current) => {
      const available = new Set(caseIds)
      const retained = current.filter((caseId) => available.has(caseId))
      return retained.length > 0 ? retained : caseIds
    })
    setError(null)
    setOpen(true)
  }, [])

  const setCaseSelected = useCallback((caseId: string, selected: boolean) => {
    setSelectedCaseIds((current) => {
      if (selected) {
        return current.includes(caseId) ? current : [...current, caseId]
      }
      return current.filter((item) => item !== caseId)
    })
  }, [])

  const selectCases = useCallback((caseIds: string[]) => {
    setSelectedCaseIds([...new Set(caseIds)])
  }, [])

  const run = useCallback(async () => {
    setError(null)
    batchCaseUniverseKeyRef.current = currentCaseUniverseKeyRef.current
    try {
      await controllerRef.current!.run(selectedCaseIds)
    } catch (runError) {
      setError(errorMessage(runError))
    }
  }, [selectedCaseIds])

  const cancel = useCallback(async () => {
    setCancelPending(true)
    try {
      await controllerRef.current!.cancel()
    } catch (cancelError) {
      setError(errorMessage(cancelError))
    } finally {
      setCancelPending(false)
    }
  }, [])

  return {
    open,
    state,
    selectedCaseIds,
    error,
    cancelPending,
    isRunning: state.status === 'running',
    openDialog,
    setOpen,
    setCaseSelected,
    selectCases,
    run,
    cancel,
  }
}
