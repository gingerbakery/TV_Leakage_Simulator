import { useRef, useState, type KeyboardEvent } from 'react'
import type { SceneComponent, ScenePayload } from '@/api'
import {
  Box,
  Eye,
  EyeOff,
  Move3D,
  Palette,
  Pencil,
  Search,
  Trash2,
} from 'lucide-react'

import {
  ComponentContextMenu,
  type ComponentContextAction,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

import { formatArea, getComponentDisplayName } from './component-utils'

const displayColorPalette = [
  '#2563eb', '#0ea5e9', '#14b8a6', '#22c55e',
  '#eab308', '#f97316', '#ef4444', '#a855f7',
  '#64748b', '#111827', '#f8fafc', '#ffffff',
]

export interface ComponentEditorRequest {
  componentId: number
  returnFocusElement: HTMLElement | null
}

interface ComponentTreePanelProps {
  scene?: ScenePayload
  isLoading?: boolean
  errorMessage?: string
  onEditMaterial(request: ComponentEditorRequest): void
  onEditTransform(request: ComponentEditorRequest): void
  onDelete(request: ComponentEditorRequest): void
}

interface ComponentTreeRowProps {
  component: SceneComponent
  displayName: string
  roiAreaMm2?: number
  roiFaceCount?: number
  selected: boolean
  visible: boolean
  traceable: boolean
  onEditMaterial(request: ComponentEditorRequest): void
  onEditTransform(request: ComponentEditorRequest): void
  onDelete(request: ComponentEditorRequest): void
}

function ComponentTreeRow({
  component,
  displayName,
  roiAreaMm2,
  roiFaceCount,
  selected,
  visible,
  traceable,
  onEditMaterial,
  onEditTransform,
  onDelete,
}: ComponentTreeRowProps) {
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const componentColorOverrides = useWorkspaceStore(
    workspaceSelectors.componentColorOverrides,
  )
  const [isRenaming, setIsRenaming] = useState(false)
  const [nameDraft, setNameDraft] = useState(displayName)
  const [isColorPaletteOpen, setIsColorPaletteOpen] = useState(false)
  const cancelRenameRef = useRef(false)
  const rowRef = useRef<HTMLDivElement>(null)
  const componentId = component.component_id
  const customColor = componentColorOverrides[componentId]
  const displayColor = customColor ?? component.color ?? '#64748b'

  const request = (): ComponentEditorRequest => ({
    componentId,
    returnFocusElement: rowRef.current,
  })

  const prepareEditorTarget = () => {
    actions.setSelectedComponentIds([componentId])
    actions.setSelectedFaceIds([])
    if (!visible) actions.toggleComponentVisibility(componentId)
  }

  const editMaterial = () => {
    prepareEditorTarget()
    onEditMaterial(request())
  }

  const editTransform = () => {
    prepareEditorTarget()
    onEditTransform(request())
  }

  const beginRename = () => {
    cancelRenameRef.current = false
    setNameDraft(displayName)
    setIsRenaming(true)
  }

  const saveRename = () => {
    actions.renameComponent(componentId, nameDraft)
    setIsRenaming(false)
  }

  const handleRenameKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
  ) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      saveRename()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      cancelRenameRef.current = true
      setIsRenaming(false)
    }
  }

  const handleContextAction = (action: ComponentContextAction) => {
    if (action === 'visibility') {
      actions.toggleComponentVisibility(componentId)
    } else if (action === 'traceability') {
      actions.toggleComponentTraceability(componentId)
    } else if (action === 'material') {
      editMaterial()
    } else if (action === 'transform') {
      editTransform()
    } else {
      onDelete(request())
    }
  }

  return (
    <ComponentContextMenu
      componentName={displayName}
      visible={visible}
      traceable={traceable}
      onAction={handleContextAction}
    >
      <div
        ref={rowRef}
        data-component-id={componentId}
        className={cn(
          'rounded-lg border bg-background/45 p-2 transition-colors',
          selected
            ? 'border-primary/50 bg-primary/8'
            : 'border-border/75 hover:border-border',
          !visible && 'opacity-55',
        )}
      >
        <div className="flex min-w-0 items-start gap-2">
          <span
            className={cn(
              'relative mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border',
              selected
                ? 'border-primary/35 bg-primary/15 text-primary'
                : 'border-border bg-muted/30 text-muted-foreground',
            )}
          >
            <Box className="size-3.5" aria-hidden="true" />
            {displayColor ? (
              <span
                className="absolute -right-0.5 -bottom-0.5 size-2.5 rounded-full border border-background"
                style={{ backgroundColor: displayColor }}
                title={
                  customColor
                    ? `사용자 표시색 ${customColor}`
                    : component.color
                      ? `CAD 원본색 ${component.color}`
                      : '기본 표시색'
                }
                aria-hidden="true"
              />
            ) : null}
          </span>
          <div className="min-w-0 flex-1">
            {isRenaming ? (
              <input
                autoFocus
                aria-label="Component name"
                className="h-7 w-full rounded-md border border-input bg-background px-2 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/25"
                value={nameDraft}
                onChange={(event) => setNameDraft(event.currentTarget.value)}
                onKeyDown={handleRenameKeyDown}
                onBlur={() => {
                  if (!cancelRenameRef.current) saveRename()
                }}
              />
            ) : (
              <button
                type="button"
                aria-pressed={selected}
                aria-label={`Select ${displayName}`}
                className="block w-full min-w-0 rounded-md text-left outline-none focus-visible:ring-2 focus-visible:ring-primary"
                onClick={() =>
                  actions.toggleSelectedComponentId(componentId)
                }
                onDoubleClick={beginRename}
                onKeyDown={(event) => {
                  if (event.key !== 'F2') return
                  event.preventDefault()
                  beginRename()
                }}
              >
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold">
                    {displayName}
                  </span>
                  {roiFaceCount !== undefined ? (
                    <Badge variant="outline" className="h-5 px-1 text-xs">
                      ROI
                    </Badge>
                  ) : null}
                  {component.is_truncated ? (
                    <Badge variant="outline" className="h-5 px-1 text-xs">
                      partial
                    </Badge>
                  ) : null}
                </span>
                <span className="mt-1 block text-xs leading-4 text-muted-foreground">
                  {formatArea(roiAreaMm2 ?? component.area_mm2)} mm²
                </span>
              </button>
            )}
          </div>
          <div className="relative shrink-0">
            <button
              type="button"
              aria-label={`${displayName} 표시색 선택`}
              aria-expanded={isColorPaletteOpen}
              title="표시색 팔레트"
              className="flex h-7 w-7 items-center justify-center rounded-md border border-border bg-background shadow-sm"
              onClick={() => setIsColorPaletteOpen((open) => !open)}
            >
              <span
                className="size-4 rounded-full border border-black/20"
                style={{ backgroundColor: displayColor }}
              />
            </button>
            {isColorPaletteOpen ? (
              <div className="absolute top-8 right-0 z-30 w-40 rounded-lg border border-border bg-popover p-2 shadow-xl">
                <div className="mb-1.5 text-sm font-semibold text-muted-foreground">
                  표시색 팔레트
                </div>
                <div className="grid grid-cols-6 gap-1.5">
                  {displayColorPalette.map((color) => (
                    <button
                      key={color}
                      type="button"
                      aria-label={`표시색 ${color}`}
                      title={color}
                      className="size-5 rounded-full border border-black/20 ring-offset-1 hover:ring-2 hover:ring-primary"
                      style={{ backgroundColor: color }}
                      onClick={() => {
                        actions.setComponentColor(componentId, color)
                        setIsColorPaletteOpen(false)
                      }}
                    />
                  ))}
                </div>
                <div className="mt-2 flex items-center justify-between border-t border-border pt-2">
                  <button
                    type="button"
                    className="text-xs font-medium text-primary hover:underline"
                    onClick={() => {
                      actions.setComponentColor(componentId, null)
                      setIsColorPaletteOpen(false)
                    }}
                  >
                    CAD 원본색
                  </button>
                  <label className="flex cursor-pointer items-center gap-1 text-xs text-muted-foreground">
                    직접 지정
                    <input
                      type="color"
                      value={displayColor}
                      aria-label={`${displayName} 사용자 정의 표시색`}
                      className="h-5 w-5 cursor-pointer rounded border-0 bg-transparent p-0"
                      onChange={(event) => {
                        actions.setComponentColor(
                          componentId,
                          event.currentTarget.value,
                        )
                        setIsColorPaletteOpen(false)
                      }}
                    />
                  </label>
                </div>
              </div>
            ) : null}
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`Rename ${displayName}`}
            onClick={beginRename}
          >
            <Pencil />
          </Button>
        </div>

        <div className="mt-2 grid grid-cols-5 gap-1 rounded-md border border-blue-100 bg-blue-50/75 p-1.5 dark:border-blue-900/70 dark:bg-blue-950/30">
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`Material for ${displayName}`}
            title="Material"
            onClick={editMaterial}
          >
            <Palette />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`Transform ${displayName}`}
            title="Transform"
            onClick={editTransform}
          >
            <Move3D />
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`${visible ? 'Hide' : 'Show'} ${displayName}`}
            title={visible ? 'Hide' : 'Show'}
            onClick={() => actions.toggleComponentVisibility(componentId)}
          >
            {visible ? <Eye /> : <EyeOff />}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="xs"
            aria-pressed={traceable}
            aria-label={`Traceability ${traceable ? 'ON' : 'OFF'} for ${displayName}`}
            title={`현재 상태: Traceability ${traceable ? 'ON' : 'OFF'} · 클릭하여 ${traceable ? 'OFF' : 'ON'}`}
            className={cn(
              'h-7 min-w-0 px-1 text-xs font-bold tracking-tight',
              traceable
                ? 'bg-emerald-100/80 text-emerald-800 hover:bg-emerald-200/80 dark:bg-emerald-950/55 dark:text-emerald-300 dark:hover:bg-emerald-900/65'
                : 'bg-slate-200/90 text-slate-600 hover:bg-slate-300/90 dark:bg-slate-800 dark:text-slate-400 dark:hover:bg-slate-700',
            )}
            onClick={() => actions.toggleComponentTraceability(componentId)}
          >
            TRACE {traceable ? 'ON' : 'OFF'}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon-xs"
            aria-label={`Delete ${displayName}`}
            title="Delete"
            onClick={() => onDelete(request())}
          >
            <Trash2 />
          </Button>
        </div>
      </div>
    </ComponentContextMenu>
  )
}

