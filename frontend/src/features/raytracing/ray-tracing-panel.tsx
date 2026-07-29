import { useEffect, useMemo, useRef, useState } from 'react'
import type {
  EmitterDistribution,
  EmitterPowerMode,
  EmitterSpec,
  RayTraceConfigRequest,
  ReceiverSpec,
  ScenePayload,
  Vec3,
} from '@/api'
import {
  Activity,
  Aperture,
  Camera,
  CircleDot,
  Lightbulb,
  LoaderCircle,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Trash2,
} from 'lucide-react'

import {
  useRayTraceJobQuery,
  useStartRayTraceMutation,
} from '@/api'
import { AppDialog } from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import {
  maxReflectionDepth,
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

import {
  buildRayTraceRequest,
  createCurrentViewReceiver,
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
  nextSpecId,
  planeAxesFromRotation,
  rotationFromPlaneAxes,
  type ViewerCameraFrame,
} from './ray-tracing-model'

export interface RayObjectEditRequest {
  id: string
  kind: 'emitter' | 'receiver'
}

interface RayTracingPanelProps {
  scene?: ScenePayload
  cameraFrame: ViewerCameraFrame | null
  editRequest?: RayObjectEditRequest | null
  onEditRequestHandled?(): void
}

type EmitterCreationMode = 'face' | 'datum_plane'
type ReceiverCreationMode = 'datum_plane' | 'current_view'

const receiverDefaultSizeMm = 30
const currentViewDefaultDistanceMm = 30

const inputClassName =
  'h-8 w-full rounded-lg border border-input bg-background px-2.5 text-xs outline-none focus:border-primary focus:ring-2 focus:ring-primary/20'
const fieldLabelClassName = 'space-y-1 text-[0.68rem] font-medium'

function sceneCenter(scene: ScenePayload | undefined): Vec3 {
  if (!scene || scene.components.length === 0) return [0, 0, 0]
  const minimum: Vec3 = [Infinity, Infinity, Infinity]
  const maximum: Vec3 = [-Infinity, -Infinity, -Infinity]
  for (const component of scene.components) {
    for (let axis = 0; axis < 3; axis += 1) {
      minimum[axis] = Math.min(minimum[axis], component.bbox_min[axis])
      maximum[axis] = Math.max(maximum[axis], component.bbox_max[axis])
    }
  }
  return [
    (minimum[0] + maximum[0]) / 2,
    (minimum[1] + maximum[1]) / 2,
    (minimum[2] + maximum[2]) / 2,
  ]
}

function NumberField({
  label,
  ariaLabel,
  value,
  onChange,
  min,
  max,
  step = 'any',
  disabled = false,
}: {
  label: string
  ariaLabel?: string
  value: number
  onChange(value: number): void
  min?: number
  max?: number
  step?: number | 'any'
  disabled?: boolean
}) {
  return (
    <label className={fieldLabelClassName}>
      <span>{label}</span>
      <NumberInput
        className={inputClassName}
        aria-label={ariaLabel ?? label}
        value={value}
        min={min}
        max={max}
        step={step}
        disabled={disabled}
        onValueChange={onChange}
      />
    </label>
  )
}

function VectorFields({
  label,
  labels,
  value,
  onChange,
}: {
  label: string
  labels: [string, string, string]
  value: Vec3
  onChange(value: Vec3): void
}) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="text-[0.68rem] font-semibold text-muted-foreground">
        {label}
      </legend>
      <div className="grid grid-cols-3 gap-2">
        {labels.map((axisLabel, axis) => (
          <NumberField
            key={axisLabel}
            label={axisLabel}
            value={value[axis]}
            onChange={(nextValue) => {
              const next: Vec3 = [...value]
              next[axis] = Number.isFinite(nextValue) ? nextValue : 0
              onChange(next)
            }}
          />
        ))}
      </div>
    </fieldset>
  )
}

