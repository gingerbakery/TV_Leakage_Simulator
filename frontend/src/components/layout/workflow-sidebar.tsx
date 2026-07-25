import type { RayTraceJob, ScenePayload } from '@/api'
import {
  BoxSelect,
  ChevronRight,
  Layers3,
  Move3D,
  Palette,
  Play,
  ScanSearch,
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
  | 'transform'
  | 'material'
  | 'ray-tracing'
  | 'result'

interface WorkflowSection {
  id: WorkflowSectionId
  step: string
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
    id: 'transform',
    step: '04',
    label: 'Transform',
    description: '부품의 move·tilt 규칙과 preview를 관리합니다.',
    icon: Move3D,
  },
  {
    id: 'material',
    step: '05',
    label: 'Material',
    description: '표면 광학 속성과 부품 assignment를 관리합니다.',
    icon: Palette,
  },
  {
    id: 'ray-tracing',
    step: '06',
    label: 'Ray tracing',
    description: 'Emitter·Receiver와 계산 옵션을 구성합니다.',
    icon: Play,
  },
  {
    id: 'result',
    step: '07',
    label: 'Result',
    description: 'Ray path, Receiver와 기여도 결과를 확인합니다.',
    icon: ScanSearch,
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
  onOpenRayTraceResult,
  onEditMaterial,
  onEditTransform,
  onDeleteComponent,
}: WorkflowSidebarProps) {
  const activeSectionInfo =
    workflowSections.find((section) => section.id === activeSection) ??
    workflowSections[0]
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

    if (activeSection === 'material') {
      return (
        <MaterialAssignmentPanel
          scene={scene}
          onEditMaterial={onEditMaterial}
        />
      )
    }

    if (activeSection === 'transform') {
      return (
        <TransformRulePanel
          scene={scene}
          onEditTransform={onEditTransform}
        />
      )
    }

    if (activeSection === 'ray-tracing') {
      return <RayTracingPanel scene={scene} cameraFrame={cameraFrame} />
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

  const isMigratedSection =
    activeSection === 'roi' ||
    activeSection === 'components' ||
    activeSection === 'material' ||
    activeSection === 'transform' ||
    activeSection === 'ray-tracing' ||
    activeSection === 'result'
  const migrationBadgeText =
    activeSection === 'roi'
      ? 'Migrated · 09'
      : activeSection === 'ray-tracing'
        ? 'Migrated · 10'
        : activeSection === 'result'
          ? 'Migrated · 11'
          : isMigratedSection
            ? 'Migrated · 07'
            : 'Queued'

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

          <Card
            size="sm"
            className="border-primary/20 bg-primary/5 shadow-none"
          >
            <CardHeader>
              <CardDescription>
                Step {activeSectionInfo.step}
              </CardDescription>
              <div className="flex items-center justify-between gap-2">
                <CardTitle>{activeSectionInfo.label}</CardTitle>
                <Badge
                  variant="outline"
                  className={cn(
                    isMigratedSection
                      ? 'border-primary/25 bg-primary/8 text-primary'
                      : 'border-border text-muted-foreground',
                  )}
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
