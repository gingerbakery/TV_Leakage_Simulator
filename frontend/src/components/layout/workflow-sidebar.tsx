import { useEffect, useState } from 'react'
import type { RayTraceJob, ScenePayload } from '@/api'
import {
  BoxSelect,
  FileBox,
  Layers3,
  Move3D,
  Palette,
  Play,
  ScanSearch,
  Settings2,
  Target,
  Workflow,
} from 'lucide-react'

import { HelpTooltip } from '@/components/common'
import { ModelImportCard } from '@/features/cad'
import {
  ComponentTreePanel,
  type ComponentEditorRequest,
} from '@/features/components'
import { MaterialAssignmentPanel } from '@/features/materials'
import {
  RayTracingPanel,
  type RayObjectEditRequest,
  type ViewerCameraFrame,
} from '@/features/raytracing'
import { ResultPanel } from '@/features/results'
import { RoiSelectionPanel } from '@/features/roi'
import { TransformRulePanel } from '@/features/transforms'
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from '@/components/ui/accordion'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export type WorkflowSectionId =
  | 'model-import'
  | 'roi'
  | 'components'
  | 'ray-tracing'
  | 'result'
  | 'applied-settings'

type AppliedSettingsTab = 'material' | 'transform'

interface WorkflowSection {
  id: WorkflowSectionId
  step?: string
  label: string
  guide: string
  icon: typeof Target
}

interface WorkflowSidebarProps {
  activeSection: WorkflowSectionId
  onActiveSectionChange(section: WorkflowSectionId): void
  scene?: ScenePayload
  cameraFrame: ViewerCameraFrame | null
  isSceneLoading?: boolean
  sceneErrorMessage?: string
  rayTraceJob?: RayTraceJob
  rayObjectEditRequest?: RayObjectEditRequest | null
  onRayObjectEditRequestHandled?(): void
  onOpenRayTraceResult(): void
  onEditMaterial(request: ComponentEditorRequest): void
  onEditTransform(request: ComponentEditorRequest): void
  onDeleteComponent(request: ComponentEditorRequest): void
}

const workflowSections: WorkflowSection[] = [
  {
    id: 'model-import',
    step: '01',
    label: 'Model import',
    guide:
      'STEP/STP, X_T/X_B, STL, OBJ 등 CAD 파일을 face 단위 mesh로 변환합니다. 가져온 뒤에는 원본 CAD 파일 없이도 현재 작업 상태를 .bitsam 프로젝트 파일로 저장해 나중에 다시 불러올 수 있습니다 (단, 같은 CAD를 다시 Import해야 기하가 맞물려 복원됩니다).',
    icon: FileBox,
  },
  {
    id: 'roi',
    step: '02',
    label: 'ROI',
    guide:
      '박스 드래그 또는 좌표 선택으로 분석 대상 face 범위(ROI)를 지정합니다. 체크박스로 활성화한 scope만 이후 Ray tracing·결과 집계와 Viewer 격리 표시에 반영되고, 비활성 scope는 목록에 남아있어도 계산에서 제외됩니다.',
    icon: BoxSelect,
  },
  {
    id: 'components',
    step: '03',
    label: 'Components',
    guide:
      'CAD의 부품(component) 목록입니다. 표시/숨김, 해석 제외 여부를 토글하고, 각 행의 아이콘으로 부품 단위 Material(재질)과 Transform(이동·회전)을 지정할 수 있습니다. Face를 직접 선택하면 부품 전체가 아닌 특정 면 단위로도 지정할 수 있습니다.',
    icon: Layers3,
  },
  {
    id: 'ray-tracing',
    step: '04',
    label: 'Ray tracing',
    guide:
      'Emitter(발광면)와 Receiver(수광면)를 CAD surface 또는 Datum plane으로 배치하고, Run options(반사 횟수, 종료 조건, 저장할 ray path 수 등)를 설정한 뒤 시뮬레이션을 실행합니다.',
    icon: Play,
  },
  {
    id: 'result',
    step: '05',
    label: 'Result',
    guide:
      '완료된 Ray trace 결과를 확인합니다. Receiver별 hit 통계, Viewer에 표시되는 3D ray path, 그리고 Receiver를 지나는 단면으로 잘라 CAD와 ray를 함께 보여주는 Ray Section View 이미지를 제공합니다.',
    icon: ScanSearch,
  },
  {
    id: 'applied-settings',
    label: 'Applied Settings',
    guide:
      '지금까지 지정한 모든 Material assignment(부품/Face별 재질)와 Transform rule(이동·회전)을 한 곳에서 검토하고 개별적으로 삭제할 수 있는 목록입니다. Step 03 Components에서 지정한 내용이 여기 반영됩니다.',
    icon: Settings2,
  },
]