function EmitterDialog({
  open,
  mode,
  scene,
  selectedFaceIds,
  existingIds,
  initialEmitter,
  onOpenChange,
  onApply,
}: {
  open: boolean
  mode: EmitterCreationMode
  scene?: ScenePayload
  selectedFaceIds: number[]
  existingIds: string[]
  initialEmitter?: EmitterSpec | null
  onOpenChange(open: boolean): void
  onApply(emitter: EmitterSpec): void
}) {
  const defaultCenter = useMemo(() => sceneCenter(scene), [scene])
  const [center, setCenter] = useState<Vec3>(defaultCenter)
  const [rotation, setRotation] = useState<Vec3>([0, 0, 0])
  const [width, setWidth] = useState(20)
  const [height, setHeight] = useState(20)
  const [powerMode, setPowerMode] =
    useState<EmitterPowerMode>('total')
  const [power, setPower] = useState(1)
  const [powerDensity, setPowerDensity] = useState(100)
  const [rayCount, setRayCount] = useState(10_000)
  const [distribution, setDistribution] =
    useState<EmitterDistribution>('lambertian')
  const [sigma, setSigma] = useState(12)
  const [normalFlip, setNormalFlip] = useState(false)
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  useEffect(() => {
    if (!open) return
    setCenter(initialEmitter?.center ?? defaultCenter)
    setRotation(
      rotationFromPlaneAxes(
        initialEmitter?.u_axis ?? null,
        initialEmitter?.v_axis ?? null,
        initialEmitter?.custom_normal ?? null,
      ),
    )
    setWidth(initialEmitter?.width_mm ?? 20)
    setHeight(initialEmitter?.height_mm ?? 20)
    setPowerMode(initialEmitter?.power_mode ?? 'total')
    setPower(initialEmitter?.power_lumen ?? 1)
    setPowerDensity(
      initialEmitter?.power_density_lm_per_m2 ?? 100,
    )
    setRayCount(initialEmitter?.ray_count ?? 10_000)
    setDistribution(
      initialEmitter?.direction_distribution ?? 'lambertian',
    )
    setSigma(initialEmitter?.gaussian_sigma_deg ?? 12)
    setNormalFlip(initialEmitter?.normal_flip ?? false)
  }, [defaultCenter, initialEmitter, open])

  const emitterFaceIds =
    initialEmitter?.face_indices ?? selectedFaceIds
  const canApply =
    mode === 'datum_plane' || emitterFaceIds.length > 0
  const previewEmitter = useMemo(() => {
    if (!open || mode !== 'datum_plane') return null
    const emitter = createDatumEmitter(
      initialEmitter?.emitter_id ??
        '__placement_preview_emitter__',
      center,
      rotation,
    )
    const axes = planeAxesFromRotation(rotation)
    return {
      ...emitter,
      center,
      u_axis: axes.uAxis,
      v_axis: axes.vAxis,
      custom_normal: axes.normal,
      width_mm: Math.max(0.001, width),
      height_mm: Math.max(0.001, height),
      normal_flip: normalFlip,
      enabled: initialEmitter?.enabled ?? true,
    }
  }, [
    center,
    height,
    initialEmitter,
    mode,
    normalFlip,
    open,
    rotation,
    width,
  ])

  useEffect(() => {
    actions.setPlacementPreviewEmitter(previewEmitter)
  }, [actions, previewEmitter])

  useEffect(
    () => () => actions.setPlacementPreviewEmitter(null),
    [actions],
  )

  const handleApply = () => {
    if (!canApply) return
    const emitterId =
      initialEmitter?.emitter_id ??
      nextSpecId('emitter', existingIds)
    const emitter =
      mode === 'face'
        ? createFaceEmitter(emitterId, emitterFaceIds)
        : createDatumEmitter(emitterId, center, rotation)
    const axes = planeAxesFromRotation(rotation)
    onApply({
      ...initialEmitter,
      ...emitter,
      ...(mode === 'datum_plane'
        ? {
            center,
            u_axis: axes.uAxis,
            v_axis: axes.vAxis,
            custom_normal: axes.normal,
            width_mm: Math.max(0.001, width),
            height_mm: Math.max(0.001, height),
          }
        : {}),
      power_mode: powerMode,
      power_lumen: Math.max(0, power),
      power_density_lm_per_m2: Math.max(0, powerDensity),
      ray_count: Math.max(1, Math.trunc(rayCount)),
      direction_distribution: distribution,
      gaussian_sigma_deg: Math.max(0.1, sigma),
      normal_flip: normalFlip,
      enabled: initialEmitter?.enabled ?? true,
    })
    onOpenChange(false)
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title={
        initialEmitter
          ? `Edit ${initialEmitter.emitter_id}`
          : mode === 'face'
            ? 'CAD surface emitter'
            : 'Datum plane emitter'
      }
      description={
        mode === 'face'
          ? '현재 Viewer에서 선택한 triangle face를 실제 발광면으로 등록합니다.'
          : 'CAD가 없는 공간에 좌표와 회전으로 가상 사각 발광면을 배치합니다.'
      }
      size="lg"
      onSubmit={handleApply}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canApply} onClick={handleApply}>
            {initialEmitter ? 'Save emitter' : 'Add emitter'}
          </Button>
        </>
      }
    >
      <div className="max-h-[66vh] space-y-4 overflow-y-auto pr-1">
        {mode === 'face' ? (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="text-xs font-semibold">
              {initialEmitter ? 'Emitter faces' : 'Selected faces'} ·{' '}
              {emitterFaceIds.length.toLocaleString()}
            </div>
            <p className="mt-1 text-[0.68rem] leading-4 text-muted-foreground">
              {initialEmitter
                ? '기존 CAD 발광면은 유지됩니다. 발광면을 교체하려면 새 CAD surface Emitter를 생성하세요.'
                : '이 패널을 열어 둔 채 Viewer 면을 클릭하세요. Shift를 누르면 여러 면을 추가 선택할 수 있습니다.'}
            </p>
          </div>
        ) : (
          <>
            <VectorFields
              label="Center (mm)"
              labels={['Center X', 'Center Y', 'Center Z']}
              value={center}
              onChange={setCenter}
            />
            <VectorFields
              label="Rotation (deg)"
              labels={['Rotation X', 'Rotation Y', 'Rotation Z']}
              value={rotation}
              onChange={setRotation}
            />
            <div className="grid grid-cols-2 gap-2">
              <NumberField
                label="Emitter width (mm)"
                value={width}
                min={0.001}
                onChange={setWidth}
              />
              <NumberField
                label="Emitter height (mm)"
                value={height}
                min={0.001}
                onChange={setHeight}
              />
            </div>
          </>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          <label className={fieldLabelClassName}>
            <span>Power mode</span>
            <select
              className={inputClassName}
              aria-label="Emitter power mode"
              value={powerMode}
              onChange={(event) =>
                setPowerMode(event.currentTarget.value as EmitterPowerMode)
              }
            >
              <option value="total">Total power</option>
              <option value="power_per_area">Power per area</option>
            </select>
          </label>
          {powerMode === 'total' ? (
            <NumberField
              label="Total power (lm)"
              value={power}
              min={0}
              onChange={setPower}
            />
          ) : (
            <NumberField
              label="Power density (lm/m²)"
              value={powerDensity}
              min={0}
              onChange={setPowerDensity}
            />
          )}
          <NumberField
            label="Emitter rays"
            value={rayCount}
            min={1}
            step={1000}
            onChange={setRayCount}
          />
          <label className={fieldLabelClassName}>
            <span>Direction distribution</span>
            <select
              className={inputClassName}
              aria-label="Emitter direction distribution"
              value={distribution}
              onChange={(event) =>
                setDistribution(
                  event.currentTarget.value as EmitterDistribution,
                )
              }
            >
              <option value="lambertian">Lambertian</option>
              <option value="isotropic">Isotropic</option>
              <option value="gaussian">Gaussian</option>
            </select>
          </label>
          {distribution === 'gaussian' ? (
            <NumberField
              label="Gaussian sigma (deg)"
              value={sigma}
              min={0.1}
              onChange={setSigma}
            />
          ) : null}
        </div>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={normalFlip}
            onChange={(event) => setNormalFlip(event.currentTarget.checked)}
          />
          Flip normal direction
        </label>
      </div>
    </AppDialog>
  )
}

