import { CircleHelp } from 'lucide-react'
import { Popover } from 'radix-ui'
import type { ValidationAdvice } from './validation-guidance'

/** Brief operational action; supplementary reasoning stays closed by default. */
export function ValidationNextStep({advice,label='검증 다음 단계'}:{advice:ValidationAdvice;label?:string}) {
  return <div className="flex items-center gap-1 text-xs" role="status" aria-label={label}>
    <span>다음: {advice.title}</span>
    <Popover.Root><Popover.Trigger asChild><button type="button" aria-label={`${label} 도움말`}
      className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground hover:bg-primary/10 focus-visible:ring-2 focus-visible:ring-primary/40">
      <CircleHelp className="size-3.5" /></button></Popover.Trigger>
      <Popover.Portal><Popover.Content side="bottom" align="start" sideOffset={6}
        className="z-50 max-h-80 w-80 max-w-[90vw] overflow-auto rounded-md border border-border bg-popover p-3 text-xs leading-5 text-popover-foreground shadow-md">
        <div className="mb-2 font-semibold">{advice.title}</div>
        <ol className="list-decimal space-y-2 pl-4">{advice.steps.map((step,index) => <li key={index}>{step}</li>)}</ol>
      </Popover.Content></Popover.Portal>
    </Popover.Root>
  </div>
}
