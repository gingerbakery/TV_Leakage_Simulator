import { Badge } from '@/components/ui/badge'
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { CheckCircle2, Database, Layers3, Workflow } from 'lucide-react'

const simulationColors = [
  { label: 'Viewer', className: 'bg-sim-viewer' },
  { label: 'Selection', className: 'bg-selection' },
  { label: 'Direct ray', className: 'bg-ray-direct' },
  { label: 'Reflected ray', className: 'bg-ray-reflected' },
  { label: 'Receiver', className: 'bg-receiver' },
]

function App() {
  return (
    <main className="relative min-h-svh overflow-hidden bg-background px-5 py-12 text-foreground sm:px-8 lg:py-20">
      <div
        className="pointer-events-none absolute inset-0 opacity-80"
        aria-hidden="true"
      >
        <div className="absolute left-1/2 top-0 h-96 w-[42rem] -translate-x-1/2 rounded-full bg-primary/10 blur-3xl" />
        <div className="absolute inset-0 bg-[linear-gradient(to_right,var(--border)_1px,transparent_1px),linear-gradient(to_bottom,var(--border)_1px,transparent_1px)] bg-[size:48px_48px] opacity-15" />
      </div>

      <section className="relative mx-auto flex w-full max-w-5xl flex-col gap-8">
        <header className="space-y-5">
          <Badge
            variant="outline"
            className="border-primary/30 bg-primary/10 text-primary"
          >
            <CheckCircle2 data-icon="inline-start" />
            Framework migration · Step 06
          </Badge>
          <div className="max-w-3xl space-y-4">
            <h1 className="text-4xl font-semibold tracking-[-0.04em] text-balance sm:text-6xl">
              TV Leakage Simulator
            </h1>
            <p className="max-w-2xl text-base leading-7 text-muted-foreground sm:text-lg">
              서버 상태와 UI 상태를 분리하는 프론트엔드 상태 계층이 새 React
              작업 공간에 구성되었습니다.
            </p>
          </div>
        </header>

        <div className="grid gap-4 md:grid-cols-3">
          <Card className="border-border/80 bg-card/80 backdrop-blur">
            <CardHeader>
              <Database className="size-5 text-primary" aria-hidden="true" />
              <CardTitle>TanStack Query</CardTitle>
              <CardDescription>
                Scene, Ray Trace job과 결과 등 Python 서버 상태를 캐시하고
                동기화합니다.
              </CardDescription>
            </CardHeader>
          </Card>

          <Card className="border-border/80 bg-card/80 backdrop-blur">
            <CardHeader>
              <Layers3 className="size-5 text-primary" aria-hidden="true" />
              <CardTitle>Zustand</CardTitle>
              <CardDescription>
                CAD 작업 세션과 face/component 선택·표시 상태를 독립적으로
                관리합니다.
              </CardDescription>
            </CardHeader>
          </Card>

          <Card className="border-border/80 bg-card/80 backdrop-blur">
            <CardHeader>
              <Workflow className="size-5 text-primary" aria-hidden="true" />
              <CardTitle>State boundary</CardTitle>
              <CardDescription>
                API 응답을 UI store에 복제하지 않아 불필요한 동기화와 재요청을
                줄입니다.
              </CardDescription>
            </CardHeader>
          </Card>
        </div>

        <Card className="border-border/80 bg-card/90 shadow-2xl shadow-black/20">
          <CardHeader>
            <CardTitle>Simulation color semantics</CardTitle>
            <CardDescription>
              이후 Viewer, Ray path, Receiver 시각화가 공유할 기본 의미 색상입니다.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Separator className="mb-5" />
            <div className="flex flex-wrap gap-3">
              {simulationColors.map((color) => (
                <div
                  key={color.label}
                  className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-sm text-muted-foreground"
                >
                  <span
                    className={`size-2.5 rounded-full ${color.className}`}
                    aria-hidden="true"
                  />
                  {color.label}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        <p className="text-sm text-muted-foreground">
          기존 WebView2 화면은 아직 변경되지 않았습니다. 다음 기능 이전부터
          이 상태 계층을 통해 CAD와 Viewer UI가 실제로 연결됩니다.
        </p>
      </section>
    </main>
  )
}

export default App