function ReceiverDialog({
  open,
  mode,
  scene,
  cameraFrame,
  existingIds,
  initialReceiver,
  onOpenChange,
  onApply,
}: {
  open: boolean
  mode: ReceiverCreationMode
  scene?: ScenePayload
  cameraFrame: ViewerCameraFrame | null
  existingIds: string[]
  initialReceiver?: ReceiverSpec | null
  onOpenChange(open: boolean): void
  onApply(receiver: ReceiverSpec): void
}) {
  const defaultCenter = useMemo(() => sceneCenter(scene), [scene])
  const [displayName, setDisplayName] = useState('')
  const [center, setCenter] = useState<Vec3>(defaultCenter)
  const [rotation, setRotation] = useState<Vec3>([0, 0, 0])
  const [width, setWidth] = useState(receiverDefaultSizeMm)
  const [height, setHeight] = useState(receiverDefaultSizeMm)
  const [resolutionX, setResolutionX] = useState(80)
  const [resolutionY, setResolutionY] = useState(24)
  const [acceptance, setAcceptance] = useState(90)
  const [viewDistance, setViewDistance] = useState(
    currentViewDefaultDistanceMm,
  )
  const [positionOffset, setPositionOffset] = useState<Vec3>([0, 0, 0])
  const [tilt, setTilt] = useState<Vec3>([0, 0, 0])
  const [capturedFrame, setCapturedFrame] =
    useState<ViewerCameraFrame | null>(null)
  const [normalFlip, setNormalFlip] = useState(false)
  const cameraFrameRef = useRef(cameraFrame)
  const actions = useWorkspaceStore(workspaceSelectors.actions)

  useEffect(() => {
    cameraFrameRef.current = cameraFrame
  }, [cameraFrame])

  useEffect(() => {
    if (!open) return
    const initialDistance =
      initialReceiver?.view_distance_mm ??
      currentViewDefaultDistanceMm
    setDisplayName(initialReceiver?.display_name ?? '')
    setCenter(initialReceiver?.center ?? defaultCenter)
    setRotation(
      rotationFromPlaneAxes(
        initialReceiver?.u_axis ?? null,
        initialReceiver?.v_axis ?? null,
        initialReceiver?.normal ?? null,
      ),
    )
    setWidth(initialReceiver?.width_mm ?? receiverDefaultSizeMm)
    setHeight(initialReceiver?.height_mm ?? receiverDefaultSizeMm)
    setResolutionX(initialReceiver?.resolution[0] ?? 80)
    setResolutionY(initialReceiver?.resolution[1] ?? 24)
    setAcceptance(initialReceiver?.acceptance_angle_deg ?? 90)
    setViewDistance(initialDistance)
    setPositionOffset(initialReceiver?.position_offset_mm ?? [0, 0, 0])
    setTilt(initialReceiver?.tilt_xyz_deg ?? [0, 0, 0])
    setNormalFlip(initialReceiver?.normal_flip ?? false)
    if (mode === 'current_view' && initialReceiver) {
      const normal =
        initialReceiver.base_normal ?? initialReceiver.normal
      const uAxis =
        initialReceiver.base_u_axis ??
        initialReceiver.u_axis
      const vAxis =
        initialReceiver.base_v_axis ??
        initialReceiver.v_axis
      const baseCenter =
        initialReceiver.base_center ?? initialReceiver.center
      setCapturedFrame(
        uAxis && vAxis
          ? {
              target: [
                baseCenter[0] + normal[0] * initialDistance,
                baseCenter[1] + normal[1] * initialDistance,
                baseCenter[2] + normal[2] * initialDistance,
              ],
              normal: [...normal],
              uAxis: [...uAxis],
              vAxis: [...vAxis],
            }
          : cameraFrameRef.current,
      )
    } else {
      setCapturedFrame(cameraFrameRef.current)
    }
  }, [
    defaultCenter,
    initialReceiver,
    mode,
    open,
  ])

  const canApply = mode === 'datum_plane' || capturedFrame !== null
  const previewReceiver = useMemo(() => {
    if (!open) return null
    const receiver =
      mode === 'current_view' && capturedFrame
        ? createCurrentViewReceiver(
            initialReceiver?.receiver_id ??
              '__placement_preview_receiver__',
            capturedFrame,
            Math.max(0.001, viewDistance),
            positionOffset,
            tilt,
          )
        : createDatumReceiver(
            initialReceiver?.receiver_id ??
              '__placement_preview_receiver__',
            center,
            rotation,
          )
    const axes = planeAxesFromRotation(rotation)
    return {
      ...receiver,
      ...(mode === 'datum_plane'
        ? {
            center,
            normal: axes.normal,
            u_axis: axes.uAxis,
            v_axis: axes.vAxis,
          }
        : {}),
      width_mm: Math.max(0.001, width),
      height_mm: Math.max(0.001, height),
      normal_flip: normalFlip,
      enabled: initialReceiver?.enabled ?? true,
    }
  }, [
    capturedFrame,
    center,
    height,
    initialReceiver,
    mode,
    normalFlip,
    open,
    positionOffset,
    rotation,
    tilt,
    viewDistance,
    width,
  ])

  useEffect(() => {
    actions.setPlacementPreviewReceiver(previewReceiver)
  }, [actions, previewReceiver])

  useEffect(
    () => () => actions.setPlacementPreviewReceiver(null),
    [actions],
  )

  const handleApply = () => {
    if (!canApply) return
    const receiverId =
      initialReceiver?.receiver_id ??
      nextSpecId('receiver', existingIds)
    const receiver =
      mode === 'current_view' && capturedFrame
        ? createCurrentViewReceiver(
            receiverId,
            capturedFrame,
            Math.max(0.001, viewDistance),
            positionOffset,
            tilt,
          )
        : createDatumReceiver(receiverId, center, rotation)
    const axes = planeAxesFromRotation(rotation)
    onApply({
      ...initialReceiver,
      ...receiver,
      display_name: displayName.trim() || receiverId,
      ...(mode === 'datum_plane'
        ? {
            center,
            normal: axes.normal,
            u_axis: axes.uAxis,
            v_axis: axes.vAxis,
          }
        : {}),
      width_mm: Math.max(0.001, width),
      height_mm: Math.max(0.001, height),
      resolution: [
        Math.max(1, Math.trunc(resolutionX)),
        Math.max(1, Math.trunc(resolutionY)),
      ],
      acceptance_angle_deg: Math.max(0.1, Math.min(180, acceptance)),
      normal_flip: normalFlip,
      enabled: initialReceiver?.enabled ?? true,
    })
    onOpenChange(false)
  }

  return (
    <AppDialog
      open={open}
      onOpenChange={onOpenChange}
      floating
      title={
        initialReceiver
          ? `Edit ${initialReceiver.display_name}`
          : mode === 'current_view'
            ? 'Current view receiver'
            : 'Datum plane receiver'
      }
      description={
        mode === 'current_view'
          ? '현재 메인 Viewer의 카메라 방향과 화면 수평축을 수광면 좌표계로 저장합니다.'
          : '중심 좌표와 회전으로 가상 사각 수광면을 배치합니다.'
      }
      size="lg"
      onSubmit={handleApply}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canApply} onClick={handleApply}>
            {initialReceiver ? 'Save receiver' : 'Add receiver'}
          </Button>
        </>
      }
    >
      <div className="max-h-[66vh] space-y-4 overflow-y-auto pr-1">
        <label className={fieldLabelClassName}>
          <span>Receiver name</span>
          <input
            className={inputClassName}
            aria-label="Receiver name"
            value={displayName}
            placeholder="Receiver 1"
            onChange={(event) => setDisplayName(event.currentTarget.value)}
          />
        </label>
        {mode === 'datum_plane' ? (
          <>
            <VectorFields
              label="Center (mm)"
              labels={['Receiver center X', 'Receiver center Y', 'Receiver center Z']}
              value={center}
              onChange={setCenter}
            />
            <VectorFields
              label="Rotation (deg)"
              labels={['Receiver rotation X', 'Receiver rotation Y', 'Receiver rotation Z']}
              value={rotation}
              onChange={setRotation}
            />
          </>
        ) : (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="flex items-center gap-2 text-xs font-semibold">
              <Camera className="size-3.5 text-primary" />
              {capturedFrame ? 'Receiver view captured' : 'Camera unavailable'}
            </div>
            <div className="mt-3 flex items-end gap-2">
              <div className="max-w-40 flex-1">
                <NumberField
                  label="View distance (mm)"
                  value={viewDistance}
                  min={0.001}
                  onChange={setViewDistance}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={!cameraFrame}
                onClick={() => setCapturedFrame(cameraFrame)}
              >
                <RefreshCw />
                Use current camera
              </Button>
            </div>
            {capturedFrame && previewReceiver?.base_center ? (
              <div className="mt-4 space-y-3 border-t border-primary/15 pt-3">
                <VectorFields
                  label="Center (mm)"
                  labels={[
                    'Receiver center X',
                    'Receiver center Y',
                    'Receiver center Z',
                  ]}
                  value={previewReceiver.center}
                  onChange={(nextCenter) =>
                    setPositionOffset([
                      nextCenter[0] - previewReceiver.base_center![0],
                      nextCenter[1] - previewReceiver.base_center![1],
                      nextCenter[2] - previewReceiver.base_center![2],
                    ])
                  }
                />
                <VectorFields
                  label="Tilt (deg)"
                  labels={[
                    'Receiver tilt X',
                    'Receiver tilt Y',
                    'Receiver tilt Z',
                  ]}
                  value={tilt}
                  onChange={setTilt}
                />
              </div>
            ) : null}
          </div>
        )}
        <div className="grid grid-cols-2 gap-2">
          <NumberField
            label="Receiver width (mm)"
            value={width}
            min={0.001}
            onChange={setWidth}
          />
          <NumberField
            label="Receiver height (mm)"
            value={height}
            min={0.001}
            onChange={setHeight}
          />
          <NumberField
            label="Resolution X"
            value={resolutionX}
            min={1}
            step={1}
            onChange={setResolutionX}
          />
          <NumberField
            label="Resolution Y"
            value={resolutionY}
            min={1}
            step={1}
            onChange={setResolutionY}
          />
          <NumberField
            label="Acceptance angle (deg)"
            value={acceptance}
            min={0.1}
            max={180}
            onChange={setAcceptance}
          />
        </div>
        {mode === 'current_view' ? (
          <p className="text-[0.68rem] leading-4 text-muted-foreground">
            기본 수광면은 {receiverDefaultSizeMm} ×{' '}
            {receiverDefaultSizeMm} mm이며, View distance는 모델 중심에서
            카메라 방향으로 떨어진 거리입니다.
          </p>
        ) : null}
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={normalFlip}
            onChange={(event) => setNormalFlip(event.currentTarget.checked)}
          />
          Flip receiving normal
        </label>
      </div>
    </AppDialog>
  )
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return 'calculating'
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))}s`
  const minutes = Math.floor(seconds / 60)
  const remainder = Math.round(seconds % 60)
  return `${minutes}m ${remainder}s`
}

export function RayTracingPanel({
  scene,
  cameraFrame,
  editRequest = null,
  onEditRequestHandled,
}: RayTracingPanelProps) {
  const [emitterMode, setEmitterMode] =
    useState<EmitterCreationMode | null>(null)
  const [receiverMode, setReceiverMode] =
    useState<ReceiverCreationMode | null>(null)
  const [editingEmitterId, setEditingEmitterId] =
    useState<string | null>(null)
  const [editingReceiverId, setEditingReceiverId] =
    useState<string | null>(null)
  const selectedFaceIds = useWorkspaceStore(
    workspaceSelectors.selectedFaceIds,
  )
  const emitters = useWorkspaceStore(workspaceSelectors.emitters)
  const receivers = useWorkspaceStore(workspaceSelectors.receivers)
  const materialAssignments = useWorkspaceStore(
    workspaceSelectors.materialAssignments,
  )
  const transformRules = useWorkspaceStore(
    workspaceSelectors.transformRules,
  )
  const excludedComponentIds = useWorkspaceStore(
    workspaceSelectors.excludedComponentIds,
  )
  const deletedComponentIds = useWorkspaceStore(
    workspaceSelectors.deletedComponentIds,
  )
  const roiScopes = useWorkspaceStore(workspaceSelectors.roiScopes)
  const config = useWorkspaceStore(workspaceSelectors.rayTraceConfig)
  const activeJobId = useWorkspaceStore(
    workspaceSelectors.activeRayTraceJobId,
  )
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const editingEmitter =
    emitters.find(
      (emitter) => emitter.emitter_id === editingEmitterId,
    ) ?? null
  const editingReceiver =
    receivers.find(
      (receiver) => receiver.receiver_id === editingReceiverId,
    ) ?? null
  const startMutation = useStartRayTraceMutation()
  const jobQuery = useRayTraceJobQuery(activeJobId)
  const job = jobQuery.data
  const latestResult = job?.status === 'completed' ? job.result : null
  const isRunning =
    startMutation.isPending ||
    job?.status === 'queued' ||
    job?.status === 'running'
  const enabledEmitterCount = emitters.filter(
    (emitter) => emitter.enabled,
  ).length
  const runOptionEmitterRayCount = emitters[0]?.ray_count ?? 10_000
  const enabledEmitterRayCount = emitters
    .filter((emitter) => emitter.enabled)
    .reduce(
      (total, emitter) => total + Math.max(1, emitter.ray_count),
      0,
    )
  const hasMixedEmitterRayCounts =
    new Set(emitters.map((emitter) => emitter.ray_count)).size > 1
  const enabledReceiverCount = receivers.filter(
    (receiver) => receiver.enabled,
  ).length
  const canRun =
    scene !== undefined &&
    enabledEmitterCount > 0 &&
    enabledReceiverCount > 0 &&
    !isRunning

  useEffect(
    () => () => actions.setEmitterFaceSelectionArmed(false),
    [actions],
  )

  useEffect(() => {
    if (!editRequest) return
    actions.setEmitterFaceSelectionArmed(false)
    actions.setSelectedFaceIds([])
    actions.setSelectedComponentIds([])
    if (
      editRequest.kind === 'emitter' &&
      emitters.some(
        (emitter) => emitter.emitter_id === editRequest.id,
      )
    ) {
      setReceiverMode(null)
      setEditingReceiverId(null)
      setEmitterMode(null)
      setEditingEmitterId(editRequest.id)
    } else if (
      editRequest.kind === 'receiver' &&
      receivers.some(
        (receiver) => receiver.receiver_id === editRequest.id,
      )
    ) {
      setEmitterMode(null)
      setEditingEmitterId(null)
      setReceiverMode(null)
      setEditingReceiverId(editRequest.id)
    }
    onEditRequestHandled?.()
  }, [
    actions,
    editRequest,
    emitters,
    onEditRequestHandled,
    receivers,
  ])

  const updateConfig = (patch: Partial<RayTraceConfigRequest>) => {
    actions.setRayTraceConfig({ ...config, ...patch })
  }

  const handleRun = async () => {
    if (!scene || !canRun) return
    const request = buildRayTraceRequest({
      scene,
      projectName: activeCad?.displayName || 'TV-Leakage-Direct',
      emitters,
      receivers,
      materialAssignments,
      transformRules,
      excludedComponentIds,
      deletedComponentIds,
      roiScopes,
      config,
    })
    try {
      const startedJob = await startMutation.mutateAsync({ request })
      actions.setActiveRayTraceJobId(startedJob.job_id)
    } catch {
      // The mutation state renders the backend error in the panel.
    }
  }

  const progress =
    job?.status === 'completed'
      ? 1
      : Math.max(0, Math.min(1, job?.progress ?? 0))
  const errorMessage =
    job?.status === 'failed'
      ? job.error
      : startMutation.error?.message ?? jobQuery.error?.message

  return (
    <div className="space-y-4">
      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
            <Lightbulb className="size-3.5 text-warning" />
            Emitter
          </div>
          <Badge variant="outline">{emitters.length}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <Button
            variant="outline"
            size="sm"
            aria-label="Add CAD surface emitter"
            disabled={!scene || isRunning}
            onClick={() => {
              setEditingEmitterId(null)
              actions.setSelectedFaceIds([])
              actions.setSelectedComponentIds([])
              actions.setEmitterFaceSelectionArmed(true)
              setEmitterMode('face')
            }}
          >
            <Plus />
            CAD surface
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Add datum plane emitter"
            disabled={!scene || isRunning}
            onClick={() => {
              setEditingEmitterId(null)
              actions.setEmitterFaceSelectionArmed(false)
              setEmitterMode('datum_plane')
            }}
          >
            <Plus />
            Datum plane
          </Button>
        </div>
        {emitters.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-[0.68rem] text-muted-foreground">
            등록된 광원이 없습니다.
          </p>
        ) : (
          <div className="space-y-1.5">
            {emitters.map((emitter) => (
              <div
                key={emitter.emitter_id}
                className="flex items-center gap-2 rounded-lg border border-border bg-background/40 p-2"
              >
                <input
                  aria-label={`Enable ${emitter.emitter_id}`}
                  type="checkbox"
                  checked={emitter.enabled}
                  disabled={isRunning}
                  onChange={(event) =>
                    actions.setEmitterEnabled(
                      emitter.emitter_id,
                      event.currentTarget.checked,
                    )
                  }
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-semibold">
                    {emitter.emitter_id}
                  </div>
                  <div className="text-[0.62rem] text-muted-foreground">
                    {emitter.emitter_type === 'face'
                      ? `${emitter.face_indices.length} faces`
                      : `${emitter.width_mm} × ${emitter.height_mm} mm`}
                    {' · '}
                    {emitter.ray_count.toLocaleString()} rays
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Edit ${emitter.emitter_id}`}
                  disabled={isRunning}
                  onClick={() => {
                    actions.setEmitterFaceSelectionArmed(false)
                    actions.setSelectedFaceIds([])
                    actions.setSelectedComponentIds([])
                    setEmitterMode(null)
                    setEditingEmitterId(emitter.emitter_id)
                  }}
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Delete ${emitter.emitter_id}`}
                  disabled={isRunning}
                  onClick={() =>
                    actions.removeEmitter(emitter.emitter_id)
                  }
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
            <Aperture className="size-3.5 text-primary" />
            Receiver
          </div>
          <Badge variant="outline">{receivers.length}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <Button
            variant="outline"
            size="sm"
            aria-label="Add datum plane receiver"
            disabled={!scene || isRunning}
            onClick={() => {
              setEditingReceiverId(null)
              setReceiverMode('datum_plane')
            }}
          >
            <Plus />
            Datum plane
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Add current view receiver"
            disabled={!scene || !cameraFrame || isRunning}
            onClick={() => {
              setEditingReceiverId(null)
              setReceiverMode('current_view')
            }}
          >
            <Camera />
            Current view
          </Button>
        </div>
        {receivers.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-[0.68rem] text-muted-foreground">
            등록된 수광부가 없습니다.
          </p>
        ) : (
          <div className="space-y-1.5">
            {receivers.map((receiver) => (
              <div
                key={receiver.receiver_id}
                className="flex items-center gap-2 rounded-lg border border-border bg-background/40 p-2"
              >
                <input
                  aria-label={`Enable ${receiver.receiver_id}`}
                  type="checkbox"
                  checked={receiver.enabled}
                  disabled={isRunning}
                  onChange={(event) =>
                    actions.setReceiverEnabled(
                      receiver.receiver_id,
                      event.currentTarget.checked,
                    )
                  }
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-semibold">
                    {receiver.display_name}
                  </div>
                  <div className="text-[0.62rem] text-muted-foreground">
                    {receiver.placement_mode.replace('_', ' ')}
                    {' · '}
                    {receiver.width_mm} × {receiver.height_mm} mm
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Edit ${receiver.receiver_id}`}
                  disabled={isRunning}
                  onClick={() => {
                    setReceiverMode(null)
                    setEditingReceiverId(receiver.receiver_id)
                  }}
                >
                  <Pencil />
                </Button>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Delete ${receiver.receiver_id}`}
                  disabled={isRunning}
                  onClick={() =>
                    actions.removeReceiver(receiver.receiver_id)
                  }
                >
                  <Trash2 />
                </Button>
              </div>
            ))}
          </div>
        )}
      </section>

      <section className="space-y-3 border-t border-border pt-3">
        <div className="flex items-center gap-1.5 text-[0.68rem] font-semibold tracking-wide text-muted-foreground uppercase">
          <Activity className="size-3.5" />
          Run options
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="col-span-2 rounded-lg border border-primary/20 bg-primary/5 p-2.5">
            <NumberField
              label="Emitter rays"
              ariaLabel="Run option emitter rays"
              value={runOptionEmitterRayCount}
              min={1}
              step={1000}
              disabled={isRunning || emitters.length === 0}
              onChange={(value) =>
                actions.setEmitterRayCount(value)
              }
            />
            <p className="mt-1.5 text-[0.62rem] leading-4 text-muted-foreground">
              {hasMixedEmitterRayCounts
                ? 'Emitter별 Ray 수가 서로 다릅니다. 값을 변경하면 모든 Emitter에 동일하게 적용됩니다. '
                : '모든 등록 Emitter에 동일하게 적용됩니다. '}
              활성 Emitter 총합{' '}
              {enabledEmitterRayCount.toLocaleString()} rays
            </p>
          </div>
          <NumberField
            label={`Max reflections (0-${maxReflectionDepth})`}
            value={config.max_depth}
            min={0}
            max={maxReflectionDepth}
            step={1}
            disabled={isRunning}
            onChange={(value) =>
              updateConfig({ max_depth: Math.trunc(value) })
            }
          />
          <NumberField
            label="Random seed"
            value={config.seed}
            step={1}
            disabled={isRunning}
            onChange={(value) => updateConfig({ seed: Math.trunc(value) })}
          />
          <NumberField
            label="Minimum energy"
            value={config.min_energy}
            min={0}
            disabled={isRunning}
            onChange={(value) => updateConfig({ min_energy: value })}
          />
          <NumberField
            label="Max stored paths"
            value={config.max_stored_paths}
            min={0}
            max={1000}
            step={10}
            disabled={isRunning}
            onChange={(value) =>
              updateConfig({ max_stored_paths: Math.trunc(value) })
            }
          />
          <label className={fieldLabelClassName}>
            <span>Termination</span>
            <select
              className={inputClassName}
              aria-label="Ray termination mode"
              value={config.termination_mode}
              disabled={isRunning}
              onChange={(event) =>
                updateConfig({
                  termination_mode:
                    event.currentTarget.value === 'russian_roulette'
                      ? 'russian_roulette'
                      : 'threshold',
                })
              }
            >
              <option value="threshold">Energy threshold</option>
              <option value="russian_roulette">Russian roulette</option>
            </select>
          </label>
          <label className={fieldLabelClassName}>
            <span>Contribution</span>
            <select
              className={inputClassName}
              aria-label="Contribution mode"
              value={config.contribution_mode}
              disabled={isRunning}
              onChange={(event) =>
                updateConfig({
                  contribution_mode:
                    event.currentTarget.value === 'detailed'
                      ? 'detailed'
                      : 'summary',
                })
              }
            >
              <option value="summary">Fast summary</option>
              <option value="detailed">Detailed</option>
            </select>
          </label>
        </div>
        <p className="text-[0.68rem] leading-relaxed text-muted-foreground">
          1 for quick checks, 3 for general comparison, 10 for enclosed
          high-reflectance paths, and 20 only for convergence checks.
        </p>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={config.store_ray_paths}
            disabled={isRunning}
            onChange={(event) =>
              updateConfig({
                store_ray_paths: event.currentTarget.checked,
              })
            }
          />
          Store hit ray paths for Step 11 Viewer overlay
        </label>
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        <Button
          className="w-full"
          disabled={!canRun}
          onClick={() => void handleRun()}
        >
          {isRunning ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <Play />
          )}
          {isRunning ? 'Tracing rays…' : 'Run ray tracing'}
        </Button>
        <p className="text-[0.65rem] leading-4 text-muted-foreground">
          Emitter {enabledEmitterCount} · Receiver {enabledReceiverCount} ·
          Rays {enabledEmitterRayCount.toLocaleString()} · ROI{' '}
          {roiScopes.filter((scope) => scope.active).length} scope
        </p>

        {job ? (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="flex items-center justify-between gap-2 text-[0.68rem]">
              <span className="flex items-center gap-1.5 font-semibold">
                <CircleDot className="size-3 text-primary" />
                {job.phase}
              </span>
              <span>{(progress * 100).toFixed(1)}%</span>
            </div>
            <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${progress * 100}%` }}
              />
            </div>
            <div className="mt-2 flex justify-between text-[0.62rem] text-muted-foreground">
              <span>
                {job.processed_rays.toLocaleString()} /{' '}
                {job.total_rays.toLocaleString()} rays
              </span>
              <span>
                {job.status === 'completed'
                  ? `complete · ${formatDuration(job.elapsed_sec)}`
                  : `${formatDuration(job.estimated_remaining_sec)} left`}
              </span>
            </div>
          </div>
        ) : null}

        {latestResult ? (
          <div className="grid grid-cols-3 gap-1.5">
            {[
              ['Rays', latestResult.total_rays.toLocaleString()],
              ['Hits', latestResult.receiver_hit_count.toLocaleString()],
              ['Runtime', `${latestResult.runtime_sec.toFixed(2)}s`],
            ].map(([label, value]) => (
              <div
                key={label}
                className="rounded-lg border border-border bg-background/40 p-2 text-center"
              >
                <div className="text-[0.58rem] text-muted-foreground">
                  {label}
                </div>
                <div className="mt-0.5 text-[0.68rem] font-semibold">
                  {value}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {errorMessage ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/8 p-2 text-[0.68rem] leading-4 text-destructive">
            {errorMessage}
          </p>
        ) : null}
      </section>

      <EmitterDialog
        open={emitterMode !== null || editingEmitter !== null}
        mode={
          editingEmitter
            ? editingEmitter.emitter_type === 'face'
              ? 'face'
              : 'datum_plane'
            : emitterMode ?? 'face'
        }
        scene={scene}
        selectedFaceIds={selectedFaceIds}
        existingIds={emitters.map((emitter) => emitter.emitter_id)}
        initialEmitter={editingEmitter}
        onOpenChange={(open) => {
          if (!open) {
            actions.setEmitterFaceSelectionArmed(false)
            actions.setSelectedFaceIds([])
            actions.setSelectedComponentIds([])
            setEmitterMode(null)
            setEditingEmitterId(null)
          }
        }}
        onApply={(emitter) => {
          actions.upsertEmitter(emitter)
          actions.setSelectedFaceIds([])
          actions.setSelectedComponentIds([])
        }}
      />
      <ReceiverDialog
        open={receiverMode !== null || editingReceiver !== null}
        mode={
          editingReceiver
            ? editingReceiver.placement_mode === 'current_view'
              ? 'current_view'
              : 'datum_plane'
            : receiverMode ?? 'datum_plane'
        }
        scene={scene}
        cameraFrame={cameraFrame}
        existingIds={receivers.map((receiver) => receiver.receiver_id)}
        initialReceiver={editingReceiver}
        onOpenChange={(open) => {
          if (!open) {
            setReceiverMode(null)
            setEditingReceiverId(null)
          }
        }}
        onApply={actions.upsertReceiver}
      />
    </div>
  )
}
