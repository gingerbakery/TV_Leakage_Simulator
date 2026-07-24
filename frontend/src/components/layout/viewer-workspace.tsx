import { lazy, Suspense, useState } from 'react'
import type { ScenePayload } from '@/api'
import {
  BoxSelect,
  CircleDot,
  FileBox,
  LoaderCircle,
  Maximize2,
  Rotate3D,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type {
  ViewerCameraPreset,
  ViewerRenderMode,
} from '@/features/viewer'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

const cameraPresets: ViewerCameraPreset[] = [
  'Fit',
  'Iso',
  'XY',
  '-XY',
]
const renderModes: ViewerRenderMode[] = [
  'Wireframe',
  'Surface',
  'Surface + Edge',
]

const ThreeViewerCanvas = lazy(() =>
  import('@/features/viewer').then((module) => ({
    default: module.ThreeViewerCanvas,
  })),
)

interface ViewerWorkspaceProps {
  scene?: ScenePayload
  isSceneLoading?: boolean
  sceneErrorMessage?: string
}

export function ViewerWorkspace({
  scene,
  isSceneLoading = false,
  sceneErrorMessage,
}: ViewerWorkspaceProps) {
  const [cameraPreset, setCameraPreset] =
    useState<ViewerCameraPreset>('Iso')
  const [cameraRequestId, setCameraRequestId] = useState(0)
  const [renderMode, setRenderMode] =
    useState<ViewerRenderMode>('Surface + Edge')
  const [axisScalePercent, setAxisScalePercent] = useState(100)
  const [statusMessage, setStatusMessage] = useState(
    'CAD를 Import하면 Three.js Viewer에서 component와 face를 선택할 수 있습니다.',
  )
  const selectedComponentIds = useWorkspaceStore(
    workspaceSelectors.selectedComponentIds,
  )
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )

  const components = (scene?.components ?? []).filter(
    (component) =>
      !deletedComponentIds.includes(component.component_id),
  )
  const visibleComponentCount = components.filter(
    (component) =>
      !hiddenComponentIds.includes(component.component_id),
  ).length

  return (
    <main className="flex min-h-[42rem] min-w-0 flex-col bg-sim-viewer lg:min-h-0">
      <div className="border-b border-border bg-background/65 px-3 py-2.5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-sm font-semibold">3D Viewer</h1>
            <p className="text-[0.7rem] text-muted-foreground">
              Three.js mesh · component and face picking · Step 08
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <div
              className="flex items-center gap-1 rounded-lg border border-border bg-background/60 p-1"
              aria-label="Camera presets"
            >
              {cameraPresets.map((preset) => (
                <Button
                  key={preset}
                  size="xs"
                  variant={
                    cameraPreset === preset ? 'secondary' : 'ghost'
                  }
                  aria-pressed={cameraPreset === preset}
                  onClick={() => {
                    setCameraPreset(preset)
                    setCameraRequestId((requestId) => requestId + 1)
                    setStatusMessage(`Camera preset · ${preset}`)
                  }}
                >
                  {preset === 'Fit' ? (
                    <Maximize2 aria-hidden="true" />
                  ) : null}
                  {preset}
                </Button>
              ))}
            </div>
            <div
              className="flex items-center gap-1 rounded-lg border border-border bg-background/60 p-1"
              aria-label="Render modes"
            >
              {renderModes.map((mode) => (
                <Button
                  key={mode}
                  size="xs"
                  variant={renderMode === mode ? 'secondary' : 'ghost'}
                  aria-pressed={renderMode === mode}
                  onClick={() => {
                    setRenderMode(mode)
                    setStatusMessage(`Render mode · ${mode}`)
                  }}
                >
                  {mode}
                </Button>
              ))}
            </div>
            <label className="flex h-8 items-center gap-2 rounded-lg border border-border bg-background/60 px-2 text-[0.65rem] text-muted-foreground">
              <span className="font-medium whitespace-nowrap">
                Axis size
              </span>
              <input
                aria-label="Axis size"
                type="range"
                min="50"
                max="180"
                step="5"
                value={axisScalePercent}
                className="h-1.5 w-20 cursor-pointer accent-primary"
                onChange={(event) => {
                  const nextScale = Number(event.currentTarget.value)
                  setAxisScalePercent(nextScale)
                  setStatusMessage(`Axis size · ${nextScale}%`)
                }}
              />
              <span className="w-8 text-right font-semibold text-foreground">
                {axisScalePercent}%
              </span>
            </label>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 border-b border-border bg-background/40">
        {[
          ['Face', scene?.metadata.face_count.toLocaleString() ?? '0'],
          ['Vertex', scene?.metadata.vertex_count.toLocaleString() ?? '0'],
          [
            'Mode',
            scene ? (scene.metadata.synthetic ? 'Synthetic' : 'CAD') : '-',
          ],
        ].map(([label, value]) => (
          <div
            key={label}
            className="border-r border-border px-3 py-2 last:border-r-0"
          >
            <div className="text-[0.62rem] tracking-wide text-muted-foreground uppercase">
              {label}
            </div>
            <div className="mt-0.5 truncate text-xs font-semibold">
              {value}
            </div>
          </div>
        ))}
      </div>

      <div className="relative flex min-h-0 flex-1 p-3">
        <div className="relative flex min-h-[30rem] w-full items-center justify-center overflow-hidden rounded-xl border border-border bg-[radial-gradient(circle_at_center,var(--sim-panel-raised)_0,transparent_58%)] lg:min-h-0">
          <div className="pointer-events-none absolute top-3 left-3 z-10 flex items-center gap-2">
            <Badge
              variant="outline"
              className="border-border bg-background/70 text-muted-foreground backdrop-blur"
            >
              <Rotate3D data-icon="inline-start" />
              {cameraPreset}
            </Badge>
            <Badge
              variant="outline"
              className="border-border bg-background/70 text-muted-foreground backdrop-blur"
            >
              {renderMode}
            </Badge>
            {selectedFaceIds.length > 0 ? (
              <Badge className="bg-warning/15 text-warning">
                Face {selectedFaceIds[0]}
                {selectedFaceIds.length > 1
                  ? ` +${selectedFaceIds.length - 1}`
                  : ''}
              </Badge>
            ) : null}
          </div>

          {isSceneLoading ? (
            <div className="relative z-10 flex flex-col items-center text-center">
              <LoaderCircle className="size-8 animate-spin text-primary" />
              <div className="mt-3 text-sm font-semibold">
                Loading CAD scene
              </div>
              <div className="mt-1 text-xs text-muted-foreground">
                Tessellation과 component metadata를 읽는 중입니다.
              </div>
            </div>
          ) : sceneErrorMessage ? (
            <div className="relative z-10 max-w-md rounded-xl border border-destructive/35 bg-destructive/8 p-4 text-center">
              <div className="text-sm font-semibold text-destructive">
                Scene load failed
              </div>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {sceneErrorMessage}
              </p>
            </div>
          ) : !scene ? (
            <div className="relative z-10 flex max-w-sm flex-col items-center px-6 text-center">
              <span className="flex size-14 items-center justify-center rounded-2xl border border-border bg-background/50 text-muted-foreground">
                <FileBox className="size-7" />
              </span>
              <div className="mt-4 text-sm font-semibold">
                Empty workspace
              </div>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                왼쪽 Model import에서 CAD를 선택하면 실제 Three.js scene이
                생성됩니다.
              </p>
            </div>
          ) : components.length === 0 ? (
            <div className="relative z-10 flex max-w-sm flex-col items-center px-6 text-center">
              <BoxSelect className="size-8 text-muted-foreground" />
              <div className="mt-3 text-sm font-semibold">
                No active components
              </div>
              <p className="mt-1 text-xs text-muted-foreground">
                삭제 상태를 복원하려면 CAD를 다시 Import하세요.
              </p>
            </div>
          ) : (
            <Suspense
              fallback={
                <div className="relative z-10 flex flex-col items-center text-center">
                  <LoaderCircle className="size-8 animate-spin text-primary" />
                  <div className="mt-3 text-sm font-semibold">
                    Starting Three.js Viewer
                  </div>
                </div>
              }
            >
              <ThreeViewerCanvas
                scene={scene}
                axisScalePercent={axisScalePercent}
                cameraPreset={cameraPreset}
                cameraRequestId={cameraRequestId}
                renderMode={renderMode}
                onStatusMessage={setStatusMessage}
              />
            </Suspense>
          )}
        </div>
      </div>

      <footer className="flex min-h-9 items-center justify-between gap-3 border-t border-border bg-background/55 px-3 py-2 text-[0.68rem] text-muted-foreground">
        <span className="truncate">{statusMessage}</span>
        <span className="hidden shrink-0 items-center gap-1 sm:flex">
          <CircleDot className="size-3 text-primary" />
          {scene
            ? `${visibleComponentCount} visible · ${selectedComponentIds.length} component · ${selectedFaceIds.length} face`
            : 'Three.js Viewer · Step 08'}
        </span>
      </footer>
    </main>
  )
}
