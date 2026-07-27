import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type RefObject,
} from 'react'
import { GripVertical } from 'lucide-react'

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'

type AppDialogSize = 'sm' | 'md' | 'lg'

export interface AppDialogProps {
  open: boolean
  onOpenChange(open: boolean): void
  title: string
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  size?: AppDialogSize
  returnFocusRef?: RefObject<HTMLElement | null>
  modal?: boolean
  keepOpenOnInteractOutside?: boolean
  contentClassName?: string
  floating?: boolean
  onSubmit?(): void
}

const sizeClasses: Record<AppDialogSize, string> = {
  sm: 'sm:max-w-sm',
  md: 'sm:max-w-lg',
  lg: 'sm:max-w-2xl',
}

const floatingPanelGap = 12
const floatingPanelTop = 64

export function AppDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  returnFocusRef,
  modal = true,
  keepOpenOnInteractOutside = false,
  contentClassName,
  floating = false,
  onSubmit,
}: AppDialogProps) {
  const contentRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{
    pointerX: number
    pointerY: number
    panelX: number
    panelY: number
  } | null>(null)
  const wasDraggedRef = useRef(false)
  const [position, setPosition] = useState({
    x: floatingPanelGap,
    y: floatingPanelTop,
  })
  const isModal = modal && !floating

  useLayoutEffect(() => {
    if (!open || !floating || wasDraggedRef.current) return

    const viewer = document.querySelector<HTMLElement>(
      '[data-viewer-workspace]',
    )
    const viewerBounds = viewer?.getBoundingClientRect()
    if (!viewerBounds || viewerBounds.width <= 0) return

    const panelWidth =
      contentRef.current?.getBoundingClientRect().width ?? 340
    setPosition({
      x: Math.max(
        floatingPanelGap,
        Math.min(
          viewerBounds.left + floatingPanelGap,
          window.innerWidth - panelWidth - floatingPanelGap,
        ),
      ),
      y: floatingPanelTop,
    })
  }, [floating, open])

  useEffect(() => {
    if (!open || !floating) return

    const move = (event: PointerEvent) => {
      const drag = dragRef.current
      const panel = contentRef.current
      if (!drag || !panel) return
      const bounds = panel.getBoundingClientRect()
      setPosition({
        x: Math.max(
          8,
          Math.min(
            drag.panelX + event.clientX - drag.pointerX,
            window.innerWidth - bounds.width - 8,
          ),
        ),
        y: Math.max(
          8,
          Math.min(
            drag.panelY + event.clientY - drag.pointerY,
            window.innerHeight - bounds.height - 8,
          ),
        ),
      })
    }
    const stop = () => {
      dragRef.current = null
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', stop)
    window.addEventListener('pointercancel', stop)
    return () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', stop)
      window.removeEventListener('pointercancel', stop)
    }
  }, [floating, open])

  const beginDrag = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (!floating) return
    wasDraggedRef.current = true
    dragRef.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      panelX: position.x,
      panelY: position.y,
    }
    event.preventDefault()
  }

  const handleKeyDown = (
    event: ReactKeyboardEvent<HTMLDivElement>,
  ) => {
    if (
      !onSubmit ||
      event.key !== 'Enter' ||
      event.defaultPrevented ||
      event.nativeEvent.isComposing ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey
    ) {
      return
    }
    const target = event.target as HTMLElement
    if (
      target.closest(
        'button, a, textarea, [role="button"], [role="menuitem"], [contenteditable="true"], [data-enter-submit="false"]',
      )
    ) {
      return
    }
    event.preventDefault()
    event.stopPropagation()
    onSubmit()
  }

  return (
    <Dialog modal={isModal} open={open} onOpenChange={onOpenChange}>
      <DialogContent
        ref={contentRef}
        showOverlay={isModal}
        onInteractOutside={
          floating || keepOpenOnInteractOutside
            ? (event) => event.preventDefault()
            : undefined
        }
        onCloseAutoFocus={(event) => {
          if (!returnFocusRef?.current) return
          event.preventDefault()
          returnFocusRef.current.focus()
        }}
        className={cn(
          'border border-border bg-popover/98 shadow-2xl shadow-black/40',
          sizeClasses[size],
          floating &&
            'left-0 top-0 max-h-[calc(100vh-4.5rem)] w-[21.25rem] max-w-[calc(100vw-1.5rem)] translate-x-0 translate-y-0 overflow-hidden sm:max-w-[21.25rem]',
          contentClassName,
        )}
        style={
          floating
            ? {
                left: position.x,
                top: position.y,
              }
            : undefined
        }
        data-floating-panel={floating ? '' : undefined}
        onKeyDown={handleKeyDown}
      >
        <DialogHeader
          className={cn(
            floating &&
              '-mx-1 -mt-1 cursor-move touch-none select-none rounded-lg px-1 py-1 pr-8',
          )}
          onPointerDown={beginDrag}
        >
          <div className="flex items-center gap-1.5">
            {floating ? (
              <GripVertical
                className="size-4 shrink-0 text-muted-foreground"
                aria-hidden="true"
              />
            ) : null}
            <DialogTitle>{title}</DialogTitle>
          </div>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : null}
        </DialogHeader>
        {children}
        {footer ? (
          <DialogFooter className={cn(floating && 'flex-wrap')}>
            {footer}
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
