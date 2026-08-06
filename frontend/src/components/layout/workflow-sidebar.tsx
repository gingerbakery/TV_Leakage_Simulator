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
import { Badge } from '@/components/ui/badge'
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
  description: string
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
    description: 'CAD 파일을 불러와 Three.js scene을 생성합니다.',
    icon: FileBox,
  },
  {
    id: 'roi',
    step: '02',
    label: 'ROI',
    description: '분석할 face 영역과 scope를 설정합니다.',
    icon: BoxSelect,
  },
  {
    id: 'components',
    step: '03',
    label: 'Components',
    description: '부품 표시, 해석 포함 여부와 선택을 관리합니다.',
    icon: Layers3,
  },
  {
    id: 'ray-tracing',
    step: '04',
    label: 'Ray tracing',
    description: 'Emitter·Receiver와 계산 옵션을 구성합니다.',
    icon: Play,
  },
  {
    id: 'result',
    step: '05',
    label: 'Result',
    description: 'Ray path, Receiver와 기여도 결과를 확인합니다.',
    icon: ScanSearch,
  },
  {
    id: 'applied-settings',
    label: 'Applied Settings',
    description:
      'Component에 적용된 Material assignment와 Transform rule을 검토하고 관리합니다.',
    icon: Settings2,
  },
]

const sectionBadgeText: Partial<Record<WorkflowSectionId, string>> = {
  roi: 'Migrated · 09',
  components: 'Migrated · 07',
  'ray-tracing': 'Migrated · 10',
  result: 'Migrated · 11',
  'applied-settings': 'Applied',
}

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
        ? `${scene.metadata.face_count.toLocaleString()} faces · ${scene.metadata.component_count} components${
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
                      {sectionBadgeText[section.id] ? (
                        <Badge
                          variant="outline"
                          className="mr-1 shrink-0 border-primary/25 bg-primary/8 text-[0.6rem] text-primary"
                        >
                          {sectionBadgeText[section.id]}
                        </Badge>
                      ) : null}
                    </AccordionTrigger>
                    <AccordionContent>
                      <p className="mb-2 px-1 text-xs leading-5 text-muted-foreground">
                        {section.description}
                      </p>
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
