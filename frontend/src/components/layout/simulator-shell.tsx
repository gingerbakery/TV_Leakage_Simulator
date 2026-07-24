import { useRef, useState } from 'react'
import { useSceneQuery } from '@/api'
import { Box, CircleDot, Info } from 'lucide-react'

import { AppDialog, ConfirmationDialog } from '@/components/common'
import { ViewerWorkspace } from '@/components/layout/viewer-workspace'
import {
  WorkflowSidebar,
  type WorkflowSectionId,
} from '@/components/layout/workflow-sidebar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ComponentEditorRequest } from '@/features/components'
import { getComponentDisplayName } from '@/features/components'
import { MaterialEditorDialog } from '@/features/materials'
import type { ViewerCameraFrame } from '@/features/raytracing'
import { TransformEditorDialog } from '@/features/transforms'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

type ComponentDialogType = 'material' | 'transform' | 'delete'

export function SimulatorShell() {
  const [activeSection, setActiveSection] =
    useState<WorkflowSectionId>('components')
  const [viewerCameraFrame, setViewerCameraFrame] =
    useState<ViewerCameraFrame | null>(null)
  const [componentDialog, setComponentDialog] = useState<{
    type: ComponentDialogType
    componentId: number
  } | null>(null)
  const [noticeDialog, setNoticeDialog] = useState<{
    title: string
    description: string
  } | null>(null)
  const noticeReturnFocusRef = useRef<HTMLElement>(null)
  const componentReturnFocusRef = useRef<HTMLElement>(null)
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const nameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const sceneQuery = useSceneQuery(activeCad?.path ?? '')
  const scene = sceneQuery.data
  const sceneErrorMessage = sceneQuery.error?.message
  const activeComponent =
    scene?.components.find(
      (component) =>
        component.component_id === componentDialog?.componentId,
    ) ?? null
  const activeComponentName = activeComponent
    ? getComponentDisplayName(activeComponent, nameOverrides)
    : ''

  const openFeatureNotice = (title: string, description: string) => {
    if (document.activeElement instanceof HTMLElement) {
      noticeReturnFocusRef.current = document.activeElement
    }
    setNoticeDialog({ title, description })
  }

  const openComponentDialog = (
    type: ComponentDialogType,
    request: ComponentEditorRequest,
  ) => {
    componentReturnFocusRef.current = request.returnFocusElement
    setComponentDialog({
      type,
      componentId: request.componentId,
    })
  }

  return (
    <div className="grid min-h-svh bg-background text-foreground lg:h-svh lg:grid-rows-[3.25rem_minmax(0,1fr)] lg:overflow-hidden">
      <header className="sticky top-0 z-30 flex h-13 items-center justify-between border-b border-border bg-background/92 px-3 backdrop-blur-xl lg:static lg:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary">
            <Box className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">
              TV Leakage Simulator
            </div>
            <div className="hidden text-[0.68rem] text-muted-foreground sm:block">
              React workspace shell
            </div>
          </div>
          <Badge
            variant="outline"
            className="hidden border-primary/30 bg-primary/10 text-primary md:inline-flex"
          >
            Migration · Features 10
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
            <CircleDot className="size-3 text-primary" aria-hidden="true" />
            API layer ready
          </div>
          <Button
            variant="outline"
            size="sm"
            aria-label="Layout guide"
            onClick={() =>
              openFeatureNotice(
                'Feature migration boundary',
                'ROI 박스 드래그와 좌표 입력, 다중 scope 활성화, 정밀 mesh clipping과 폐곡선 section cap이 React Viewer에 연결되었습니다.',
              )
            }
          >
            <Info data-icon="inline-start" />
            <span className="hidden sm:inline">Layout guide</span>
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <WorkflowSidebar
          activeSection={activeSection}
          onActiveSectionChange={setActiveSection}
          scene={scene}
          cameraFrame={viewerCameraFrame}
          isSceneLoading={sceneQuery.isPending && activeCad !== null}
          sceneErrorMessage={sceneErrorMessage}
          onEditMaterial={(request) =>
            openComponentDialog('material', request)
          }
          onEditTransform={(request) =>
            openComponentDialog('transform', request)
          }
          onDeleteComponent={(request) =>
            openComponentDialog('delete', request)
          }
        />
        <ViewerWorkspace
          scene={scene}
          isSceneLoading={sceneQuery.isPending && activeCad !== null}
          sceneErrorMessage={sceneErrorMessage}
          onCameraFrameChange={setViewerCameraFrame}
          onEditMaterial={(request) =>
            openComponentDialog('material', request)
          }
          onEditTransform={(request) =>
            openComponentDialog('transform', request)
          }
          onDeleteComponent={(request) =>
            openComponentDialog('delete', request)
          }
        />
      </div>

      <AppDialog
        open={noticeDialog !== null}
        onOpenChange={(open) => {
          if (!open) setNoticeDialog(null)
        }}
        title={noticeDialog?.title ?? 'Migration notice'}
        description={noticeDialog?.description}
        returnFocusRef={noticeReturnFocusRef}
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

      <MaterialEditorDialog
        open={componentDialog?.type === 'material'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        component={activeComponent}
        componentName={activeComponentName}
        returnFocusRef={componentReturnFocusRef}
      />

      <TransformEditorDialog
        open={componentDialog?.type === 'transform'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        component={activeComponent}
        componentName={activeComponentName}
        returnFocusRef={componentReturnFocusRef}
      />

      <ConfirmationDialog
        open={componentDialog?.type === 'delete'}
        onOpenChange={(open) => {
          if (!open) setComponentDialog(null)
        }}
        title={`Delete ${activeComponentName || 'component'}?`}
        description="Viewer와 ray tracing 대상에서 제외하며 연결된 Material assignment와 Transform rule도 함께 정리합니다. CAD를 다시 Import하면 복원됩니다."
        confirmLabel="Delete component"
        cancelLabel="Cancel"
        destructive
        returnFocusRef={componentReturnFocusRef}
        onConfirm={() => {
          if (!activeComponent) return
          actions.deleteComponent(
            activeComponent.component_id,
            activeComponent.face_indices,
          )
          setComponentDialog(null)
        }}
      />
    </div>
  )
}
