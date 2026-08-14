import { useMemo, useState } from 'react'
import { ArrowLeftRight } from 'lucide-react'
import type { RayHit, ReceiverSpec, ScenePayload } from '@/api'
import { Button } from '@/components/ui/button'
import { rayObjectDisplayName } from '@/features/raytracing/ray-tracing-model'

import { renderRaySectionImage } from './ray-section-view'

interface RaySectionImageProps {
  scene: ScenePayload
  receiver: ReceiverSpec
  storedPaths: RayHit[][]
  roiFaceIds?: number[]
}

const legendEntries = [
  { color: '#0e7490', label: 'Receiver (정센터 · normal 방향)' },
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
  const dataUrl = useMemo(
    () =>
      renderRaySectionImage({
        scene,
        receiver,
        storedPaths,
        roiFaceIds,
        reverseDirection,
      }),
    [scene, receiver, storedPaths, roiFaceIds, reverseDirection],
  )
  const label = rayObjectDisplayName(
    'receiver',
    receiver.receiver_id,
    receiver.display_name,
  )

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background/40">
      <div className="flex items-center justify-between gap-2 border-b border-border bg-muted/20 px-3 py-1.5">
        <span className="text-sm font-semibold">{label}</span>
        <Button
          type="button"
          size="xs"
          variant={reverseDirection ? 'secondary' : 'outline'}
          aria-pressed={reverseDirection}
          onClick={() => setReverseDirection((value) => !value)}
          title="동일한 단면을 반대편에서 봅니다"
        >
          <ArrowLeftRight />
          방향 반전 {reverseDirection ? 'ON' : 'OFF'}
        </Button>
      </div>
      {dataUrl ? (
        <>
          <img
            src={dataUrl}
            alt={`${label} ray section view`}
            className="block w-full"
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
              기구 부품은 Component에 지정된 색상 그대로 표시됩니다
            </span>
          </div>
        </>
      ) : (
        <p className="p-4 text-center text-xs leading-5 text-muted-foreground">
          이 Receiver 방향에서는 section view를 생성할 수 없습니다.
        </p>
      )}
    </div>
  )
}
