import type { ReactNode } from 'react'
import { CircleHelp } from 'lucide-react'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import { cn } from '@/lib/utils'

export interface HelpTooltipProps {
  label: string
  children: ReactNode
  triggerClassName?: string
  iconClassName?: string
}

/** A small "?" icon that reveals guide text on hover/focus - for putting
 *  next to a feature/section title without cluttering the layout when the
 *  user doesn't need it. */
export function HelpTooltip({
  label,
  children,
  triggerClassName,
  iconClassName,
}: HelpTooltipProps) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            className={cn(
              'inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:outline-none',
              triggerClassName,
            )}
          >
            <CircleHelp className={cn('size-3.5', iconClassName)} />
          </button>
        </TooltipTrigger>
        <TooltipContent
          side="right"
          sideOffset={6}
          className="max-w-72 whitespace-normal text-left leading-5"
        >
          {children}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
