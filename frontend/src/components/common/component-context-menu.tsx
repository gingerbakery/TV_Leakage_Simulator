import {
  useEffect,
  useRef,
  type ReactNode,
  type WheelEvent as ReactWheelEvent,
} from 'react'
import { createPortal } from 'react-dom'
import {
  Eye,
  EyeOff,
  Move3D,
  Palette,
  ScanLine,
  ScanSearch,
  Trash2,
} from 'lucide-react'

import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuLabel,
  ContextMenuSeparator,
  ContextMenuShortcut,
  ContextMenuTrigger,
} from '@/components/ui/context-menu'

export type ComponentContextAction =
  | 'visibility'
  | 'traceability'
  | 'material'
  | 'transform'
  | 'delete'

export interface ComponentContextMenuProps {
  children: ReactNode
  componentName: string
  visible: boolean
  traceable: boolean
  onAction(action: ComponentContextAction): void
}

export interface ViewerComponentActionMenuProps {
  componentName: string
  open: boolean
  position: { x: number; y: number }
  visible: boolean
  traceable: boolean
  wheelTarget?: HTMLElement | null
  onOpenChange(open: boolean): void
  onAction(action: ComponentContextAction): void
}

export function ComponentContextMenu({
  children,
  componentName,
  visible,
  traceable,
  onAction,
}: ComponentContextMenuProps) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="w-64 border border-border bg-popover/98 shadow-2xl shadow-black/40">
        <ContextMenuLabel className="px-2 py-1.5">
          <span className="block truncate text-sm font-semibold text-foreground">
            {componentName}
          </span>
          <span className="mt-0.5 block text-[0.7rem] font-normal text-muted-foreground">
            {visible ? 'Visible' : 'Hidden'} ·{' '}
            {traceable ? 'Traceability on' : 'Traceability off'}
          </span>
        </ContextMenuLabel>
        <ContextMenuSeparator />
        <ContextMenuItem onSelect={() => onAction('visibility')}>
          {visible ? <EyeOff /> : <Eye />}
          {visible ? 'Hide' : 'Show'}
        </ContextMenuItem>
        <ContextMenuItem onSelect={() => onAction('traceability')}>
          {traceable ? <ScanLine /> : <ScanSearch />}
          {traceable ? 'Traceability Off' : 'Traceability On'}
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          aria-label="Material"
          onSelect={() => onAction('material')}
        >
          <Palette />
          Material
          <ContextMenuShortcut>M</ContextMenuShortcut>
        </ContextMenuItem>
        <ContextMenuItem
          aria-label="Transform"
          onSelect={() => onAction('transform')}
        >
          <Move3D />
          Transform
          <ContextMenuShortcut>T</ContextMenuShortcut>
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          variant="destructive"
          onSelect={() => onAction('delete')}
        >
          <Trash2 />
          Delete…
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function ViewerComponentActionMenu({
  componentName,
  open,
  position,
  visible,
  traceable,
  wheelTarget,
  onOpenChange,
  onAction,
}: ViewerComponentActionMenuProps) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    menuRef.current
      ?.querySelector<HTMLButtonElement>('[role="menuitem"]')
      ?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      onOpenChange(false)
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [onOpenChange, open])

  if (!open || typeof document === 'undefined') return null

  const left = Math.max(
    8,
    Math.min(position.x, window.innerWidth - 272),
  )
  const top = Math.max(
    8,
    Math.min(position.y, window.innerHeight - 260),
  )
  const select = (action: ComponentContextAction) => {
    onAction(action)
    onOpenChange(false)
  }
  const forwardWheel = (event: ReactWheelEvent) => {
    const WheelEventType =
      wheelTarget?.ownerDocument.defaultView?.WheelEvent
    if (!wheelTarget || !WheelEventType) return

    event.stopPropagation()
    wheelTarget.dispatchEvent(
      new WheelEventType('wheel', {
        bubbles: true,
        cancelable: true,
        clientX: event.clientX,
        clientY: event.clientY,
        ctrlKey: event.ctrlKey,
        deltaMode: event.deltaMode,
        deltaX: event.deltaX,
        deltaY: event.deltaY,
        deltaZ: event.deltaZ,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
      }),
    )
  }
  const itemClassName =
    'flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-sm outline-none hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground [&_svg]:size-4 [&_svg]:shrink-0'

  return createPortal(
    <div
      className="fixed inset-0 z-50"
      onPointerDown={() => onOpenChange(false)}
      onContextMenu={(event) => {
        event.preventDefault()
        onOpenChange(false)
      }}
      onWheel={forwardWheel}
    >
      <div
        ref={menuRef}
        role="menu"
        aria-label={`Component actions for ${componentName}`}
        className="fixed w-64 rounded-lg border border-border bg-popover/98 p-1 text-popover-foreground shadow-2xl shadow-black/40 ring-1 ring-foreground/10"
        style={{ left, top }}
        onPointerDown={(event) => event.stopPropagation()}
        onContextMenu={(event) => {
          event.preventDefault()
          event.stopPropagation()
        }}
      >
        <div className="px-2 py-1.5">
          <span className="block truncate text-sm font-semibold text-foreground">
            {componentName}
          </span>
          <span className="mt-0.5 block text-[0.7rem] text-muted-foreground">
            {visible ? 'Visible' : 'Hidden'} ·{' '}
            {traceable ? 'Traceability on' : 'Traceability off'}
          </span>
        </div>
        <div className="-mx-1 my-1 h-px bg-border" />
        <button
          type="button"
          role="menuitem"
          className={itemClassName}
          onClick={() => select('visibility')}
        >
          {visible ? <EyeOff /> : <Eye />}
          {visible ? 'Hide' : 'Show'}
        </button>
        <button
          type="button"
          role="menuitem"
          className={itemClassName}
          onClick={() => select('traceability')}
        >
          {traceable ? <ScanLine /> : <ScanSearch />}
          {traceable ? 'Traceability Off' : 'Traceability On'}
        </button>
        <div className="-mx-1 my-1 h-px bg-border" />
        <button
          type="button"
          role="menuitem"
          className={itemClassName}
          onClick={() => select('material')}
        >
          <Palette />
          Material
          <span className="ml-auto text-xs tracking-widest text-muted-foreground">
            M
          </span>
        </button>
        <button
          type="button"
          role="menuitem"
          className={itemClassName}
          onClick={() => select('transform')}
        >
          <Move3D />
          Transform
          <span className="ml-auto text-xs tracking-widest text-muted-foreground">
            T
          </span>
        </button>
        <div className="-mx-1 my-1 h-px bg-border" />
        <button
          type="button"
          role="menuitem"
          className={`${itemClassName} text-destructive hover:bg-destructive/10 hover:text-destructive focus:bg-destructive/10 focus:text-destructive`}
          onClick={() => select('delete')}
        >
          <Trash2 />
          Delete…
        </button>
      </div>
    </div>,
    document.body,
  )
}
