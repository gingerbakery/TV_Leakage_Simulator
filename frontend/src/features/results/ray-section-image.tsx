import { useEffect, useMemo, useState } from 'react'
import { ArrowLeftRight } from 'lucide-react'

import type { RayHit, ReceiverSpec, ScenePayload } from '@/api'
import { Button } from '@/components/ui/button'
import { rayObjectDisplayName } from '@/features/raytracing/ray-tracing-model'

import {
  renderRaySectionImage,
  type ReceiverSectionAxis,
} from './ray-section-view'

interface RaySectionImageProps {
  scene: ScenePayload
  receiver: ReceiverSpec
  storedPaths: RayHit[][]
  roiFaceIds?: number[]
}

const legendEntries = [
  { color: '#0e7490', label: 'Receiver (중심 · normal 방향)' },
  { color: '#15803d', label: 'Direct' },
  { color: '#b45309', label: '반사광' },
]

export function RaySectionImage({
  scene,
  receiver,
  storedPaths,
  roiFaceIds,
}: RaySectionImageProps) {
  const [reverseDirection, setReverseDirection] = useState(false)
  const [sectionAxis, setSectionAxis] = useState<ReceiverSectionAxis>('u')
  const [sectionOffsetMm, setSectionOffsetMm] = useState(0)
  const [renderOffsetMm, setRenderOffsetMm] = useState(0)

  const sectionSizeMm =
    sectionAxis === 'u' ? receiver.width_mm : receiver.height_mm
  const sectionResolution = Math.max(
    sectionAxis === 'u' ? receiver.resolution[0] : receiver.resolution[1],
    1,
  )
  const sectionThicknessMm = Math.max(sectionSizeMm / sectionResolution, 0.1)
  const halfRangeMm = Math.max(sectionSizeMm / 2, 0.1)
  const sliderStepMm = Math.max(sectionThicknessMm / 2, 0.1)

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setRenderOffsetMm(sectionOffsetMm)
    }, 180)
    return () => window.clearTimeout(timer)
  }, [sectionOffsetMm])

  useEffect(() => {
    setSectionOffsetMm(0)
    setRenderOffsetMm(0)
  }, [receiver.receiver_id, sectionAxis])

  const dataUrl = useMemo(
    () =>
      renderRaySectionImage({
        scene,
        receiver,
        storedPaths,
        roiFaceIds,
        reverseDirection,
        sectionAxis,
        sectionOffsetMm: renderOffsetMm,
        sectionThicknessMm,
      }),
    [
      scene,
      receiver,
      storedPaths,
      roiFaceIds,
      reverseDirection,
      sectionAxis,
      renderOffsetMm,
      sectionThicknessMm,
    ],
  )
  const label = rayObjectDisplayName(
    'receiver',
    receiver.receiver_id,
    receiver.display_name,
  )

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background/40">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/20 px-3 py-1.5">
        <span className="text-sm font-semibold">{label}</span>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <div className="flex overflow-hidden rounded-lg border border-border bg-background">
            {(['u', 'v'] as const).map((axis) => (
              <Button
                key={axis}
                type="button"
                size="xs"
                variant={sectionAxis === axis ? 'secondary' : 'ghost'}
                className="rounded-none border-0 px-3 uppercase"
                aria-pressed={sectionAxis === axis}
                onClick={() => setSectionAxis(axis)}
                title={`Receiver Local ${axis.toUpperCase()}축을 따라 단면을 이동합니다.`}
              >
                {axis.toUpperCase()} 단면
              </Button>
            ))}
          </div>
          <label className="flex min-w-72 items-center gap-2 text-xs text-muted-foreground">
            <span className="whitespace-nowrap">단면 위치</span>
            <input
              type="range"
              min={-halfRangeMm}
              max={halfRangeMm}
              step={sliderStepMm}
              value={sectionOffsetMm}
              onChange={(event) =>
                setSectionOffsetMm(Number(event.target.value))
              }
              className="h-1.5 min-w-36 flex-1 cursor-pointer accent-orange-500"
              aria-label={`${label} 단면 위치`}
            />
            <span className="w-20 text-right font-mono text-foreground">
              {sectionOffsetMm >= 0 ? '+' : ''}
              {sectionOffsetMm.toFixed(1)} mm
            </span>
          </label>
          <Button
            type="button"
            size="xs"
            variant={reverseDirection ? 'secondary' : 'outline'}
            aria-pressed={reverseDirection}
            onClick={() => setReverseDirection((value) => !value)}
            title="동일한 단면을 반대편에서 봅니다."
          >
            <ArrowLeftRight />
            방향 반전 {reverseDirection ? 'ON' : 'OFF'}
          </Button>
        </div>
      </div>
      {dataUrl ? (
        <>
          <img
            src={dataUrl}
            alt={`${label} ray section view`}
            className="mx-auto block h-auto w-4/5"
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-border bg-muted/20 px-3 py-1.5 text-xs text-muted-foreground">
            {legendEntries.map((entry) => (
              <span key={entry.label} className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: entry.color }}
                />
                {entry.label}
              </span>
            ))}
            <span className="text-muted-foreground/70">
              단면 CAP은 Component 색상으로 표시되며, 선택 위치의
              Receiver 픽셀 폭 안에 도달한 Stored Ray만 표시됩니다.
            </span>
          </div>
        </>
      ) : (
        <p className="p-4 text-center text-xs leading-5 text-muted-foreground">
          이 Receiver 방향에서는 Section View를 생성할 수 없습니다.
        </p>
      )}
    </div>
  )
}
