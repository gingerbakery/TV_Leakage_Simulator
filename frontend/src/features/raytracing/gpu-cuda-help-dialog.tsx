import {
  CheckCircle2,
  CircleHelp,
  FileText,
  Gauge,
  MonitorCog,
  ShieldCheck,
} from 'lucide-react'

import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'

const helpCardClassName =
  'rounded-lg border border-border bg-muted/35 p-3'

export function GpuCudaHelpDialog() {
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button
          type="button"
          aria-label="GPU CUDA 가속 도움말 열기"
          className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-primary/10 hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40"
        >
          <CircleHelp className="size-3.5" aria-hidden="true" />
        </button>
      </DialogTrigger>

      <DialogContent className="simulator-popup-typography gap-3 border border-border bg-popover/98 shadow-2xl shadow-black/40 sm:max-w-lg">
        <DialogHeader className="gap-2 pr-8">
          <div className="flex flex-wrap items-center gap-2">
            <Gauge className="size-5 text-primary" aria-hidden="true" />
            <DialogTitle>NVIDIA CUDA GPU 가속</DialogTitle>
            <Badge variant="secondary">선택 기능</Badge>
          </div>
          <DialogDescription>
            대규모 Ray 해석을 지원 NVIDIA GPU로 가속합니다.
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-2 sm:grid-cols-3">
          <section className={helpCardClassName} aria-labelledby="gpu-requirements-title">
            <MonitorCog
              className="mb-2 size-5 text-primary"
              aria-hidden="true"
            />
            <h3 id="gpu-requirements-title" className="text-sm font-semibold">
              요구사항
            </h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              GPU CUDA 패키지, 지원 NVIDIA GPU, GPU와 호환되는 NVIDIA
              드라이버, CUDA Toolkit 13.1
            </p>
          </section>

          <section className={helpCardClassName} aria-labelledby="gpu-setup-title">
            <CheckCircle2
              className="mb-2 size-5 text-receiver"
              aria-hidden="true"
            />
            <h3 id="gpu-setup-title" className="text-sm font-semibold">
              설치 · 검증
            </h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              <code>CHECK_GPU_CUDA.bat</code> 통과 후 이 설정에서 NVIDIA CUDA
              GPU를 선택하세요.
            </p>
          </section>

          <section className={helpCardClassName} aria-labelledby="gpu-fallback-title">
            <ShieldCheck
              className="mb-2 size-5 text-warning"
              aria-hidden="true"
            />
            <h3 id="gpu-fallback-title" className="text-sm font-semibold">
              안전한 fallback
            </h3>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              GPU를 쓸 수 없거나 작업이 실패하면 해당 작업 단위를 CPU로 다시
              계산합니다.
            </p>
          </section>
        </div>

        <div className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 px-3 py-2 text-xs">
          <FileText className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <span className="text-muted-foreground">상세 매뉴얼</span>
          <code className="font-semibold text-foreground">
            docs/gpu-cuda-user-guide.md
          </code>
        </div>

        <DialogFooter className="-mx-4 -mb-4 mt-1">
          <DialogClose asChild>
            <Button variant="outline">확인</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
