import { useRef, useState } from 'react'
import { Box, CircleDot, Info } from 'lucide-react'

import { AppDialog } from '@/components/common'
import { ViewerWorkspace } from '@/components/layout/viewer-workspace'
import {
  WorkflowSidebar,
  type WorkflowSectionId,
} from '@/components/layout/workflow-sidebar'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

export function SimulatorShell() {
  const [activeSection, setActiveSection] =
    useState<WorkflowSectionId>('components')
  const [noticeDialog, setNoticeDialog] = useState<{
    title: string
    description: string
  } | null>(null)
  const noticeReturnFocusRef = useRef<HTMLElement>(null)

  const openFeatureNotice = (title: string, description: string) => {
    if (document.activeElement instanceof HTMLElement) {
      noticeReturnFocusRef.current = document.activeElement
    }
    setNoticeDialog({ title, description })
  }

  return (
    <div className="grid min-h-svh bg-background text-foreground lg:h-svh lg:grid-rows-[3.25rem_minmax(0,1fr)] lg:overflow-hidden">
      <header className="sticky top-0 z-30 flex h-13 items-center justify-between border-b border-border bg-background/92 px-3 backdrop-blur-xl lg:static lg:px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-8 shrink-0 items-center justify-center rounded-lg border border-primary/30 bg-primary/10 text-primary">
            <Box className="size-4" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold tracking-tight">
              TV Leakage Simulator
            </div>
            <div className="hidden text-[0.68rem] text-muted-foreground sm:block">
              React workspace shell
            </div>
          </div>
          <Badge
            variant="outline"
            className="hidden border-primary/30 bg-primary/10 text-primary md:inline-flex"
          >
            Migration · Layout 06
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <div className="hidden items-center gap-1.5 text-xs text-muted-foreground sm:flex">
            <CircleDot className="size-3 text-primary" aria-hidden="true" />
            API layer ready
          </div>
          <Button
            variant="outline"
            size="sm"
            aria-label="Layout guide"
            onClick={() =>
              openFeatureNotice(
                'Layout migration boundary',
                '이번 단계는 App Shell, Dialog와 Context Menu의 공통 기반만 이전합니다. Component Tree와 Material·Transform 데이터 연결은 다음 단계에서 진행합니다.',
              )
            }
          >
            <Info data-icon="inline-start" />
            <span className="hidden sm:inline">Layout guide</span>
          </Button>
        </div>
      </header>

      <div className="grid min-h-0 lg:grid-cols-[22rem_minmax(0,1fr)]">
        <WorkflowSidebar
          activeSection={activeSection}
          onActiveSectionChange={setActiveSection}
          onFeatureNotice={openFeatureNotice}
        />
        <ViewerWorkspace />
      </div>

      <AppDialog
        open={noticeDialog !== null}
        onOpenChange={(open) => {
          if (!open) setNoticeDialog(null)
        }}
        title={noticeDialog?.title ?? 'Migration notice'}
        description={noticeDialog?.description}
        returnFocusRef={noticeReturnFocusRef}
        footer={
          <Button variant="outline" onClick={() => setNoticeDialog(null)}>
            Close
          </Button>
        }
      >
        <div className="rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs leading-5 text-muted-foreground">
          공통 Dialog는 focus trap, Escape 닫기, 배경 interaction 차단을 Radix
          계층에서 처리합니다.
        </div>
      </AppDialog>
    </div>
  )
}
