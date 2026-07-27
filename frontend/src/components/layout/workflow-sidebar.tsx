import { useState } from 'react'
import type { RayTraceJob, ScenePayload } from '@/api'
import {
  BoxSelect,
  ChevronRight,
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
import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { ScrollArea } from '@/components/ui/scroll-area'
import { cn } from '@/lib/utils'

export type WorkflowSectionId =
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
]

const appliedSettingsSection: WorkflowSection = {
  id: 'applied-settings',
  label: 'Applied Settings',
  description:
    'Component에 적용된 Material assignment와 Transform rule을 검토하고 관리합니다.',
  icon: Settings2,
}

const sectionBadgeText: Record<WorkflowSectionId, string> = {
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
  const activeSectionInfo =
    activeSection === 'applied-settings'
      ? appliedSettingsSection
      : (workflowSections.find(
          (section) => section.id === activeSection,
        ) ?? workflowSections[0])
  const sceneStatus = isSceneLoading
    ? 'Loading scene and component tree…'
    : sceneErrorMessage
      ? 'Scene load failed'
      : scene
        ? `${scene.metadata.face_count.toLocaleString()} faces · ${scene.metadata.component_count} components`
        : undefined

  const activePanel = (() => {
    if (activeSection === 'roi') {
      return <RoiSelectionPanel scene={scene} />
    }

    if (activeSection === 'components') {
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

    if (activeSection === 'applied-settings') {
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

    if (activeSection === 'ray-tracing') {
      return (
        <RayTracingPanel
          scene={scene}
          cameraFrame={cameraFrame}
          editRequest={rayObjectEditRequest}
          onEditRequestHandled={onRayObjectEditRequestHandled}
        />
      )
    }

    if (activeSection === 'result') {
      return (
        <ResultPanel
          job={rayTraceJob}
          onOpenAnalysis={onOpenRayTraceResult}
        />
      )
    }

    return (
      <>
        <p className="text-xs leading-5 text-muted-foreground">
          {activeSectionInfo.description}
        </p>
        <Badge
          variant="outline"
          className="mt-3 border-border bg-background/40 text-muted-foreground"
        >
          Planned migration
        </Badge>
      </>
    )
  })()

  const migrationBadgeText = sectionBadgeText[activeSection]

  return (
    <aside className="border-b border-border bg-sidebar lg:min-h-0 lg:border-r lg:border-b-0">
      <ScrollArea className="h-[38rem] lg:h-full">
        <div className="space-y-4 p-3">
          <ModelImportCard
            sceneStatus={sceneStatus}
            onImported={() => onActiveSectionChange('roi')}
          />

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
            <nav
              className="grid grid-cols-2 gap-1.5 lg:grid-cols-1"
              aria-label="Simulation workflow"
            >
              {workflowSections.map((section) => {
                const Icon = section.icon
                const isActive = section.id === activeSection

                return (
                  <button
                    key={section.id}
                    type="button"
                    aria-label={`Step ${section.step} ${section.label}`}
                    aria-current={isActive ? 'step' : undefined}
                    className={cn(
                      'group flex min-h-12 items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors',
                      isActive
                        ? 'border-primary/40 bg-primary/10 text-foreground'
                        : 'border-transparent text-muted-foreground hover:border-border hover:bg-muted/35 hover:text-foreground',
                    )}
                    onClick={() => onActiveSectionChange(section.id)}
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
                      <span className="block text-[0.65rem] text-muted-foreground">
                        Step {section.step}
                      </span>
                      <span className="block truncate text-xs font-medium">
                        {section.label}
                      </span>
                    </span>
                    <ChevronRight
                      className={cn(
                        'hidden size-3.5 transition-transform lg:block',
                        isActive && 'translate-x-0.5 text-primary',
                      )}
                      aria-hidden="true"
                    />
                  </button>
                )
              })}
            </nav>
          </section>

          <button
            type="button"
            aria-label="Applied Settings"
            aria-current={
              activeSection === 'applied-settings' ? 'page' : undefined
            }
            className={cn(
              'group flex min-h-12 w-full items-center gap-2 rounded-lg border px-2.5 py-2 text-left transition-colors',
              activeSection === 'applied-settings'
                ? 'border-primary/40 bg-primary/10 text-foreground'
                : 'border-border/70 bg-background/25 text-muted-foreground hover:border-border hover:bg-muted/35 hover:text-foreground',
            )}
            onClick={() => onActiveSectionChange('applied-settings')}
          >
            <span
              className={cn(
                'flex size-7 shrink-0 items-center justify-center rounded-md border',
                activeSection === 'applied-settings'
                  ? 'border-primary/30 bg-primary/15 text-primary'
                  : 'border-border bg-background/40',
              )}
            >
              <Settings2 className="size-3.5" aria-hidden="true" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block truncate text-xs font-medium">
                Applied Settings
              </span>
              <span className="block truncate text-[0.62rem] text-muted-foreground">
                Material assignments · Transform rules
              </span>
            </span>
            <ChevronRight
              className={cn(
                'hidden size-3.5 transition-transform lg:block',
                activeSection === 'applied-settings' &&
                  'translate-x-0.5 text-primary',
              )}
              aria-hidden="true"
            />
          </button>

          <Card
            size="sm"
            className="border-primary/20 bg-primary/5 shadow-none"
          >
            <CardHeader>
              <CardDescription>
                {activeSectionInfo.step
                  ? `Step ${activeSectionInfo.step}`
                  : 'Configuration review'}
              </CardDescription>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>{activeSectionInfo.label}</CardTitle>
                <Badge
                  variant="outline"
                  className="border-primary/25 bg-primary/8 text-primary"
                >
                  {migrationBadgeText}
                </Badge>
              </div>
            </CardHeader>
            <CardContent>{activePanel}</CardContent>
          </Card>
        </div>
      </ScrollArea>
    </aside>
  )
}
