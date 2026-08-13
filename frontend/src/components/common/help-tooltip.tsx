import type { ReactNode } from 'react'
import { CircleHelp } from 'lucide-react'

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'

export interface HelpTooltipProps {
  label: string
  children: ReactNode
}

/** A small "?" icon that reveals guide text on hover/focus - for putting
 *  next to a feature/section title without cluttering the layout when the
 *  user doesn't need it. */
export function HelpTooltip({ label, children }: HelpTooltipProps) {
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <button
            type="button"
            aria-label={label}
            className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
          >
            <CircleHelp className="size-3.5" />
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
