import { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react'
import type {
  EmitterDistribution,
  EmitterPowerMode,
  EmitterSpec,
  RayTraceConfigRequest,
  RayTraceResult,
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
  Square,
  Trash2,
} from 'lucide-react'

import {
  useRayTraceJobQuery,
  useGpuCudaStatusQuery,
  useStartRayTraceMutation,
  useStopRayTraceMutation,
} from '@/api'
import {
  AppDialog,
  HelpTooltip,
  ViewerFacePickControl,
} from '@/components/common'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NumberInput } from '@/components/ui/number-input'
import {
  maxReflectionDepth,
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

import {
  axesFromNormal,
  buildRayTraceRequest,
  convergenceSegmentSeed,
  createCurrentViewReceiver,
  createDatumEmitter,
  createDatumReceiver,
  createFaceEmitter,
  mergeConvergenceRayTraceResults,
  nextSpecId,
  planeAxesFromRotation,
  rayObjectDisplayName,
  rotationFromPlaneAxes,
  type ViewerCameraFrame,
} from './ray-tracing-model'
import { ComputeDeviceSelector } from './compute-device-selector'
import { isGpuCudaStatusReady } from './gpu-cuda-status'

export interface RayObjectEditRequest {
  id: string
  kind: 'emitter' | 'receiver'
}

interface RayTracingPanelProps {
  scene?: ScenePayload
  cameraFrame: ViewerCameraFrame | null
  editRequest?: RayObjectEditRequest | null
  autoConvergenceCancelToken?: number
  onEditRequestHandled?(): void
}

type EmitterCreationMode = 'face' | 'datum_plane'
type ReceiverCreationMode = 'datum_plane' | 'current_view'

const receiverDefaultSizeMm = 30
const currentViewDefaultDistanceMm = 30

const inputClassName =
  'h-8 w-full rounded-lg border border-input bg-background px-2.5 text-base outline-none focus:border-primary focus:ring-2 focus:ring-primary/20'
const fieldLabelClassName = 'space-y-1 text-sm font-medium'

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

function countCadFaces(
  scene: ScenePayload | undefined,
  faceIds: number[],
): number {
  const sourceIds = scene?.mesh.face_source_ids
  if (!sourceIds) return faceIds.length
  return new Set(faceIds.map((faceId) => sourceIds[faceId] ?? faceId)).size
}

function NumberField({
  label,
  ariaLabel,
  value,
  onChange,
  min,
  max,
  step = 'any',
  decimals,
  disabled = false,
  description,
  className = '',
  labelClassName = '',
}: {
  label: string
  ariaLabel?: string
  value: number
  onChange(value: number): void
  min?: number
  max?: number
  step?: number | 'any'
  decimals?: number
  disabled?: boolean
  description?: string
  className?: string
  labelClassName?: string
}) {
  return (
    <label className={`${fieldLabelClassName} ${className}`}>
      <span className={`flex items-center gap-1.5 ${labelClassName}`}>
        {label}
        {description ? (
          <HelpTooltip label={`${label} 도움말`}>{description}</HelpTooltip>
        ) : null}
      </span>
      <NumberInput
        className={inputClassName}
        aria-label={ariaLabel ?? label}
        value={value}
        min={min}
        max={max}
        step={step}
        decimals={decimals}
        disabled={disabled}
        onValueChange={onChange}
      />
    </label>
  )
}

function VectorFields({
  label,
  help,
  labels,
  ariaLabels,
  value,
  onChange,
}: {
  label: string
  help?: string
  labels: [string, string, string]
  /** Accessible names, when the visible labels alone would collide with
   * another field group in the same dialog (e.g. multiple "X" fields). */
  ariaLabels?: [string, string, string]
  value: Vec3
  onChange(value: Vec3): void
}) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
        {label}
        {help ? (
          <HelpTooltip label={`${label} 도움말`}>{help}</HelpTooltip>
        ) : null}
      </legend>
      <div className="grid grid-cols-3 gap-2">
        {labels.map((axisLabel, axis) => (
          <NumberField
            key={axisLabel}
            label={axisLabel}
            ariaLabel={ariaLabels?.[axis]}
            value={value[axis]}
            decimals={1}
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
    useState<EmitterPowerMode>('set_luminance')
  const [power, setPower] = useState(1)
  const [powerDensity, setPowerDensity] = useState(100)
  const [luminanceNit, setLuminanceNit] = useState(500)
  const [rayCount, setRayCount] = useState(10_000)
  const [distribution, setDistribution] =
    useState<EmitterDistribution>('lambertian')
  const [sigma, setSigma] = useState(12)
  const [normalFlip, setNormalFlip] = useState(false)
  const [datumFaceAssigned, setDatumFaceAssigned] = useState(false)
  const [sourceFaceIds, setSourceFaceIds] = useState<number[]>([])
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const datumFacePickArmed = useWorkspaceStore(
    workspaceSelectors.datumFacePickArmed,
  )
  const datumFacePickResult = useWorkspaceStore(
    workspaceSelectors.datumFacePickResult,
  )
  const emitterFaceSelectionArmed = useWorkspaceStore(
    workspaceSelectors.emitterFaceSelectionArmed,
  )

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
    setPowerMode(initialEmitter?.power_mode ?? 'set_luminance')
    setPower(initialEmitter?.power_lumen ?? 1)
    setPowerDensity(
      initialEmitter?.power_density_lm_per_m2 ?? 100,
    )
    setLuminanceNit(initialEmitter?.luminance_nit ?? 500)
    setRayCount(initialEmitter?.ray_count ?? 10_000)
    setDistribution(
      initialEmitter?.direction_distribution ?? 'lambertian',
    )
    setSigma(initialEmitter?.gaussian_sigma_deg ?? 12)
    setNormalFlip(initialEmitter?.normal_flip ?? false)
    setDatumFaceAssigned(
      mode === 'datum_plane' && Boolean(initialEmitter),
    )
    const initialSourceFaceIds =
      mode === 'face'
        ? (initialEmitter?.face_indices ?? [])
        : (initialEmitter?.source_face_indices ?? [])
    setSourceFaceIds(initialSourceFaceIds)
    if (mode === 'face' && initialEmitter) {
      actions.setSelectedFaceIds(initialEmitter.face_indices)
    } else if (mode === 'datum_plane') {
      actions.setSelectedFaceIds(initialSourceFaceIds)
    }
  }, [actions, defaultCenter, initialEmitter, mode, open])

  // Same pick-a-face-in-the-viewer channel Receiver's Datum Plane uses -
  // reused as-is since both just want a starting center/rotation.
  useEffect(() => {
    if (!open || mode !== 'datum_plane' || !datumFacePickResult) return
    const { center: pickedCenter, normal: pickedNormal } =
      datumFacePickResult
    const nextCenter: Vec3 = [pickedCenter.x, pickedCenter.y, pickedCenter.z]
    const normalVector: Vec3 = [
      pickedNormal.x,
      pickedNormal.y,
      pickedNormal.z,
    ]
    const { uAxis, vAxis } = axesFromNormal(normalVector)
    setCenter(nextCenter)
    setRotation(rotationFromPlaneAxes(uAxis, vAxis, normalVector))
    // Re-selecting a CAD face explicitly adopts the Receiver front-view
    // convention: look from the arrow start along the arrow, X+ right/Y+ up.
    setNormalFlip(true)
    setDatumFaceAssigned(true)
    setSourceFaceIds(datumFacePickResult.faceIds)
    actions.setDatumFacePickResult(null)
  }, [actions, mode, open, datumFacePickResult])

  useEffect(() => {
    if (open) return
    actions.setDatumFacePickArmed(false)
  }, [actions, open])

  const emitterFaceIds = selectedFaceIds
  const emitterCadFaceCount = countCadFaces(scene, emitterFaceIds)
  const emitterAreaMm2 =
    mode === 'datum_plane'
      ? Math.max(0, width) * Math.max(0, height)
      : emitterFaceIds.reduce(
          (sum, faceId) =>
            sum + (scene?.mesh.face_areas_mm2[faceId] ?? 0),
          0,
        )
  const luminancePowerDensity = Math.PI * Math.max(0, luminanceNit)
  const luminanceTotalFlux =
    luminancePowerDensity * emitterAreaMm2 * 1e-6
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
            source_face_indices: sourceFaceIds,
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
      luminance_nit: Math.max(0, luminanceNit),
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
          ? `Edit ${rayObjectDisplayName('emitter', initialEmitter.emitter_id)}`
          : mode === 'face'
            ? 'CAD Surface Emitter'
            : 'Datum Plane Emitter'
      }
      help={
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
            {initialEmitter ? 'Save Emitter' : 'Add Emitter'}
          </Button>
        </>
      }
    >
      <div className="max-h-[66vh] space-y-4 overflow-y-auto pr-1">
        {mode === 'face' ? (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="text-sm font-semibold">
              {initialEmitter ? 'Emitter faces' : 'Selected faces'}
              {emitterCadFaceCount > 0
                ? ` · CAD 면 ${emitterCadFaceCount}개`
                : ''}
            </div>
            <div className="mt-2">
              <ViewerFacePickControl
                armed={emitterFaceSelectionArmed}
                assigned={emitterCadFaceCount > 0}
                kind="surface"
                cadFaceCount={emitterCadFaceCount}
                onToggle={() => {
                  if (emitterFaceSelectionArmed) {
                    actions.setEmitterFaceSelectionArmed(false)
                    return
                  }
                  actions.setEmitterFaceSelectionArmed(true)
                }}
              />
            </div>
          </div>
        ) : (
          <>
            <ViewerFacePickControl
              armed={datumFacePickArmed}
              assigned={datumFaceAssigned}
              kind="datum"
              onToggle={() =>
                actions.setDatumFacePickArmed(!datumFacePickArmed)
              }
            />
            <VectorFields
              label="Emitter Center 좌표 (mm)"
              help="발광면의 중심 좌표입니다 (mm, CAD/Datum 원점 기준)."
              labels={['X', 'Y', 'Z']}
              ariaLabels={[
                'Emitter center X',
                'Emitter center Y',
                'Emitter center Z',
              ]}
              value={center}
              onChange={setCenter}
            />
            <VectorFields
              label="Emitter Rotation (deg)"
              help="발광면의 X/Y/Z축 기준 회전(도)입니다. 회전 후의 로컬 Z축이 발광 방향(normal)이 됩니다."
              labels={['X', 'Y', 'Z']}
              ariaLabels={[
                'Emitter rotation X',
                'Emitter rotation Y',
                'Emitter rotation Z',
              ]}
              value={rotation}
              onChange={setRotation}
            />
            <fieldset className="space-y-1.5">
              <legend className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
                Emitter Size (mm)
                <HelpTooltip label="Emitter Size 도움말">
                  발광면의 가로(Width)·세로(Height) 크기입니다 (mm).
                </HelpTooltip>
              </legend>
              <div className="grid grid-cols-2 gap-2">
                <NumberField
                  label="Width (mm)"
                  ariaLabel="Emitter width (mm)"
                  value={width}
                  min={0.001}
                  onChange={setWidth}
                />
                <NumberField
                  label="Height (mm)"
                  ariaLabel="Emitter height (mm)"
                  value={height}
                  min={0.001}
                  onChange={setHeight}
                />
              </div>
            </fieldset>
          </>
        )}

        <div className="grid gap-2 sm:grid-cols-2">
          <label className={fieldLabelClassName}>
            <span className="flex items-center gap-1.5">
              Power mode
              <HelpTooltip label="Power mode 도움말">
                SET luminance: 완제품 화면 휘도(nit)를 입력하면 선택한 발광면
                면적으로 Lambertian 등가 총광속을 자동 계산합니다.{' '}
                Total power: 발광면 전체의 총 광속(lm)을 지정합니다. Power
                per area: 단위 면적당 광속(lm/m²)을 지정해, 발광면 크기에
                따라 총 광량이 자동으로 계산됩니다.
              </HelpTooltip>
            </span>
            <select
              className={inputClassName}
              aria-label="Emitter power mode"
              value={powerMode}
              onChange={(event) =>
                setPowerMode(event.currentTarget.value as EmitterPowerMode)
              }
            >
              <option value="set_luminance">SET luminance (nit)</option>
              <option value="total">Total power</option>
              <option value="power_per_area">Power per area</option>
            </select>
          </label>
          {powerMode === 'set_luminance' ? (
            <NumberField
              label="SET luminance (nit)"
              ariaLabel="SET luminance (nit)"
              value={luminanceNit}
              min={0}
              onChange={setLuminanceNit}
              description="완제품 화면 기준 휘도입니다. 선택한 Emitter 면적에 비례해 총광속을 자동 환산합니다."
            />
          ) : powerMode === 'total' ? (
            <NumberField
              label="Total power (lm)"
              value={power}
              min={0}
              onChange={setPower}
              description="발광면 전체에서 방출하는 총 광속입니다."
            />
          ) : (
            <NumberField
              label="Power density (lm/m²)"
              value={powerDensity}
              min={0}
              onChange={setPowerDensity}
              description="단위 면적당 방출 광속입니다. 발광면 크기(Width×Height)를 곱한 값이 총 광속이 됩니다."
            />
          )}
          {powerMode === 'set_luminance' ? (
            <div className="rounded-lg border border-blue-200 bg-blue-50/65 p-2.5 text-xs leading-5 text-blue-950 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-100 sm:col-span-2">
              <div className="font-semibold">SET luminance conversion</div>
              <div>
                Emitter area {emitterAreaMm2.toLocaleString(undefined, { maximumFractionDigits: 2 })} mm²
                {' · '}πL {luminancePowerDensity.toLocaleString(undefined, { maximumFractionDigits: 3 })} lm/m²
                {' · '}Total flux {luminanceTotalFlux.toLocaleString(undefined, { maximumFractionDigits: 6 })} lm
              </div>
              <div className="text-blue-800/80 dark:text-blue-200/75">
                Lambertian 등가 환산값이며, 선택 영역이 작아지면 총광속도 면적에 비례해 감소합니다.
              </div>
            </div>
          ) : null}
          <NumberField
            label="Emitter rays"
            value={rayCount}
            min={1}
            step={1000}
            onChange={setRayCount}
            description="시뮬레이션에 사용할 ray 샘플 개수입니다. 많을수록 결과가 정밀해지지만 계산 시간이 늘어납니다."
          />
          <label className={fieldLabelClassName}>
            <span className="flex items-center gap-1.5">
              Direction distribution
              <HelpTooltip label="Direction distribution 도움말">
                발광 방향의 각도 분포입니다. Lambertian: cosine 가중 확산광
                (일반 표면 발광). Isotropic: 반구 전체에 균일 분포.
                Gaussian: normal 방향을 중심으로 좁게 퍼지는 지향성 광원.
              </HelpTooltip>
            </span>
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
              description="Gaussian 분포의 표준편차(도)입니다. 작을수록 normal 방향으로 좁게 집중됩니다."
            />
          ) : null}
        </div>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={normalFlip}
            onChange={(event) => setNormalFlip(event.currentTarget.checked)}
          />
          <span className="flex items-center gap-1.5">
            Flip normal direction
            <HelpTooltip label="Flip normal direction 도움말">
              발광면의 발광 방향(normal)을 반대로 뒤집습니다.
            </HelpTooltip>
          </span>
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
  const [pixelSize, setPixelSize] = useState(
    receiverDefaultSizeMm / Math.sqrt(80 * 24),
  )
  const [acceptance, setAcceptance] = useState(90)
  const [viewDistance, setViewDistance] = useState(
    currentViewDefaultDistanceMm,
  )
  const [positionOffset, setPositionOffset] = useState<Vec3>([0, 0, 0])
  const [tilt, setTilt] = useState<Vec3>([0, 0, 0])
  const [capturedFrame, setCapturedFrame] =
    useState<ViewerCameraFrame | null>(null)
  const [normalFlip, setNormalFlip] = useState(false)
  const [datumFaceAssigned, setDatumFaceAssigned] = useState(false)
  const [receiverSourceFaceIds, setReceiverSourceFaceIds] =
    useState<number[]>([])
  const cameraFrameRef = useRef(cameraFrame)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const datumFacePickArmed = useWorkspaceStore(
    workspaceSelectors.datumFacePickArmed,
  )
  const datumFacePickResult = useWorkspaceStore(
    workspaceSelectors.datumFacePickResult,
  )

  useEffect(() => {
    cameraFrameRef.current = cameraFrame
  }, [cameraFrame])

  useEffect(() => {
    if (!open) return
    const initialDistance =
      initialReceiver?.view_distance_mm ?? currentViewDefaultDistanceMm
    setDisplayName(initialReceiver?.display_name ?? '')
    setCenter(
      initialReceiver?.base_center ??
        initialReceiver?.center ??
        defaultCenter,
    )
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
    const initialResolution = initialReceiver?.resolution ?? [80, 24]
    const initialWidth = initialReceiver?.width_mm ?? receiverDefaultSizeMm
    const initialHeight = initialReceiver?.height_mm ?? receiverDefaultSizeMm
    setPixelSize(
      Math.sqrt(
        (initialWidth / initialResolution[0]) *
          (initialHeight / initialResolution[1]),
      ),
    )
    setAcceptance(initialReceiver?.acceptance_angle_deg ?? 90)
    setViewDistance(initialDistance)
    setPositionOffset(initialReceiver?.position_offset_mm ?? [0, 0, 0])
    setTilt(initialReceiver?.tilt_xyz_deg ?? [0, 0, 0])
    setNormalFlip(initialReceiver?.normal_flip ?? true)
    setDatumFaceAssigned(Boolean(initialReceiver))
    const initialSourceFaceIds = initialReceiver?.source_face_indices ?? []
    setReceiverSourceFaceIds(initialSourceFaceIds)
    if (mode === 'datum_plane') {
      actions.setSelectedFaceIds(initialSourceFaceIds)
    }
    if (mode === 'current_view' && initialReceiver) {
      const normal = initialReceiver.base_normal ?? initialReceiver.normal
      const uAxis = initialReceiver.base_u_axis ?? initialReceiver.u_axis
      const vAxis = initialReceiver.base_v_axis ?? initialReceiver.v_axis
      const baseCenter = initialReceiver.base_center ?? initialReceiver.center
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
    actions,
    defaultCenter,
    initialReceiver,
    mode,
    open,
  ])

  // A face picked in the viewer lands here as {center, normal} - reuse it
  // as the base placement and rotation, the same way typing a face's own
  // coordinates by hand would. Consumed once, then cleared.
  useEffect(() => {
    if (!open || mode !== 'datum_plane' || !datumFacePickResult) return
    const { center: pickedCenter, normal: pickedNormal } =
      datumFacePickResult
    const nextCenter: Vec3 = [pickedCenter.x, pickedCenter.y, pickedCenter.z]
    const normalVector: Vec3 = [pickedNormal.x, pickedNormal.y, pickedNormal.z]
    const aligned = {
      normal: normalVector,
      ...axesFromNormal(normalVector),
    }
    setCenter(nextCenter)
    setRotation(
      rotationFromPlaneAxes(
        aligned.uAxis,
        aligned.vAxis,
        aligned.normal,
      ),
    )
    // Receiver FRONT is always read from the arrow tail toward its tip.
    // Keeping the flip enabled makes that trace direction match the Viewer
    // direction used above while +X remains right and +Y remains up.
    setNormalFlip(true)
    setDatumFaceAssigned(true)
    setReceiverSourceFaceIds(datumFacePickResult.faceIds)
    actions.setDatumFacePickResult(null)
  }, [actions, mode, open, datumFacePickResult])

  useEffect(() => {
    if (open) return
    actions.setDatumFacePickArmed(false)
  }, [actions, open])

  const canApply = mode === 'datum_plane' || capturedFrame !== null
  const updatePixelSizeFromResolution = (
    nextResolutionX: number,
    nextResolutionY: number,
  ) => {
    const columns = Math.max(1, Math.trunc(nextResolutionX))
    const rows = Math.max(1, Math.trunc(nextResolutionY))
    setPixelSize(
      Math.sqrt(
        (Math.max(0.001, width) / columns) *
          (Math.max(0.001, height) / rows),
      ),
    )
  }
  const updateResolutionFromPixelSize = (nextPixelSize: number) => {
    const pitch = Math.max(0.001, nextPixelSize)
    setPixelSize(pitch)
    setResolutionX(Math.max(1, Math.round(width / pitch)))
    setResolutionY(Math.max(1, Math.round(height / pitch)))
  }
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
            positionOffset,
          )
    return {
      ...receiver,
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
        : createDatumReceiver(
            receiverId,
            center,
            rotation,
            positionOffset,
          )
    onApply({
      ...initialReceiver,
      ...receiver,
      source_face_indices:
        mode === 'datum_plane' ? receiverSourceFaceIds : [],
      display_name:
        displayName.trim() ||
        rayObjectDisplayName('receiver', receiverId),
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
          ? `Edit ${rayObjectDisplayName(
              'receiver',
              initialReceiver.receiver_id,
              initialReceiver.display_name,
            )}`
          : mode === 'current_view'
            ? 'Current View Receiver'
            : 'Datum Plane Receiver'
      }
      help={
        mode === 'current_view'
          ? '현재 3D Viewer의 카메라 위치와 방향을 기준으로 Receiver를 생성합니다.'
          : 'CAD Face 또는 중심 좌표와 Receiver Tilt로 사각 수광면을 배치합니다.'
      }
      size="lg"
      onSubmit={handleApply}
      footer={
        <>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!canApply} onClick={handleApply}>
            {initialReceiver ? 'Save Receiver' : 'Add Receiver'}
          </Button>
        </>
      }
    >
      <div className="max-h-[66vh] space-y-4 overflow-y-auto pr-1">
        <label className={fieldLabelClassName}>
          <span className="flex items-center gap-1.5">
            Receiver name
            <HelpTooltip label="Receiver name 도움말">
              결과·리스트에 표시할 이 Receiver의 이름입니다.
            </HelpTooltip>
          </span>
          <input
            className={inputClassName}
            aria-label="Receiver name"
            value={displayName}
            placeholder="Receiver 1"
            onChange={(event) => setDisplayName(event.currentTarget.value)}
          />
        </label>
        {mode === 'datum_plane' ? <>
        <ViewerFacePickControl
          armed={datumFacePickArmed}
          assigned={datumFaceAssigned}
          kind="datum"
          onToggle={() =>
            actions.setDatumFacePickArmed(!datumFacePickArmed)
          }
        />
        <div className="rounded-lg border border-blue-200 bg-blue-50/65 p-2.5 dark:border-blue-900/70 dark:bg-blue-950/30">
          <div className="flex items-center justify-between gap-2">
            <div className="min-w-0 text-xs leading-5 text-muted-foreground">
              Heatmap 좌표 = 3D Viewer의 빨간 X+ · 녹색 Y+ 축
            </div>
            <HelpTooltip label="Receiver Heatmap 좌표 도움말">
              3D Viewer에 표시되는 Receiver의 빨간 X+와 녹색 Y+가 Heatmap의
              오른쪽과 위쪽에 각각 대응합니다. Receiver Tilt를 변경하면 면과
              로컬 X/Y 축이 함께 회전합니다.
            </HelpTooltip>
          </div>
        </div>
        <VectorFields
          label="Receiver Center 좌표 (mm)"
          help="수광면의 중심 좌표입니다 (mm)."
          labels={['X', 'Y', 'Z']}
          ariaLabels={[
            'Receiver center X',
            'Receiver center Y',
            'Receiver center Z',
          ]}
          value={center}
          onChange={setCenter}
        />
        <VectorFields
          label="Receiver Offset (mm)"
          help="Center 좌표에 추가하는 이동값입니다 (mm)."
          labels={['X', 'Y', 'Z']}
          ariaLabels={[
            'Receiver offset X',
            'Receiver offset Y',
            'Receiver offset Z',
          ]}
          value={positionOffset}
          onChange={setPositionOffset}
        />
        <VectorFields
          label="Receiver Tilt (deg)"
          help="Receiver 면과 로컬 X/Y 좌표축을 X/Y/Z 기준으로 함께 회전합니다. 면 안에서 X/Y 방향만 돌리려면 면의 normal 방향에 해당하는 축의 Tilt 값을 조정하세요."
          labels={['X', 'Y', 'Z']}
          ariaLabels={[
            'Receiver tilt X',
            'Receiver tilt Y',
            'Receiver tilt Z',
          ]}
          value={rotation}
          onChange={setRotation}
        />
        </> : (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="flex items-center gap-2 text-sm font-semibold">
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
                  description="현재 Viewer의 시점 중심에서 카메라 방향으로 떨어진 Receiver 위치를 지정합니다."
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
                Use Current Camera
              </Button>
            </div>
            {capturedFrame && previewReceiver?.base_center ? (
              <div className="mt-4 space-y-3 border-t border-primary/15 pt-3">
                <VectorFields
                  label="Center (mm)"
                  help="캡처한 카메라 기준으로 계산된 Receiver 중심 좌표입니다."
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
                  label="Receiver Tilt (deg)"
                  help="캡처한 카메라 기준 Receiver 면과 X/Y 좌표축을 함께 회전합니다."
                  labels={['X', 'Y', 'Z']}
                  ariaLabels={[
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
        <fieldset className="space-y-1.5">
          <legend className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
            Receiver Size (mm)
            <HelpTooltip label="Receiver Size 도움말">
              수광면의 가로(Width)·세로(Height) 크기입니다 (mm).
            </HelpTooltip>
          </legend>
          <div className="grid grid-cols-2 gap-2">
            <NumberField
              label="Width (mm)"
              ariaLabel="Receiver width (mm)"
              value={width}
              min={0.001}
              onChange={setWidth}
            />
            <NumberField
              label="Height (mm)"
              ariaLabel="Receiver height (mm)"
              value={height}
              min={0.001}
              onChange={setHeight}
            />
          </div>
        </fieldset>
        <div className="grid grid-cols-[1.15fr_1fr_1fr] items-end gap-2">
          <NumberField
            label="Pixel Size (mm)"
            value={pixelSize}
            min={0.1}
            step={0.1}
            decimals={1}
            labelClassName="whitespace-nowrap"
            onChange={updateResolutionFromPixelSize}
            description="Square pixel target size. Entering a value automatically calculates Resolution X/Y. When resolution is edited, this shows the equivalent pixel size from the actual X/Y cell area."
          />
          <NumberField
            label="Resolution X"
            value={resolutionX}
            min={1}
            step={1}
            onChange={(value) => {
              setResolutionX(value)
              updatePixelSizeFromResolution(value, resolutionY)
            }}
            description="수광면을 가로로 몇 개의 Grid Cell로 나눠 Hit 분포(Heatmap)를 기록할지 지정합니다."
          />
          <NumberField
            label="Resolution Y"
            value={resolutionY}
            min={1}
            step={1}
            onChange={(value) => {
              setResolutionY(value)
              updatePixelSizeFromResolution(resolutionX, value)
            }}
            description="수광면을 세로로 몇 개의 Grid Cell로 나눠 Hit 분포(Heatmap)를 기록할지 지정합니다."
          />
        </div>
        <div className="grid grid-cols-1 gap-2">
          <NumberField
            label="Acceptance Angle (deg)"
            ariaLabel="Acceptance angle (deg)"
            value={acceptance}
            min={0.1}
            max={180}
            labelClassName="whitespace-nowrap"
            onChange={setAcceptance}
            description="이 각도보다 큰 입사각으로 도달한 ray는 수광 대상에서 제외합니다 (0=정면만, 180=모든 각도)."
          />
        </div>
        <label className="flex items-center gap-2 text-xs">
          <input
            type="checkbox"
            checked={normalFlip}
            onChange={(event) => setNormalFlip(event.currentTarget.checked)}
          />
          <span className="flex items-center gap-1.5">
            Flip receiving normal
            <HelpTooltip label="Flip receiving normal 도움말">
              Emitter의 Flip normal direction과 동일하게 Receiver가 빛을
              받는 방향과 3D Viewer 화살표 방향만 반대로 뒤집습니다.
              Receiver Width/Height와 X/Y 좌표축은 변경하지 않습니다.
            </HelpTooltip>
          </span>
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

function ConvergenceSparkline({ label, values }: { label: string; values: number[] }) {
  const maximum = Math.max(...values.filter(Number.isFinite), 0)
  const points = values.map((value, index) => {
    const x = values.length <= 1 ? 0 : index / (values.length - 1) * 100
    const y = maximum > 0 && Number.isFinite(value) ? 28 - value / maximum * 24 : 28
    return `${x},${y}`
  }).join(' ')
  return (
    <div className="rounded border border-border bg-background/40 p-1.5">
      <div className="text-xs text-muted-foreground">{label}</div>
      <svg viewBox="0 0 100 30" preserveAspectRatio="none" className="mt-1 h-10 w-full">
        <path d="M0 28 H100" className="stroke-border" strokeWidth="0.7" />
        <polyline points={points} fill="none" className="stroke-primary" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  )
}

export function RayTracingPanel({
  scene,
  cameraFrame,
  editRequest = null,
  autoConvergenceCancelToken = 0,
  onEditRequestHandled,
}: RayTracingPanelProps) {
  const accelerationStructureId = useId()
  const [emitterMode, setEmitterMode] =
    useState<EmitterCreationMode | null>(null)
  const [receiverMode, setReceiverMode] =
    useState<ReceiverCreationMode | null>(null)
  const [editingEmitterId, setEditingEmitterId] =
    useState<string | null>(null)
  const [editingReceiverId, setEditingReceiverId] =
    useState<string | null>(null)
  const autoConvergenceActiveRef = useRef(false)
  const convergenceMultiplierRef = useRef(1)
  const convergenceSegmentIndexRef = useRef(0)
  const convergenceAggregateRef = useRef<RayTraceResult | null>(null)
  const handledConvergenceJobRef = useRef<string | null>(null)
  const autoRetryJobIdRef = useRef<string | null>(null)
  const autoRetryAbortControllerRef = useRef<AbortController | null>(null)
  const autoConvergenceCancelTokenRef = useRef(autoConvergenceCancelToken)
  const handledAutoConvergenceCancelTokenRef = useRef(
    autoConvergenceCancelToken,
  )
  autoConvergenceCancelTokenRef.current = autoConvergenceCancelToken
  const [convergenceHistory, setConvergenceHistory] = useState<
    { rays: number; totalError: number; peakError: number; peakNit: number; flux: number }[]
  >([])
  const convergenceHistoryRef = useRef<
    { rays: number; totalError: number; peakError: number; peakNit: number; flux: number }[]
  >([])
  const [autoConvergenceStatus, setAutoConvergenceStatus] = useState('')
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
  const stopMutation = useStopRayTraceMutation()
  const gpuCudaStatusQuery = useGpuCudaStatusQuery(
    config.compute_backend === 'gpu_cuda',
  )
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
  const gpuCudaProbeReady =
    gpuCudaStatusQuery.isSuccess &&
    !gpuCudaStatusQuery.isFetching &&
    !gpuCudaStatusQuery.isRefreshing &&
    !gpuCudaStatusQuery.isError &&
    !gpuCudaStatusQuery.refreshFailed &&
    isGpuCudaStatusReady(gpuCudaStatusQuery.data)
  const gpuCudaReady =
    config.compute_backend !== 'gpu_cuda' ||
    gpuCudaProbeReady
  const canRun =
    scene !== undefined &&
    enabledEmitterCount > 0 &&
    enabledReceiverCount > 0 &&
    gpuCudaReady &&
    !isRunning

  useEffect(() => {
    if (
      autoConvergenceCancelToken <=
      handledAutoConvergenceCancelTokenRef.current
    ) return
    handledAutoConvergenceCancelTokenRef.current = autoConvergenceCancelToken
    const hadPendingAutoConvergence =
      autoConvergenceActiveRef.current ||
      autoRetryAbortControllerRef.current !== null ||
      autoRetryJobIdRef.current !== null
    autoConvergenceActiveRef.current = false
    autoRetryAbortControllerRef.current?.abort()
    autoRetryAbortControllerRef.current = null
    const autoRetryJobId = autoRetryJobIdRef.current
    autoRetryJobIdRef.current = null
    if (autoRetryJobId) {
      stopMutation.mutate({ jobId: autoRetryJobId })
      if (activeJobId === autoRetryJobId) {
        // Keep the completed result that was already being reviewed, while
        // detaching the canceled automatic retry so its partial result cannot
        // reopen the report window after the user closes it.
        actions.setActiveRayTraceJobId(null)
      }
    }
    if (hadPendingAutoConvergence) {
      setAutoConvergenceStatus(
        '결과 창을 닫아 이후 Auto convergence 추가 실행을 취소했습니다.',
      )
    }
  }, [actions, activeJobId, autoConvergenceCancelToken, stopMutation])

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

  const launchRun = useCallback(async (
    rayMultiplier = 1,
    autoRetry = false,
    segmentIndex = 0,
  ) => {
    if (
      !scene ||
      !emitters.some((emitter) => emitter.enabled) ||
      !receivers.some((receiver) => receiver.enabled)
    ) return false
    const request = buildRayTraceRequest({
      scene,
      projectName: activeCad?.displayName || 'TV-Leakage-Direct',
      emitters: emitters.map((emitter) => ({
        ...emitter,
        ray_count: Math.max(1, Math.trunc(emitter.ray_count * rayMultiplier)),
      })),
      receivers,
      materialAssignments,
      transformRules,
      excludedComponentIds,
      deletedComponentIds,
      roiScopes,
      config,
    })
    if (config.auto_convergence) {
      request.config.seed = convergenceSegmentSeed(config.seed, segmentIndex)
      request.emitters = request.emitters.map((emitter) => ({
        ...emitter,
        seed: emitter.seed === null
          ? null
          : convergenceSegmentSeed(emitter.seed, segmentIndex),
      }))
    }
    const cancelTokenAtStart = autoConvergenceCancelTokenRef.current
    const abortController = autoRetry ? new AbortController() : null
    if (autoRetry) {
      autoRetryAbortControllerRef.current?.abort()
      autoRetryAbortControllerRef.current = abortController
    }
    try {
      const startedJob = await startMutation.mutateAsync({
        request,
        signal: abortController?.signal,
      })
      if (autoRetryAbortControllerRef.current === abortController) {
        autoRetryAbortControllerRef.current = null
      }
      if (
        autoRetry &&
        (cancelTokenAtStart !== autoConvergenceCancelTokenRef.current ||
          !autoConvergenceActiveRef.current)
      ) {
        stopMutation.mutate({ jobId: startedJob.job_id })
        return false
      }
      autoRetryJobIdRef.current = autoRetry ? startedJob.job_id : null
      actions.setActiveRayTraceJobId(startedJob.job_id)
      return true
    } catch {
      if (autoRetryAbortControllerRef.current === abortController) {
        autoRetryAbortControllerRef.current = null
      }
      if (autoRetry && abortController?.signal.aborted) return false
      autoConvergenceActiveRef.current = false
      setAutoConvergenceStatus('자동 수렴의 다음 Ray 실행을 시작하지 못했습니다.')
      return false
    }
  }, [activeCad?.displayName, config, deletedComponentIds, emitters, excludedComponentIds, materialAssignments, receivers, roiScopes, scene, startMutation, stopMutation, transformRules, actions])

  const handleRun = async () => {
    autoConvergenceActiveRef.current = config.auto_convergence ?? false
    autoRetryJobIdRef.current = null
    autoRetryAbortControllerRef.current?.abort()
    autoRetryAbortControllerRef.current = null
    convergenceMultiplierRef.current = 1
    convergenceSegmentIndexRef.current = 0
    convergenceAggregateRef.current = null
    handledConvergenceJobRef.current = null
    setAutoConvergenceStatus(
      config.auto_convergence ? '자동 수렴 1차 해석을 시작합니다.' : '',
    )
    setConvergenceHistory([])
    convergenceHistoryRef.current = []
    await launchRun(1, false, 0)
  }

  useEffect(() => {
    if (!job || job.status !== 'completed' || !job.result) return
    if (handledConvergenceJobRef.current === job.job_id) return
    handledConvergenceJobRef.current = job.job_id
    if (autoRetryJobIdRef.current === job.job_id) {
      autoRetryJobIdRef.current = null
    }
    let accumulatedResult = job.result
    if (config.auto_convergence) {
      try {
        accumulatedResult = mergeConvergenceRayTraceResults(
          convergenceAggregateRef.current,
          job.result,
        )
      } catch {
        autoConvergenceActiveRef.current = false
        setAutoConvergenceStatus(
          'Receiver 또는 해석 설정이 실행 중 변경되어 누적을 중단했습니다.',
        )
        return
      }
      convergenceAggregateRef.current = accumulatedResult
    }
    const enabledIds = receivers.filter((receiver) => receiver.enabled).map((receiver) => receiver.receiver_id)
    const receiverMetrics = enabledIds.map((id) => {
      const value = accumulatedResult.metrics[id]
      return value && typeof value === 'object' ? value as Record<string, unknown> : {}
    })
    const metricError = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : Infinity
    const totalError = Math.max(...receiverMetrics.map((value) => metricError(value.error_estimate_percent)))
    const peakError = Math.max(...receiverMetrics.map((value) => metricError(value.peak_area_error_estimate_percent)))
    const peakNit = Math.max(...receiverMetrics.map((value) => Number(value.peak_nit_est) || 0), 0)
    const flux = receiverMetrics.reduce((sum, value) => sum + (Number(value.total_flux_lumen) || 0), 0)
    const enoughSamples = receiverMetrics.every((value) => (Number(value.hit_count) || 0) >= 30)
    const historyEntry = {
      rays: accumulatedResult.total_rays,
      totalError,
      peakError,
      peakNit,
      flux,
    }
    const nextHistory = [...convergenceHistoryRef.current, historyEntry]
    convergenceHistoryRef.current = nextHistory
    setConvergenceHistory(nextHistory)
    accumulatedResult.metrics._convergence_history = nextHistory
    actions.setActiveCadCaseResult(accumulatedResult)
    const convergenceTarget = config.convergence_target_percent ?? 5
    const converged = enoughSamples &&
      totalError <= convergenceTarget &&
      peakError <= convergenceTarget
    if (!autoConvergenceActiveRef.current || converged) {
      autoConvergenceActiveRef.current = false
      if (config.auto_convergence) {
        setAutoConvergenceStatus(
          converged
            ? `목표 오차 ${convergenceTarget}% 이하로 수렴했습니다.`
            : '자동 수렴이 비활성화되어 현재 결과에서 종료했습니다.',
        )
      }
      return
    }
    const currentMultiplier = convergenceMultiplierRef.current
    const nextMultiplier = currentMultiplier * 2
    if (nextMultiplier > (config.max_convergence_multiplier ?? 8)) {
      autoConvergenceActiveRef.current = false
      setAutoConvergenceStatus(
        `최대 Ray 배수 ${(config.max_convergence_multiplier ?? 8)}배에 도달하여 종료했습니다.`,
      )
      return
    }
    const incrementalMultiplier = nextMultiplier - currentMultiplier
    convergenceMultiplierRef.current = nextMultiplier
    convergenceSegmentIndexRef.current += 1
    const incrementalRays = enabledEmitterRayCount * incrementalMultiplier
    const nextTotalRays = enabledEmitterRayCount * nextMultiplier
    setAutoConvergenceStatus(
      `오차가 목표보다 높아 ${incrementalRays.toLocaleString()} Ray를 추가합니다. 누적 ${nextTotalRays.toLocaleString()} Ray`,
    )
    void launchRun(
      incrementalMultiplier,
      true,
      convergenceSegmentIndexRef.current,
    )
  }, [actions, config.auto_convergence, config.convergence_target_percent, config.max_convergence_multiplier, enabledEmitterRayCount, job, launchRun, receivers])

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
      <ComputeDeviceSelector
        value={config.compute_backend}
        disabled={isRunning}
        status={gpuCudaStatusQuery.data}
        pending={
          gpuCudaStatusQuery.isPending ||
          gpuCudaStatusQuery.isFetching ||
          gpuCudaStatusQuery.isRefreshing
        }
        failed={
          gpuCudaStatusQuery.isError || gpuCudaStatusQuery.refreshFailed
        }
        onChange={(computeBackend) =>
          updateConfig({
            compute_backend: computeBackend,
            ...(computeBackend === 'gpu_cuda' &&
            config.intersection_backend === 'brute_force'
              ? { intersection_backend: 'bvh' as const }
              : {}),
          })
        }
        onRetry={() => void gpuCudaStatusQuery.refresh()}
      />

      <section className="space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Lightbulb className="size-3.5 text-warning" />
            Emitter
            <HelpTooltip label="Emitter 도움말">
              빛이 나오는 발광면입니다. CAD surface는 기존 모델의 face를
              그대로 발광면으로 쓰고, Datum plane은 좌표를 직접 입력해
              CAD와 무관한 평면을 새로 배치합니다. 여러 개를 등록하고
              체크박스로 개별적으로 켜고 끌 수 있습니다.
            </HelpTooltip>
          </div>
          <Badge variant="outline">{emitters.length}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <Button
            variant="outline"
            size="sm"
            aria-label="Add CAD Surface Emitter"
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
            CAD Surface
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Add Datum Plane Emitter"
            disabled={!scene || isRunning}
            onClick={() => {
              setEditingEmitterId(null)
              actions.setEmitterFaceSelectionArmed(false)
              setEmitterMode('datum_plane')
            }}
          >
            <Plus />
            Datum Plane
          </Button>
        </div>
        {emitters.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
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
                  aria-label={`Enable ${rayObjectDisplayName('emitter', emitter.emitter_id)}`}
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
                  <div className="truncate text-sm font-semibold">
                    {rayObjectDisplayName('emitter', emitter.emitter_id)}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {emitter.emitter_type === 'face'
                      ? 'CAD Surface'
                      : `${emitter.width_mm} × ${emitter.height_mm} mm`}
                    {' · '}
                    {emitter.ray_count.toLocaleString()} Rays
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Edit ${rayObjectDisplayName('emitter', emitter.emitter_id)}`}
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
                  aria-label={`Delete ${rayObjectDisplayName('emitter', emitter.emitter_id)}`}
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
          <div className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase">
            <Aperture className="size-3.5 text-primary" />
            Receiver
            <HelpTooltip label="Receiver 도움말">
              빛을 받아 hit을 집계하는 수광면입니다. Datum plane은 좌표를
              직접 입력해 배치하고, Current view는 지금 3D Viewer 카메라가
              보고 있는 화면을 그대로 Receiver로 등록합니다. Acceptance
              각도 안으로 들어오는 ray만 hit으로 집계됩니다.
            </HelpTooltip>
          </div>
          <Badge variant="outline">{receivers.length}</Badge>
        </div>
        <div className="grid grid-cols-2 gap-1.5">
          <Button
            variant="outline"
            size="sm"
            aria-label="Add Datum Plane Receiver"
            disabled={!scene || isRunning}
            onClick={() => {
              setEditingReceiverId(null)
              setReceiverMode('datum_plane')
            }}
          >
            <Plus />
            Datum Plane
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Add Current View Receiver"
            disabled={!scene || !cameraFrame || isRunning}
            onClick={() => {
              setEditingReceiverId(null)
              setReceiverMode('current_view')
            }}
          >
            <Camera />
            Current View
          </Button>
        </div>
        {receivers.length === 0 ? (
          <p className="rounded-lg border border-dashed border-border p-3 text-center text-xs text-muted-foreground">
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
                  aria-label={`Enable ${rayObjectDisplayName(
                    'receiver',
                    receiver.receiver_id,
                    receiver.display_name,
                  )}`}
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
                  <div className="truncate text-sm font-semibold">
                    {rayObjectDisplayName(
                      'receiver',
                      receiver.receiver_id,
                      receiver.display_name,
                    )}
                  </div>
                  <div className="text-xs text-muted-foreground">
                    {receiver.placement_mode === 'current_view'
                      ? 'Current View'
                      : 'Datum Plane'}
                    {' · '}
                    {receiver.width_mm} × {receiver.height_mm} mm
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="icon-xs"
                  aria-label={`Edit ${rayObjectDisplayName(
                    'receiver',
                    receiver.receiver_id,
                    receiver.display_name,
                  )}`}
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
                  aria-label={`Delete ${rayObjectDisplayName(
                    'receiver',
                    receiver.receiver_id,
                    receiver.display_name,
                  )}`}
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

      <section className="border-t border-border pt-3">
        <details className="group rounded-lg border border-border bg-background/35">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg px-3 py-2.5 text-sm font-semibold tracking-wide text-muted-foreground uppercase transition-colors hover:bg-primary/5 [&::-webkit-details-marker]:hidden">
            <Activity className="size-3.5" />
            <span className="flex-1">Run Options</span>
            <span className="hidden text-xs font-medium normal-case tracking-normal text-muted-foreground group-open:inline">
              접기
            </span>
            <HelpTooltip label="Run Options 도움말">
              반사 횟수, 종료 조건, 저장할 Ray Path 수 등 전문 계산 조건을
              설정합니다. 필요한 경우에만 펼쳐서 변경하세요.
            </HelpTooltip>
          </summary>
          <div className="space-y-3 border-t border-border p-3">
            <div className="grid grid-cols-1 gap-2.5">
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-2.5">
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
              description={
                (hasMixedEmitterRayCounts
                  ? 'Emitter별 Ray 수가 서로 다릅니다. 값을 변경하면 모든 Emitter에 동일하게 적용됩니다. '
                  : 'Emitter 하나당 발사할 ray 개수 - 모든 등록 Emitter에 동일하게 적용됩니다. ') +
                `활성 Emitter 총합 ${enabledEmitterRayCount.toLocaleString()} rays.`
              }
            />
          </div>
          <div className="rounded-lg border border-primary/20 bg-primary/5 p-2.5">
            <label className="flex items-center gap-2 text-sm font-semibold">
              <input
                type="checkbox"
                checked={config.auto_convergence}
                disabled={isRunning}
                onChange={(event) => updateConfig({ auto_convergence: event.currentTarget.checked })}
              />
              Auto convergence
              <HelpTooltip label="Auto convergence help">
                Total Flux Error와 Peak-area Error가 모두 목표 오차 이하가 될 때까지
                독립 Ray 구간을 추가해 누적 표본을 2배씩 늘립니다. 이전 표본은
                버리지 않고 광량과 제곱합을 표본 수로 가중 결합합니다.
                1→2→4→8배 설정은 실제로 8배 Ray만 처리하며, Flux 수렴이 셀별
                Heatmap 노이즈 감소까지 보장하지는 않습니다.
              </HelpTooltip>
            </label>
            {config.auto_convergence ? (
              <div className="mt-2 grid grid-cols-2 gap-2">
                <NumberField
                  label="Target error (%)"
                  value={config.convergence_target_percent ?? 5}
                  min={0.1}
                  max={100}
                  step={0.5}
                  disabled={isRunning}
                  onChange={(value) => updateConfig({ convergence_target_percent: value })}
                  description="자동 수렴의 목표 오차입니다. Receiver의 Total Flux Error와 Peak-area Error가 모두 이 값 이하가 되면 Converged로 판단하고 자동 해석을 종료합니다. 값이 낮을수록 더 많은 Ray와 계산 시간이 필요합니다."
                />
                <NumberField
                  label="Max ray multiplier"
                  value={config.max_convergence_multiplier ?? 8}
                  min={1}
                  max={64}
                  step={1}
                  disabled={isRunning}
                  onChange={(value) => updateConfig({ max_convergence_multiplier: Math.trunc(value) })}
                  description="최초 설정한 Emitter Ray 수를 자동 수렴 과정에서 최대 몇 배까지 늘릴지 정하는 상한입니다. 예를 들어 10,000 Ray에 8배를 설정하면 10,000 → 20,000 → 40,000 → 80,000 Ray를 각각 새로 실행하여 누적 150,000 Ray를 처리합니다."
                />
              </div>
            ) : null}
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
            description="반사를 최대 몇 번까지 추적할지 (0 = 직접광만, 반사 없음). 클수록 정확하지만 계산이 느려집니다 - quick 체크는 1, 일반 비교는 3, 밀폐된 고반사 경로는 10, 수렴성 확인 목적일 때만 20을 권장합니다."
          />
          <label className="flex items-center gap-2 rounded-lg border border-primary/20 bg-primary/5 p-2.5 text-sm font-semibold">
            <input
              type="checkbox"
              checked={config.angle_dependent_reflectance !== false}
              disabled={isRunning}
              onChange={(event) =>
                updateConfig({
                  angle_dependent_reflectance: event.currentTarget.checked,
                })
              }
            />
            <span className="flex-1">Angle-dependent reflectance</span>
            <span className="text-xs font-semibold text-muted-foreground">
              {config.angle_dependent_reflectance !== false ? 'ON' : 'OFF'}
            </span>
            <HelpTooltip label="Angle-dependent reflectance 도움말">
              ON이면 빛이 표면에 비스듬히 입사할수록 유효 반사율이 증가합니다.
              OFF이면 입사각과 관계없이 Material에 지정한 기본 Reflectance를
              그대로 사용합니다.
            </HelpTooltip>
          </label>
          <NumberField
            label="Random seed"
            value={config.seed}
            step={1}
            disabled={isRunning}
            onChange={(value) => updateConfig({ seed: Math.trunc(value) })}
            description="Monte Carlo 샘플링에 쓰는 난수 시드 - 같은 값이면 항상 동일한 ray 시퀀스로 재현 가능한 결과를 얻습니다."
          />
          <NumberField
            label="Minimum energy"
            value={config.min_energy}
            min={0}
            disabled={isRunning}
            onChange={(value) => updateConfig({ min_energy: value })}
            description="반사광 세기(lm)가 이 값 아래로 떨어지면 종료 대상이 됩니다 - 실제 종료 방식은 아래 Termination 설정을 따릅니다."
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
            description="3D Viewer·Ray Section View에 표시할 최대 경로 수입니다. Receiver 도달 경로가 우선 저장되며 통계 결과에는 영향을 주지 않습니다."
          />
          <label className={fieldLabelClassName}>
            <span className="flex items-center gap-1.5">
              Termination
              <HelpTooltip label="Termination 도움말">
                Energy threshold: Minimum energy 미만이면 즉시 종료합니다.
                Russian roulette: 즉시 끊는 대신 확률적으로 생존시키고
                생존한 ray는 에너지를 보정해, 통계적 편향 없이 계산량을
                줄입니다.
              </HelpTooltip>
            </span>
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
            <span className="flex items-center gap-1.5">
              Contribution
              <HelpTooltip label="Contribution 도움말">
                Fast summary: 집계 통계만 빠르게 계산합니다. Detailed: face별
                기여도까지 추적해 상세 분석이 가능하지만 더 오래 걸립니다.
              </HelpTooltip>
            </span>
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
              <span className="flex items-center gap-1.5">
                Store ray paths · Receiver priority
                <HelpTooltip label="Store ray paths 도움말">
                  Receiver 도달 경로를 최우선으로 저장합니다. 저장 한도가 차면
                  이후 발견된 Receiver 경로가 Blocked/Escaped 경로를 대체합니다.
                  끄면 3D Viewer·Ray Section View의 ray 표시가 비활성화됩니다.
                </HelpTooltip>
              </span>
            </label>

            <details className="group/advanced rounded-lg border border-border bg-muted/20">
              <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-2.5 py-2 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted/45 [&::-webkit-details-marker]:hidden">
                <span className="min-w-0 flex-1">고급 옵션</span>
                <span className="text-[11px] font-normal group-open/advanced:hidden">
                  필요할 때만 변경
                </span>
                <span className="hidden text-[11px] font-normal group-open/advanced:inline">
                  접기
                </span>
              </summary>
              <div className="space-y-2 border-t border-border p-2.5">
                <label className={fieldLabelClassName}>
                  <span className="flex items-center gap-1.5">
                    Primary ray sampling
                    <HelpTooltip label="Primary ray sampling 도움말">
                      Source distribution은 Emitter의 원래 분포만 사용합니다.
                      Receiver-directed MIS는 원래 분포와 Receiver 방향 샘플을
                      편향 없이 혼합해 작은 Receiver의 유효 hit를 늘립니다.
                      현재 Lambertian·Isotropic Emitter에 적용되며 Gaussian 등
                      미지원 형식은 자동으로 Source 방식으로 실행됩니다.
                    </HelpTooltip>
                  </span>
                  <select
                    className={inputClassName}
                    aria-label="Primary ray sampling"
                    value={config.primary_sampling_strategy ?? 'source'}
                    disabled={isRunning}
                    onChange={(event) =>
                      updateConfig({
                        primary_sampling_strategy:
                          event.currentTarget.value === 'receiver_mis'
                            ? 'receiver_mis'
                            : 'source',
                      })
                    }
                  >
                    <option value="source">Source distribution (기본)</option>
                    <option value="receiver_mis">
                      Receiver-directed MIS (실험)
                    </option>
                  </select>
                </label>
                {config.primary_sampling_strategy === 'receiver_mis' ? (
                  <NumberField
                    label="Receiver sample ratio"
                    value={config.receiver_importance_fraction ?? 0.5}
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    disabled={isRunning}
                    onChange={(value) =>
                      updateConfig({ receiver_importance_fraction: value })
                    }
                    description="전체 primary ray 중 Receiver 방향으로 제안할 비율입니다. 기본 0.5를 권장하며, 값이 너무 높으면 간접광·차폐 경로 탐색이 부족해질 수 있습니다."
                  />
                ) : null}
                <label className={fieldLabelClassName}>
                  <span className="flex items-center gap-1.5">
                    Reflected ray sampling
                    <HelpTooltip label="Reflected ray sampling 도움말">
                      Surface distribution은 표면의 원래 반사 분포만
                      사용합니다. Receiver-directed bounce MIS는 Lambertian
                      반사점에서 원래 분포와 Receiver 방향을 편향 없이 혼합해
                      차폐 뒤 희귀 반사광 hit를 늘립니다. Specular는 원래 delta
                      경로를 유지하고 Gaussian·Mixed 표면은 정확도 보호를 위해
                      자동으로 Surface 방식으로 실행됩니다.
                    </HelpTooltip>
                  </span>
                  <select
                    className={inputClassName}
                    aria-label="Reflected ray sampling"
                    value={config.bounce_sampling_strategy ?? 'source'}
                    disabled={isRunning}
                    onChange={(event) =>
                      updateConfig({
                        bounce_sampling_strategy:
                          event.currentTarget.value === 'receiver_mis'
                            ? 'receiver_mis'
                            : 'source',
                      })
                    }
                  >
                    <option value="source">Surface distribution (기본)</option>
                    <option value="receiver_mis">
                      Receiver-directed bounce MIS (실험)
                    </option>
                  </select>
                </label>
                {config.bounce_sampling_strategy === 'receiver_mis' ? (
                  <NumberField
                    label="Bounce Receiver sample ratio"
                    value={
                      config.bounce_receiver_importance_fraction ?? 0.5
                    }
                    min={0.05}
                    max={0.95}
                    step={0.05}
                    disabled={isRunning}
                    onChange={(value) =>
                      updateConfig({
                        bounce_receiver_importance_fraction: value,
                      })
                    }
                    description="Lambertian 반사 표본 중 Receiver 방향으로 제안할 비율입니다. 기본 0.5를 권장합니다."
                  />
                ) : null}
                <label className={fieldLabelClassName}>
                  <span className="flex items-center gap-1.5">
                    충돌 계산 방식
                    <HelpTooltip label="충돌 계산 방식 도움말">
                      Ray와 CAD Mesh의 충돌 후보를 찾는 전문 설정입니다. 일반
                      사용자는 자동 최적화(권장)를 사용하면 됩니다. GPU는 고속
                      공간 인덱스(BVH)를 자동으로 사용합니다.
                    </HelpTooltip>
                  </span>
                  <select
                    id={accelerationStructureId}
                    className={inputClassName}
                    aria-label="Acceleration structure"
                    value={config.intersection_backend ?? 'auto'}
                    disabled={isRunning}
                    onChange={(event) =>
                      updateConfig({
                        intersection_backend:
                          event.currentTarget.value === 'brute_force'
                            ? 'brute_force'
                            : event.currentTarget.value === 'bvh'
                              ? 'bvh'
                              : 'auto',
                      })
                    }
                  >
                    <option value="auto">자동 최적화 (권장)</option>
                    <option value="bvh">고속 공간 인덱스 (BVH)</option>
                    <option
                      value="brute_force"
                      disabled={config.compute_backend === 'gpu_cuda'}
                    >
                      직접 삼각형 검사 (Brute force · CPU 전용)
                    </option>
                  </select>
                </label>
                <p className="text-[11px] leading-4 text-muted-foreground">
                  {config.compute_backend === 'gpu_cuda'
                    ? 'GPU 선택 시 호환되는 고속 방식이 자동 적용됩니다.'
                    : '특별한 검증 목적이 없다면 자동 설정을 유지하세요.'}
                </p>
              </div>
            </details>
          </div>
        </details>
      </section>

      <section className="space-y-2 border-t border-border pt-3">
        {isRunning && activeJobId ? (
          <Button
            className="w-full"
            variant="destructive"
            disabled={stopMutation.isPending || job?.phase === 'stopping'}
            onClick={() => {
              autoConvergenceActiveRef.current = false
              stopMutation.mutate({ jobId: activeJobId })
            }}
          >
            {stopMutation.isPending || job?.phase === 'stopping' ? (
              <LoaderCircle className="animate-spin" />
            ) : (
              <Square />
            )}
            {job?.phase === 'stopping'
              ? 'Stopping · 부분 결과 정리 중'
              : 'Stop and analyze partial result'}
          </Button>
        ) : (
          <Button
            className="w-full"
            disabled={!canRun}
            title={
              config.compute_backend === 'gpu_cuda' && !gpuCudaReady
                ? 'GPU 준비 상태를 확인한 뒤 실행할 수 있습니다.'
                : undefined
            }
            onClick={() => void handleRun()}
          >
            <Play />
            Run Ray Tracing
          </Button>
        )}
        <p className="text-xs leading-4 text-muted-foreground">
          Emitter {enabledEmitterCount} · Receiver {enabledReceiverCount} ·
          Rays {enabledEmitterRayCount.toLocaleString()} · ROI{' '}
          {roiScopes.filter((scope) => scope.active).length} scope
        </p>

        {job ? (
          <div className="rounded-xl border border-primary/20 bg-primary/5 p-3">
            <div className="flex items-center justify-between gap-2 text-sm">
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
            <div className="mt-2 flex justify-between text-xs text-muted-foreground">
              <span>
                {job.processed_rays.toLocaleString()} /{' '}
                {job.total_rays.toLocaleString()} rays
              </span>
              <span>
                {job.status === 'completed'
                  ? `${job.phase === 'stopped' ? 'stopped · partial result' : 'complete'} · ${formatDuration(job.elapsed_sec)}`
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
                <div className="text-xs text-muted-foreground">
                  {label}
                </div>
                <div className="mt-0.5 text-base font-semibold">
                  {value}
                </div>
              </div>
            ))}
          </div>
        ) : null}

        {convergenceHistory.length > 0 ? (
          <details className="rounded-lg border border-border bg-muted/15 p-2" open={config.auto_convergence}>
            <summary className="cursor-pointer text-sm font-semibold">
              Convergence history · {convergenceHistory.length} run{convergenceHistory.length > 1 ? 's' : ''}
            </summary>
            <div className="mt-2 grid grid-cols-3 gap-1.5">
              <ConvergenceSparkline label="Error %" values={convergenceHistory.map((item) => Math.max(item.totalError, item.peakError))} />
              <ConvergenceSparkline label="Peak nit" values={convergenceHistory.map((item) => item.peakNit)} />
              <ConvergenceSparkline label="Flux lm" values={convergenceHistory.map((item) => item.flux)} />
            </div>
            <div className="mt-1 flex justify-between font-mono text-xs text-muted-foreground">
              <span>{convergenceHistory[0]?.rays.toLocaleString()} rays</span>
              <span>{convergenceHistory.at(-1)?.rays.toLocaleString()} rays</span>
            </div>
          </details>
        ) : null}

        {autoConvergenceStatus ? (
          <p
            role="status"
            className="rounded-lg border border-primary/25 bg-primary/5 p-2 text-xs leading-4 text-foreground"
          >
            {autoConvergenceStatus}
          </p>
        ) : null}

        {errorMessage ? (
          <p className="rounded-lg border border-destructive/30 bg-destructive/8 p-2 text-xs leading-4 text-destructive">
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
          editingReceiver?.placement_mode === 'current_view'
            ? 'current_view'
            : receiverMode ?? 'datum_plane'
        }
        scene={scene}
        cameraFrame={cameraFrame}
        existingIds={receivers.map((receiver) => receiver.receiver_id)}
        initialReceiver={editingReceiver}
        onOpenChange={(open) => {
          if (!open) {
            actions.setDatumFacePickArmed(false)
            actions.setSelectedFaceIds([])
            actions.setSelectedComponentIds([])
            setReceiverMode(null)
            setEditingReceiverId(null)
          }
        }}
        onApply={(receiver) => {
          actions.upsertReceiver(receiver)
          actions.setSelectedFaceIds([])
          actions.setSelectedComponentIds([])
        }}
      />
    </div>
  )
}