export function WorkflowSidebar({
  activeSection,
  onActiveSectionChange,
  scene,
  cameraFrame,
  isSceneLoading = false,
  sceneErrorMessage,
  rayTraceJob,
  rayObjectEditRequest,
  onRayObjectEditRequestHandled,
  onOpenRayTraceResult,
  onEditMaterial,
  onEditTransform,
  onDeleteComponent,
}: WorkflowSidebarProps) {
  const [appliedSettingsTab, setAppliedSettingsTab] =
    useState<AppliedSettingsTab>('material')
  // Which step is visually expanded - starts in sync with `activeSection`,
  // but can be collapsed independently (e.g. the user closes it to scan the
  // rest of the workflow) without changing what's functionally "active"
  // elsewhere in the app. External navigation (a completed ray trace
  // jumping to Result, editing a ray object jumping to Ray tracing) should
  // still force it back open, so it re-syncs whenever `activeSection`
  // changes from the outside.
  const [expandedSection, setExpandedSection] = useState<
    WorkflowSectionId | ''
  >(activeSection)
  useEffect(() => {
    setExpandedSection(activeSection)
  }, [activeSection])

  const sceneStatus = isSceneLoading
    ? 'Loading scene and component tree…'
    : sceneErrorMessage
      ? 'Scene load failed'
      : scene
        ? `${scene.metadata.component_count} components${
            scene.metadata.import_timings_sec?.scene_payload_total !==
            undefined
              ? ` · ${scene.metadata.import_timings_sec.scene_payload_total.toFixed(1)}s`
              : ''
          }`
        : undefined

  const renderPanel = (sectionId: WorkflowSectionId) => {
    if (sectionId === 'model-import') {
      return (
        <ModelImportCard
          sceneStatus={sceneStatus}
          onImported={() => onActiveSectionChange('roi')}
        />
      )
    }

    if (sectionId === 'roi') {
      return <RoiSelectionPanel scene={scene} />
    }

    if (sectionId === 'components') {
      return (
        <ComponentTreePanel
          scene={scene}
          isLoading={isSceneLoading}
          errorMessage={sceneErrorMessage}
          onEditMaterial={onEditMaterial}
          onEditTransform={onEditTransform}
          onDelete={onDeleteComponent}
        />
      )
    }

    if (sectionId === 'applied-settings') {
      return (
        <div>
          <div
            className="mb-3 grid grid-cols-2 gap-1 rounded-lg border border-border bg-background/35 p-1"
            role="tablist"
            aria-label="Applied settings views"
          >
            <button
              type="button"
              role="tab"
              aria-selected={appliedSettingsTab === 'material'}
              className={cn(
                'flex min-h-8 items-center justify-center gap-1.5 rounded-md px-2 text-[0.68rem] font-medium transition-colors',
                appliedSettingsTab === 'material'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
              onClick={() => setAppliedSettingsTab('material')}
            >
              <Palette className="size-3.5" aria-hidden="true" />
              Material
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={appliedSettingsTab === 'transform'}
              className={cn(
                'flex min-h-8 items-center justify-center gap-1.5 rounded-md px-2 text-[0.68rem] font-medium transition-colors',
                appliedSettingsTab === 'transform'
                  ? 'bg-primary/15 text-primary'
                  : 'text-muted-foreground hover:bg-muted/40 hover:text-foreground',
              )}
              onClick={() => setAppliedSettingsTab('transform')}
            >
              <Move3D className="size-3.5" aria-hidden="true" />
              Transform
            </button>
          </div>

          <div
            role="tabpanel"
            aria-label={
              appliedSettingsTab === 'material'
                ? 'Material assignments'
                : 'Transform rules'
            }
          >
            {appliedSettingsTab === 'material' ? (
              <MaterialAssignmentPanel
                scene={scene}
                onEditMaterial={onEditMaterial}
              />
            ) : (
              <TransformRulePanel
                scene={scene}
                onEditTransform={onEditTransform}
              />
            )}
          </div>
        </div>
      )
    }

    if (sectionId === 'ray-tracing') {
      return (
        <RayTracingPanel
          scene={scene}
          cameraFrame={cameraFrame}
          editRequest={rayObjectEditRequest}
          onEditRequestHandled={onRayObjectEditRequestHandled}
        />
      )
    }

    if (sectionId === 'result') {
      return <ResultPanel job={rayTraceJob} onOpenAnalysis={onOpenRayTraceResult} />
    }

    return null
  }

  return (
    <aside className="border-b border-border bg-sidebar lg:min-h-0 lg:border-r lg:border-b-0">
      <ScrollArea className="h-[38rem] lg:h-full">
        <div className="space-y-4 p-3">
          <section aria-labelledby="workflow-navigation-title">
            <div className="mb-2 flex items-center justify-between px-1">
              <h2
                id="workflow-navigation-title"
                className="text-xs font-semibold tracking-wide text-muted-foreground uppercase"
              >
                Workflow
              </h2>
              <Workflow className="size-3.5 text-muted-foreground" />
            </div>
            <Accordion
              type="single"
              collapsible
              value={expandedSection}
              onValueChange={(value) => {
                const nextValue = value as WorkflowSectionId | ''
                setExpandedSection(nextValue)
                if (nextValue) onActiveSectionChange(nextValue)
              }}
              className="rounded-lg border border-border bg-background/25 px-2.5"
              aria-label="Simulation workflow"
            >
              {workflowSections.map((section) => {
                const Icon = section.icon
                const isActive = section.id === activeSection

                return (
                  <AccordionItem key={section.id} value={section.id}>
                    <AccordionTrigger
                      aria-label={
                        section.step
                          ? `Step ${section.step} ${section.label}`
                          : section.label
                      }
                      aria-current={isActive ? 'step' : undefined}
                      endAdornment={
                        <HelpTooltip label={`${section.label} 도움말`}>
                          {section.guide}
                        </HelpTooltip>
                      }
                    >
                      <span
                        className={cn(
                          'flex size-7 shrink-0 items-center justify-center rounded-md border',
                          isActive
                            ? 'border-primary/30 bg-primary/15 text-primary'
                            : 'border-border bg-background/40',
                        )}
                      >
                        <Icon className="size-3.5" aria-hidden="true" />
                      </span>
                      <span className="min-w-0 flex-1">
                        {section.step ? (
                          <span className="block text-[0.65rem] text-muted-foreground">
                            Step {section.step}
                          </span>
                        ) : null}
                        <span className="block truncate text-xs font-medium">
                          {section.label}
                        </span>
                      </span>
                    </AccordionTrigger>
                    <AccordionContent>
                      {renderPanel(section.id)}
                    </AccordionContent>
                  </AccordionItem>
                )
              })}
            </Accordion>
          </section>
        </div>
      </ScrollArea>
    </aside>
  )
}