export function ComponentTreePanel({
  scene,
  isLoading = false,
  errorMessage,
  onEditMaterial,
  onEditTransform,
  onDelete,
}: ComponentTreePanelProps) {
  const [searchQuery, setSearchQuery] = useState('')
  const selectedComponentIds = useWorkspaceStore(
    workspaceSelectors.selectedComponentIds,
  )
  const hiddenComponentIds = useWorkspaceStore(
    workspaceSelectors.hiddenComponentIds,
  )
  const excludedComponentIds = useWorkspaceStore(
    workspaceSelectors.excludedComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const nameOverrides = useWorkspaceStore(
    workspaceSelectors.componentNameOverrides,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)

  const activeRoiFaceIdsByComponent = new Map<number, Set<number>>()
  for (const scope of roiScopes) {
    if (!scope.active) continue
    for (const component of scope.components) {
      const faceIds =
        activeRoiFaceIdsByComponent.get(component.componentId) ??
        new Set<number>()
      component.faceIds.forEach((faceId) => faceIds.add(faceId))
      activeRoiFaceIdsByComponent.set(component.componentId, faceIds)
    }
  }
  const hasActiveRoi = activeRoiFaceIdsByComponent.size > 0

  const availableComponents = (scene?.components ?? []).filter(
    (component) =>
      !deletedComponentIds.includes(component.component_id) &&
      (!hasActiveRoi ||
        activeRoiFaceIdsByComponent.has(component.component_id)),
  )
  const normalizedQuery = searchQuery.trim().toLocaleLowerCase()
  const filteredComponents = availableComponents.filter((component) =>
    getComponentDisplayName(component, nameOverrides)
      .toLocaleLowerCase()
      .includes(normalizedQuery),
  )

  if (isLoading) {
    return (
      <div className="rounded-lg border border-border bg-background/35 p-4 text-center text-xs text-muted-foreground">
        Component tree를 구성하는 중입니다…
      </div>
    )
  }

  if (errorMessage) {
    return (
      <div className="rounded-lg border border-destructive/35 bg-destructive/8 p-3 text-xs leading-5 text-destructive">
        Scene을 불러오지 못했습니다.
        <span className="mt-1 block text-xs opacity-85">
          {errorMessage}
        </span>
      </div>
    )
  }

  if (!scene) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background/30 p-4 text-center">
        <Box className="mx-auto size-6 text-muted-foreground" />
        <div className="mt-2 text-xs font-medium">No component data</div>
        <p className="mt-1 text-xs leading-4 text-muted-foreground">
          CAD를 Import하면 실제 ScenePayload의 component가 표시됩니다.
        </p>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between text-xs text-muted-foreground">
        <span>{availableComponents.length} components</span>
        <span>{selectedComponentIds.length} selected</span>
      </div>
      <label className="relative block">
        <Search
          className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
        />
        <span className="sr-only">Search components</span>
        <input
          type="search"
          placeholder="Search components"
          className="h-8 w-full rounded-lg border border-input bg-background/60 pr-2 pl-8 text-xs outline-none placeholder:text-muted-foreground focus:border-primary focus:ring-2 focus:ring-primary/20"
          value={searchQuery}
          onChange={(event) => setSearchQuery(event.currentTarget.value)}
        />
      </label>
      <div className="space-y-1.5" aria-label="Component tree">
        {filteredComponents.map((component) => {
          const componentId = component.component_id
          const roiFaceIds = activeRoiFaceIdsByComponent.get(componentId)
          const roiAreaMm2 = roiFaceIds
            ? [...roiFaceIds].reduce(
                (sum, faceId) =>
                  sum + (scene.mesh.face_areas_mm2[faceId] ?? 0),
                0,
              )
            : undefined
          return (
            <ComponentTreeRow
              key={componentId}
              component={component}
              displayName={getComponentDisplayName(
                component,
                nameOverrides,
              )}
              roiAreaMm2={roiAreaMm2}
              roiFaceCount={roiFaceIds?.size}
              selected={selectedComponentIds.includes(componentId)}
              visible={!hiddenComponentIds.includes(componentId)}
              traceable={!excludedComponentIds.includes(componentId)}
              onEditMaterial={onEditMaterial}
              onEditTransform={onEditTransform}
              onDelete={onDelete}
            />
          )
        })}
      </div>
      {filteredComponents.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
          검색 결과가 없습니다.
        </div>
      ) : null}
      {deletedComponentIds.length > 0 ? (
        <p className="text-xs leading-4 text-muted-foreground">
          {deletedComponentIds.length}개 component가 작업 상태에서
          제외되었습니다. CAD를 다시 Import하면 복원됩니다.
        </p>
      ) : null}
    </div>
  )
}
