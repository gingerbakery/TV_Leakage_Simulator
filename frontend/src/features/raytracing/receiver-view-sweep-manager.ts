import { useRef, useState } from 'react'
import type { ScenePayload } from '@/api'
import { workspaceStore } from '@/stores'
import type { BatchRayTraceState } from './batch-ray-tracing'
import { createReceiverViewSweepController, type ReceiverViewReport } from './receiver-view-sweep'

export function useReceiverViewSweepManager() {
  const [open, setOpen] = useState(false)
  const [reportOpen, setReportOpen] = useState(false)
  const [state, setState] = useState<BatchRayTraceState | null>(null)
  const [reports, setReports] = useState<ReceiverViewReport[]>([])
  const [scene, setScene] = useState<ScenePayload | undefined>(undefined)
  const [error, setError] = useState('')
  const [stopping, setStopping] = useState(false)
  const controller = useRef<ReturnType<typeof createReceiverViewSweepController> | null>(null)
  if (!controller.current) controller.current = createReceiverViewSweepController({ store: workspaceStore,
    onStateChange: (next, nextReports) => { setState(next); setReports(nextReports) } })
  return { open, setOpen, reportOpen, setReportOpen, state, reports, error, stopping, scene,
    isRunning: state?.status === 'running',
    async run(receiverId: string, scene: ScenePayload) {
      if (controller.current!.isRunning()) { setOpen(true); return }
      setError(''); setState(null); setReports([]); setScene(scene); setOpen(true)
      try { await controller.current!.run(receiverId, scene) }
      catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    },
    async cancel() {
      setStopping(true)
      try { await controller.current!.cancel() }
      catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
      finally { setStopping(false) }
    },
  }
}
