import { useRef, useState } from 'react'
import {
  Box,
  CircleDot,
  Maximize2,
  Rotate3D,
  Target,
} from 'lucide-react'

import {
  AppDialog,
  ComponentContextMenu,
  ConfirmationDialog,
  type ComponentContextAction,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

const cameraPresets = ['Fit', 'Iso', 'XY', '-XY'] as const
const renderModes = ['Wireframe', 'Surface', 'Surface + Edge'] as const

export function ViewerWorkspace() {
  const [cameraPreset, setCameraPreset] =
    useState<(typeof cameraPresets)[number]>('Iso')
  const [renderMode, setRenderMode] =
    useState<(typeof renderModes)[number]>('Wireframe')
  const [componentVisible, setComponentVisible] = useState(true)
  const [componentTraceable, setComponentTraceable] = useState(true)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [noticeDialog, setNoticeDialog] = useState<{
    title: string
    description: string
  } | null>(null)
  const [statusMessage, setStatusMessage] = useState(
    '레이아웃 셸 준비 완료 · CAD scene 연결 대기 중',
  )
  const componentTargetRef = useRef<HTMLDivElement>(null)

  const handleContextAction = (action: ComponentContextAction) => {
    if (action === 'visibility') {
      setComponentVisible((visible) => !visible)
      setStatusMessage(
        componentVisible
          ? 'Display Panel A를 숨겼습니다.'
          : 'Display Panel A를 표시했습니다.',
      )
      return
    }

    if (action === 'traceability') {
      setComponentTraceable((traceable) => !traceable)
      setStatusMessage(
        componentTraceable
          ? 'Display Panel A를 해석 대상에서 제외했습니다.'
          : 'Display Panel A를 해석 대상에 포함했습니다.',
      )
      return
    }

    if (action === 'delete') {
      setDeleteDialogOpen(true)
      return
    }

    const isMaterial = action === 'material'
    setNoticeDialog({
      title: isMaterial ? 'Material assignment' : 'Transform input',
      description: isMaterial
        ? '공통 Context Menu 연결은 완료되었습니다. 실제 Material 편집 상태는 7단계에서 이 Dialog에 연결합니다.'
        : '공통 Context Menu 연결은 완료되었습니다. 실제 move·tilt 편집 상태는 7단계에서 이 Dialog에 연결합니다.',
    })
  }

  return (
    <>
      <main className="flex min-h-[42rem] min-w-0 flex-col bg-sim-viewer lg:min-h-0">
        <div className="border-b border-border bg-background/65 px-3 py-2.5">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h1 className="text-sm font-semibold">3D Viewer</h1>
              <p className="text-[0.7rem] text-muted-foreground">
                CAD workspace shell · viewer engine connection pending
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
                    variant={cameraPreset === preset ? 'secondary' : 'ghost'}
                    aria-pressed={cameraPreset === preset}
                    onClick={() => {
                      setCameraPreset(preset)
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
            </div>
          </div>
        </div>

        <div className="grid grid-cols-3 border-b border-border bg-background/40">
          {[
            ['Face', '0'],
            ['Vertex', '0'],
            ['Mode', renderMode],
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
            <div
              className="pointer-events-none absolute inset-0 opacity-20"
              aria-hidden="true"
            >
              <div className="absolute inset-0 bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] bg-[size:32px_32px]" />
            </div>
            <div className="absolute top-3 left-3 flex items-center gap-2">
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
            </div>

            <ComponentContextMenu
              componentName="Display Panel A"
              visible={componentVisible}
              traceable={componentTraceable}
              onAction={handleContextAction}
            >
              <div
                ref={componentTargetRef}
                data-testid="component-context-target"
                tabIndex={0}
                className={cn(
                  'group relative z-10 flex w-[min(28rem,calc(100%-2rem))] select-none flex-col items-center rounded-2xl border border-primary/35 bg-card/70 px-6 py-10 text-center shadow-2xl shadow-black/30 outline-none backdrop-blur transition-all focus-visible:ring-2 focus-visible:ring-primary',
                  !componentVisible && 'opacity-45 grayscale',
                )}
              >
                <div className="flex size-16 items-center justify-center rounded-2xl border border-primary/30 bg-primary/10 text-primary shadow-inner">
                  <Box className="size-8" aria-hidden="true" />
                </div>
                <div className="mt-4 text-base font-semibold">
                  Display Panel A
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {componentVisible ? 'Visible' : 'Hidden'} ·{' '}
                  {componentTraceable
                    ? 'Traceability on'
                    : 'Traceability off'}
                </div>
                <div className="mt-5 flex items-center gap-2 rounded-full border border-border bg-background/65 px-3 py-1.5 text-[0.7rem] text-muted-foreground">
                  <Target className="size-3.5 text-selection" />
                  Right click for component actions
                </div>
              </div>
            </ComponentContextMenu>
          </div>
        </div>

        <footer className="flex min-h-9 items-center justify-between gap-3 border-t border-border bg-background/55 px-3 py-2 text-[0.68rem] text-muted-foreground">
          <span className="truncate">{statusMessage}</span>
          <span className="hidden shrink-0 items-center gap-1 sm:flex">
            <CircleDot className="size-3 text-primary" />
            Layout · Dialog · Context Menu
          </span>
        </footer>
      </main>

      <AppDialog
        open={noticeDialog !== null}
        onOpenChange={(open) => {
          if (!open) setNoticeDialog(null)
        }}
        title={noticeDialog?.title ?? 'Migration notice'}
        description={noticeDialog?.description}
        returnFocusRef={componentTargetRef}
        footer={
          <Button variant="outline" onClick={() => setNoticeDialog(null)}>
            Close
          </Button>
        }
      >
        <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-5 text-muted-foreground">
          공통 Dialog는 focus trap, Escape 닫기, 배경 interaction 차단을 Radix
          계층에서 처리합니다.
        </div>
      </AppDialog>

      <ConfirmationDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        title="Delete Display Panel A?"
        description="이 확인 패턴은 7단계에서 실제 Component 삭제 action에 연결됩니다. 현재는 레이아웃 검증용 preview만 유지합니다."
        confirmLabel="Delete preview"
        cancelLabel="Cancel"
        destructive
        returnFocusRef={componentTargetRef}
        onConfirm={() =>
          setStatusMessage('Delete 확인 Dialog 동작을 검증했습니다.')
        }
      />
    </>
  )
}
