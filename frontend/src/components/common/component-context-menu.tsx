import type { ReactNode } from 'react'
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
