import type { ScenePayload } from '@/api'
import { Layers3, Palette, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ComponentEditorRequest } from '@/features/components/component-tree-panel'
import { getComponentDisplayName } from '@/features/components/component-utils'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

import {
  baseMaterials,
  findBaseMaterial,
  findSurfaceProperty,
  opticalProfilePresets,
  surfaceProperties,
} from './material-catalog'

interface MaterialAssignmentPanelProps {
  scene?: ScenePayload
  onEditMaterial(request: ComponentEditorRequest): void
}

export function MaterialAssignmentPanel({
  scene,
  onEditMaterial,
}: MaterialAssignmentPanelProps) {
  const assignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const nameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  const componentsById = new Map(
    (scene?.components ?? []).map((component) => [
      component.component_id,
      component,
    ]),
  )

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-3 gap-1.5">
        {[
          ['Base', baseMaterials.length],
          ['Surface', surfaceProperties.length],
          ['Profile', opticalProfilePresets.length],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-lg border border-border bg-background/40 p-2 text-center"
          >
            <div className="text-xs text-muted-foreground">{label}</div>
            <div className="mt-0.5 text-base font-semibold">{value}</div>
          </div>
        ))}
      </div>

      <div className="flex items-center justify-between">
        <div className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">
          Assignments
        </div>
        <Badge variant="outline">{assignments.length}</Badge>
      </div>

      {assignments.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border bg-background/30 p-4 text-center">
          <Palette className="mx-auto size-5 text-muted-foreground" />
          <p className="mt-2 text-xs font-medium">No assignments</p>
          <p className="mt-1 text-xs leading-4 text-muted-foreground">
            Components에서 Material을 선택해 part 또는 face profile을
            지정하세요.
          </p>
        </div>
      ) : (
        <div className="space-y-1.5">
          {assignments.map((assignment) => {
            const component = componentsById.get(assignment.componentId)
            const componentName = component
              ? getComponentDisplayName(component, nameOverrides)
              : `Component ${assignment.componentId}`
            const base = findBaseMaterial(assignment.baseMaterialId)
            const surface = findSurfaceProperty(assignment.surfaceId)

            return (
              <div
                key={assignment.assignmentId}
                className="rounded-lg border border-border bg-background/40 p-2.5"
              >
                <div className="flex items-start justify-between gap-2">
                  <button
                    type="button"
                    className="min-w-0 flex-1 rounded text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
                    disabled={!component}
                    onClick={(event) =>
                      onEditMaterial({
                        componentId: assignment.componentId,
                        returnFocusElement: event.currentTarget,
                      })
                    }
                  >
                    <span className="block truncate text-sm font-semibold">
                      {componentName}
                    </span>
                    <span className="mt-1 block text-xs leading-4 text-muted-foreground">
                      {assignment.targetType === 'faces'
                        ? surface.name
                        : `${base.name} · ${surface.name}`}
                    </span>
                  </button>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Remove material assignment for ${componentName}`}
                    onClick={() =>
                      actions.removeMaterialAssignment(
                        assignment.assignmentId,
                      )
                    }
                  >
                    <Trash2 />
                  </Button>
                </div>
                <div className="mt-2 flex items-center gap-1.5">
                  <Badge variant="outline">
                    {assignment.targetType === 'faces'
                      ? 'Face 지정'
                      : 'Part material'}
                  </Badge>
                  <Layers3 className="size-3 text-muted-foreground" />
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
