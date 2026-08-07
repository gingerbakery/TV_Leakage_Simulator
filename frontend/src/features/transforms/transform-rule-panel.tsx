import type { ScenePayload } from '@/api'
import { Move3D, Power, Trash2 } from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import type { ComponentEditorRequest } from '@/features/components/component-tree-panel'
import { getComponentDisplayName } from '@/features/components/component-utils'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'
import { cn } from '@/lib/utils'

interface TransformRulePanelProps {
  scene?: ScenePayload
  onEditTransform(request: ComponentEditorRequest): void
}

export function TransformRulePanel({
  scene,
  onEditTransform,
}: TransformRulePanelProps) {
  const rules = useWorkspaceStore(workspaceSelectors.transformRules)
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

  if (rules.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/30 p-4 text-center">
        <Move3D className="mx-auto size-5 text-muted-foreground" />
        <p className="mt-2 text-xs font-medium">No transform rules</p>
        <p className="mt-1 text-[0.68rem] leading-4 text-muted-foreground">
          Components에서 Transform을 선택해 component move 또는 local face
          rule을 만드세요.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <span className="text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
          Transform rules
        </span>
        <Badge variant="outline">{rules.length}</Badge>
      </div>
      {rules.map((rule) => {
        const component = componentsById.get(rule.componentId)
        const componentName = component
          ? getComponentDisplayName(component, nameOverrides)
          : `Component ${rule.componentId}`

        return (
          <div
            key={rule.ruleId}
            className={cn(
              'rounded-lg border border-border bg-background/40 p-2.5',
              !rule.enabled && 'opacity-55',
            )}
          >
            <div className="flex items-start gap-2">
              <button
                type="button"
                className="min-w-0 flex-1 rounded text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
                disabled={!component}
                onClick={(event) =>
                  onEditTransform({
                    componentId: rule.componentId,
                    returnFocusElement: event.currentTarget,
                  })
                }
              >
                <span className="block truncate text-xs font-semibold">
                  {componentName}
                </span>
                <span className="mt-1 block font-mono text-[0.62rem] leading-4 text-muted-foreground">
                  M {rule.move.x.toFixed(2)}, {rule.move.y.toFixed(2)},{' '}
                  {rule.move.z.toFixed(2)} mm
                  <br />R {rule.tilt.x.toFixed(2)}, {rule.tilt.y.toFixed(2)},{' '}
                  {rule.tilt.z.toFixed(2)}°
                </span>
              </button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-pressed={rule.enabled}
                aria-label={`${rule.enabled ? 'Disable' : 'Enable'} transform for ${componentName}`}
                onClick={() =>
                  actions.setTransformRuleEnabled(
                    rule.ruleId,
                    !rule.enabled,
                  )
                }
              >
                <Power />
              </Button>
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={`Remove transform for ${componentName}`}
                onClick={() => actions.removeTransformRule(rule.ruleId)}
              >
                <Trash2 />
              </Button>
            </div>
            <Badge variant="outline" className="mt-2">
              {rule.targetType === 'faces' ? 'Local faces' : 'Component move'}
            </Badge>
          </div>
        )
      })}
    </div>
  )
}
