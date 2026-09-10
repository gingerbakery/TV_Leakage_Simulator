import { useRef, type RefObject } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export interface ProjectFileNotice {
  operation: 'save' | 'load'
  fileName: string
  includesCad: boolean
  includesResult: boolean
  downloadStarted?: boolean
  notes?: string[]
}

interface ProjectFileDialogProps {
  notice: ProjectFileNotice | null
  onClose(): void
  returnFocusRef: RefObject<HTMLElement | null>
}

export function ProjectFileDialog({ notice, onClose, returnFocusRef }: ProjectFileDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null)
  const isLoad = notice?.operation === 'load'

  return (
    <Dialog open={notice !== null} onOpenChange={(open) => { if (!open) onClose() }}>
      <DialogContent
        className="min-w-0 gap-4 border border-border bg-popover p-5 text-popover-foreground shadow-xl sm:max-w-[26.25rem]"
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          confirmRef.current?.focus()
        }}
        onCloseAutoFocus={(event) => {
          if (!returnFocusRef.current) return
          event.preventDefault()
          returnFocusRef.current.focus()
        }}
        onKeyDown={(event) => {
          if (event.key !== 'Enter' || event.nativeEvent.isComposing || event.defaultPrevented ||
              event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return
          event.preventDefault()
          event.stopPropagation()
          onClose()
        }}
      >
        <DialogHeader className="min-w-0 gap-2 pr-6">
          <DialogTitle>{isLoad ? '불러오기 완료' : notice?.downloadStarted ? '다운로드 시작' : '저장 완료'}</DialogTitle>
          <DialogDescription className="text-sm leading-5 text-popover-foreground [overflow-wrap:anywhere]">
            {notice?.fileName}
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0 text-xs leading-5 text-muted-foreground">
          <p className="text-[11px] font-medium">{isLoad ? '불러온 항목' : '함께 저장된 항목'}</p>
          <ul className="mt-1 list-disc pl-4">
            {notice?.includesCad && <li>{isLoad ? 'CAD 모델 형상' : '원본 CAD 및 모델 형상'}</li>}
            <li>{notice?.includesResult ? '해석 설정 및 결과' : '해석 설정'}</li>
          </ul>
          {!isLoad && notice && !notice.includesCad && <p className="mt-2">원본 CAD 파일은 별도로 보관해 주세요.</p>}
          {!isLoad && notice?.downloadStarted && <p className="mt-2">브라우저에서 다운로드 완료를 확인해 주세요.</p>}
          {notice?.notes?.map((note) => <p key={note} className="mt-2 [overflow-wrap:anywhere]">{note}</p>)}
        </div>
        <div className="flex justify-end pt-1">
          <Button ref={confirmRef} variant="outline" className="min-w-20" onClick={onClose}>확인</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
