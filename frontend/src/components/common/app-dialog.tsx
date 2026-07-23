import type { ReactNode, RefObject } from 'react'

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
}

const sizeClasses: Record<AppDialogSize, string> = {
  sm: 'sm:max-w-sm',
  md: 'sm:max-w-lg',
  lg: 'sm:max-w-2xl',
}

export function AppDialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  size = 'md',
  returnFocusRef,
}: AppDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        onCloseAutoFocus={(event) => {
          if (!returnFocusRef?.current) return
          event.preventDefault()
          returnFocusRef.current.focus()
        }}
        className={cn(
          'border border-border bg-popover/98 shadow-2xl shadow-black/40',
          sizeClasses[size],
        )}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description ? (
            <DialogDescription>{description}</DialogDescription>
          ) : null}
        </DialogHeader>
        {children}
        {footer ? <DialogFooter>{footer}</DialogFooter> : null}
      </DialogContent>
    </Dialog>
  )
}
