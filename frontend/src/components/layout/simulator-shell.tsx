import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from 'react'
import { useRayTraceJobQuery, useSceneQuery } from '@/api'
import {
  Box,
  CircleDot,
  FolderOpen,
  Info,
  Moon,
  Save,
  Sun,
} from 'lucide-react'

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
import {
  BitsamProjectError,
  compareBitsamProjectScene,
  createBitsamSettingsOnlyState,
  createBitsamProject,
  saveBitsamProject,
  readBitsamProjectFile,
  type BitsamProject,
} from '@/features/projects'
import type {
  RayObjectEditRequest,
  ViewerCameraFrame,
} from '@/features/raytracing'
import { TransformEditorDialog } from '@/features/transforms'
import {
  useWorkspaceStore,
  workspaceSelectors,
  workspaceStore,
} from '@/stores'

type ComponentDialogType = 'material' | 'transform' | 'delete'

export function SimulatorShell() {
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    window.localStorage.getItem('tv-leakage-theme') === 'dark'
      ? 'dark'
      : 'light',
  )
  const [activeSection, setActiveSection] =
    useState<WorkflowSectionId>('model-import')
  const [viewerCameraFrame, setViewerCameraFrame] =
    useState<ViewerCameraFrame | null>(null)
  const [rayTraceResultOpen, setRayTraceResultOpen] = useState(false)
  const [rayObjectEditRequest, setRayObjectEditRequest] =
    useState<RayObjectEditRequest | null>(null)
  const [componentDialog, setComponentDialog] = useState<{
    type: ComponentDialogType
    componentId: number
  } | null>(null)
  const [noticeDialog, setNoticeDialog] = useState<{
    title: string
    description: string
  } | null>(null)
  const [pendingProject, setPendingProject] =
    useState<BitsamProject | null>(null)
  const noticeReturnFocusRef = useRef<HTMLElement>(null)
  const componentReturnFocusRef = useRef<HTMLElement>(null)
  const projectFileInputRef = useRef<HTMLInputElement>(null)
  const projectLoadAttemptRef = useRef('')

  useEffect(() => {
    document.documentElement.classList.toggle('dark', theme === 'dark')
    document.documentElement.style.colorScheme = theme
    window.localStorage.setItem('tv-leakage-theme', theme)
  }, [theme])
  const lastOpenedResultRunIdRef = useRef('')
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const activeCadCaseVisible =
    cadCases.find((item) => item.caseId === activeCadCaseId)?.visible ?? true
  const nameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const activeRayTraceJobId = useWorkspaceStore(
    workspaceSelectors.activeRayTraceJobId,
  )
  const sceneQuery = useSceneQuery(activeCad?.path ?? '')
  const rayTraceJobQuery = useRayTraceJobQuery(activeRayTraceJobId)
  const rayTraceJob = rayTraceJobQuery.data
  const rayTraceResult =
    rayTraceJob?.status === 'completed' ? rayTraceJob.result : null
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

  useEffect(() => {
    if (!rayTraceResult) {
      return
    }
    actions.setActiveCadCaseResult(rayTraceResult)
    if (lastOpenedResultRunIdRef.current === rayTraceResult.run_id) return
    lastOpenedResultRunIdRef.current = rayTraceResult.run_id
    setActiveSection('result')
    setRayTraceResultOpen(true)
  }, [actions, rayTraceResult])

  const openFeatureNotice = (title: string, description: string) => {
    if (document.activeElement instanceof HTMLElement) {
      noticeReturnFocusRef.current = document.activeElement
    }
    setNoticeDialog({ title, description })
  }

  useEffect(() => {
    if (!pendingProject || !activeCad || !scene) return

    const compatibility = compareBitsamProjectScene(
      pendingProject,
      scene,
      activeCad,
    )
    if (!compatibility.compatible) {
      const attemptKey = `${pendingProject.saved_at}:${scene.metadata.scene_token}`
      if (projectLoadAttemptRef.current === attemptKey) return
      projectLoadAttemptRef.current = attemptKey
      const settingsOnly = createBitsamSettingsOnlyState(pendingProject)
      actions.restoreProjectState(settingsOnly.workspace)
      setPendingProject(null)
      openFeatureNotice(
        '설정 조건만 불러왔습니다',
        [
          '저장 당시 CAD와 현재 CAD의 Surface 구조가 달라 형상 연결 항목은 제외했습니다.',
          'Ray 개수, 최대 반사 횟수, 종료 조건, Stored paths 및 표시 조건을 복원했습니다.',
          `Datum emitter ${settingsOnly.restoredDatumEmitters}개 / Datum receiver ${settingsOnly.restoredDatumReceivers}개를 복원했습니다.`,
          `CAD Component·Face·ROI 연결 항목 ${settingsOnly.skippedGeometryItems}개는 잘못된 면 연결을 방지하기 위해 제외했습니다.`,
          ...compatibility.reasons.map((reason) => `불일치: ${reason}`),
        ].join('\n'),
      )
      return
    }

    actions.restoreProjectState(pendingProject.workspace)
    setPendingProject(null)
    projectLoadAttemptRef.current = ''
    openFeatureNotice(
      'BITSAM 프로젝트 불러오기 완료',
      [
        `${pendingProject.project_name} 설정을 현재 CAD에 복원했습니다.`,
        'ROI, Component 상태, Transform, Material, Emitter, Receiver와 Ray tracing 설정이 적용되었습니다.',
        ...compatibility.warnings,
      ].join('\n'),
    )
  }, [actions, activeCad, pendingProject, scene])

  const handleSaveProject = async () => {
    if (!activeCad || !scene) {
      openFeatureNotice(
        '저장할 CAD가 없습니다',
        'CAD 모델을 불러온 뒤 BITSAM 프로젝트를 저장해 주세요.',
      )
      return
    }

    try {
      const project = createBitsamProject(
        scene,
        workspaceStore.getState(),
      )
      const saveResult = await saveBitsamProject(project)
      if (saveResult === 'cancelled') return
      openFeatureNotice(
        'BITSAM 프로젝트 저장 완료',
        saveResult === 'picked'
          ? `${project.project_name}.bitsam 파일을 선택한 위치에 저장했습니다. 원본 CAD 파일은 포함되지 않으므로 함께 보관해 주세요.`
          : `${project.project_name}.bitsam 파일을 다운로드 폴더에 저장했습니다. 이 브라우저에서는 저장 위치 선택을 지원하지 않습니다.`,
      )
    } catch (error) {
      openFeatureNotice(
        'BITSAM 프로젝트 저장 실패',
        error instanceof Error
          ? error.message
          : '알 수 없는 오류가 발생했습니다.',
      )
    }
  }

  const handleLoadProject = async (
    event: ChangeEvent<HTMLInputElement>,
  ) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return

    try {
      const project = await readBitsamProjectFile(file)
      projectLoadAttemptRef.current = ''
      setPendingProject(project)
      if (!activeCad || !scene) {
        openFeatureNotice(
          'BITSAM 프로젝트를 읽었습니다',
          `${project.cad.display_name} CAD 파일을 Import하면 저장된 설정을 자동으로 복원합니다.`,
        )
      }
    } catch (error) {
      setPendingProject(null)
      openFeatureNotice(
        'BITSAM 프로젝트 불러오기 실패',
        error instanceof BitsamProjectError
          ? error.message
          : '파일을 읽는 중 알 수 없는 오류가 발생했습니다.',
      )
    }
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
            v1.0.0 · React
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <input
            ref={projectFileInputRef}
            type="file"
            accept=".bitsam,application/vnd.bitsam+json"
            className="hidden"
            aria-label="BITSAM project file"
            onChange={handleLoadProject}
          />
          <Button
            variant="outline"
            size="sm"
            aria-label="Save BITSAM project"
            disabled={!activeCad || !scene}
            title={
              activeCad && scene
                ? '현재 시뮬레이션을 .bitsam 파일로 저장'
                : 'CAD를 먼저 불러와 주세요'
            }
            onClick={handleSaveProject}
          >
            <Save data-icon="inline-start" />
            <span className="hidden sm:inline">Save</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Load BITSAM project"
            title=".bitsam 시뮬레이션 파일 불러오기"
            onClick={() => projectFileInputRef.current?.click()}
          >
            <FolderOpen data-icon="inline-start" />
            <span className="hidden sm:inline">Load</span>
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label={
              theme === 'light'
                ? 'Switch to dark mode'
                : 'Switch to light mode'
            }
            title={
              theme === 'light'
                ? 'Dark mode로 변경'
                : 'Light mode로 변경'
            }
            onClick={() =>
              setTheme((current) =>
                current === 'light' ? 'dark' : 'light',
              )
            }
          >
            {theme === 'light' ? (
              <Moon data-icon="inline-start" />
            ) : (
              <Sun data-icon="inline-start" />
            )}
            <span className="hidden xl:inline">
              {theme === 'light' ? 'Dark' : 'Light'}
            </span>
          </Button>
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
          rayTraceJob={rayTraceJob}
          rayObjectEditRequest={rayObjectEditRequest}
          onRayObjectEditRequestHandled={() =>
            setRayObjectEditRequest(null)
          }
          onOpenRayTraceResult={() => {
            if (rayTraceResult) setRayTraceResultOpen(true)
          }}
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
          cadDisplayName={activeCad?.displayName}
          scene={activeCadCaseVisible ? scene : undefined}
          isSceneLoading={sceneQuery.isPending && activeCad !== null}
          sceneErrorMessage={sceneErrorMessage}
          onCameraFrameChange={setViewerCameraFrame}
          rayTraceResult={rayTraceResult}
          rayTraceResultOpen={rayTraceResultOpen}
          onRayTraceResultOpenChange={setRayTraceResultOpen}
          editingComponentId={
            componentDialog?.type === 'material' ||
            componentDialog?.type === 'transform'
              ? componentDialog.componentId
              : null
          }
          editingComponentMode={
            componentDialog?.type === 'material' ||
            componentDialog?.type === 'transform'
              ? componentDialog.type
              : null
          }
          onEditMaterial={(request) =>
            openComponentDialog('material', request)
          }
          onEditTransform={(request) =>
            openComponentDialog('transform', request)
          }
          onDeleteComponent={(request) =>
            openComponentDialog('delete', request)
          }
          onEditRayObject={(request) => {
            setActiveSection('ray-tracing')
            setRayObjectEditRequest(request)
          }}
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
        scene={scene}
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
