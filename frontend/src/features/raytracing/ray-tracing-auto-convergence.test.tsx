// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { RayTraceJob } from '@/api'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import {
  createCompletedRayTraceJobFixture,
  createRayTraceResultFixture,
} from '@/test/raytrace-fixture'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBsdfFixture } from '@/test/bsdf-fixture'

import { RayTracingPanel } from './ray-tracing-panel'
import {
  createDatumEmitter,
  createDatumReceiver,
  createCurrentViewReceiver,
  createFaceEmitter,
  distributeIntegerBudget,
  groupSelectedCadFaces,
  rotationFromPlaneAxes,
} from './ray-tracing-model'
import { createEmitterAim } from './emitter-aim'

const apiHookState = vi.hoisted(() => ({
  job: undefined as RayTraceJob | undefined,
  matchJobId: false,
  start: vi.fn(),
  stop: vi.fn(),
}))

vi.mock('@/api', async () => {
  const actual = await vi.importActual<typeof import('@/api')>('@/api')
  return {
    ...actual,
    useRayTraceJobQuery: (jobId: string | null) => ({
      data: !apiHookState.matchJobId || apiHookState.job?.job_id === jobId
        ? apiHookState.job : undefined,
      error: null,
    }),
    useStartRayTraceMutation: () => ({
      mutateAsync: apiHookState.start,
      isPending: false,
      error: null,
    }),
    useStopRayTraceMutation: () => ({
      mutate: apiHookState.stop,
      isPending: false,
    }),
  }
})

afterEach(() => {
  cleanup()
  apiHookState.job = undefined
  apiHookState.matchJobId = false
  apiHookState.start.mockReset()
  apiHookState.stop.mockReset()
  workspaceStore.getState().actions.resetWorkspace()
})

describe('RayTracingPanel Aim editing', () => {
  it.each(['datum_plane', 'current_view'] as const)('saves edited %s Receiver as a new entry without changing the original', (mode) => {
    const frame = { target: [0, 0, 0] as [number, number, number], normal: [0, 0, 1] as [number, number, number],
      uAxis: [1, 0, 0] as [number, number, number], vAxis: [0, 1, 0] as [number, number, number] }
    const receiver = { ...(mode === 'current_view'
      ? createCurrentViewReceiver('receiver_001', frame, 5, [0, 0, 0], [0, 0, 0])
      : createDatumReceiver('receiver_001', [1, 2, 3], [0, 0, 0])), display_name: 'Original RX',
      resolution: [40, 20] as [number, number], acceptance_angle_deg: 45, source_face_indices: mode === 'datum_plane' ? [1] : [] }
    workspaceStore.getState().actions.upsertReceiver(receiver)
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Edit Original RX' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Receiver name' }), { target: { value: 'New RX' } })
    fireEvent.change(screen.getByLabelText('Receiver width (mm)'), { target: { value: '42' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save As' }))
    const receivers = workspaceStore.getState().receivers
    expect(receivers).toHaveLength(2)
    expect(receivers[0]).toEqual(receiver)
    expect(receivers[1]).toMatchObject({ display_name: 'New RX', width_mm: 42,
      resolution: [40, 20], acceptance_angle_deg: 45, placement_mode: mode,
      source_face_indices: receiver.source_face_indices })
    expect(receivers[1].receiver_id).not.toBe(receiver.receiver_id)
    expect(screen.queryByRole('button', { name: 'Save As' })).toBeNull()
  })

  it('Receiver Save As allocates a new ID and labels an unchanged name as a copy', () => {
    const original = { ...createDatumReceiver('receiver_001', [1, 2, 3], [0, 0, 0]), display_name: 'Original RX' }
    workspaceStore.getState().actions.upsertReceiver(original)
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Edit Original RX' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save As' }))
    const receivers = workspaceStore.getState().receivers
    expect(receivers).toHaveLength(2)
    expect(receivers[0]).toEqual(original)
    expect(receivers[1].receiver_id).not.toBe(original.receiver_id)
    expect(receivers[1].display_name).toBe('Original RX copy')
    expect(receivers[1].center).toEqual(original.center)
  })

  it.each([
    ['area', 'lambertian'], ['area', 'gaussian'],
    ['sphere', 'lambertian'], ['sphere', 'gaussian'],
  ] as const)('saves and reopens %s %s without overwriting the base distribution', (mode, distribution) => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.direction_distribution = 'gaussian'
    emitter.gaussian_sigma_deg = 7.5
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: mode } })
    const select = screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement
    expect(select.disabled).toBe(false)
    expect(select.value).toBe(mode === 'area' ? 'uniform_target_area' : 'uniform_solid_angle')
    fireEvent.change(select, { target: { value: distribution } })
    expect((screen.getByRole('button', { name: 'Both Sides' }) as HTMLButtonElement).disabled).toBe(mode === 'sphere')
    if (distribution === 'gaussian') {
      fireEvent.change(screen.getByLabelText('Emitter Gaussian sigma (deg)'), { target: { value: '21.5' } })
    }
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    expect(workspaceStore.getState().emitters[0]).toMatchObject({
      direction_distribution: 'gaussian', gaussian_sigma_deg: 7.5,
      aim: { mode, distribution, gaussian_sigma_deg: distribution === 'gaussian' ? 21.5 : 12 },
    })
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe(distribution)
    if (distribution === 'gaussian') expect((screen.getByLabelText('Emitter Gaussian sigma (deg)') as HTMLInputElement).value).toBe('21.5')
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: mode === 'area' ? 'sphere' : 'area' } })
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe(distribution)
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'off' } })
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe('gaussian')
    expect((screen.getByLabelText('Emitter Gaussian sigma (deg)') as HTMLInputElement).value).toBe('7.5')
  })

  it('rejects a Lambertian Sphere outside the forward hemisphere', () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true, mode: 'sphere', distribution: 'lambertian' }
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Aim Sphere Upper'), { target: { value: '90' } })
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByRole('alert').textContent).toContain('Upper가 90° 미만')
    fireEvent.click(screen.getByRole('button', { name: '평행광' }))
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('makes the full-sphere preset a true uniform 4π distribution', () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'sphere.step', displayName: 'sphere.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'area' } })
    fireEvent.change(screen.getByLabelText('Emitter direction distribution'), { target: { value: 'lambertian' } })
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'sphere' } })
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe('lambertian')
    fireEvent.click(screen.getByRole('button', { name: '전방위' }))
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe('uniform_solid_angle')
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    expect(workspaceStore.getState().emitters[0].aim).toMatchObject({
      mode: 'sphere',
      distribution: 'uniform_solid_angle',
      sphere_upper_deg: 0,
      sphere_lower_deg: 180,
    })
  })

  it('creates one independently editable Emitter and Aim per selected CAD face', async () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [10, 10, 11, 20, 20]
    render(<AppProviders><RayTracingPanel scene={scene} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Add CAD Surface Emitter' }))
    act(() => workspaceStore.getState().actions.setSelectedFaceIds([0, 1, 2]))
    await waitFor(() => expect(screen.getByLabelText('Emitter grouping')).toBeTruthy())
    fireEvent.change(screen.getByLabelText('Emitter grouping'), { target: { value: 'per_cad_face' } })
    fireEvent.change(screen.getByLabelText('Emitter power mode'), { target: { value: 'total' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Total power (lm)' }), { target: { value: '9' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Emitter rays' }), { target: { value: '101' } })
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'sphere' } })
    fireEvent.click(screen.getByRole('button', { name: 'Add Emitter' }))

    const emitters = workspaceStore.getState().emitters
    expect(emitters).toHaveLength(2)
    expect(emitters.map((emitter) => emitter.face_indices)).toEqual([[0, 1], [2]])
    expect(emitters.map((emitter) => emitter.power_lumen)).toEqual([4.5, 4.5])
    expect(emitters.reduce((sum, emitter) => sum + emitter.ray_count, 0)).toBe(101)
    expect(emitters.every((emitter) => emitter.aim?.mode === 'sphere')).toBe(true)
    expect(new Set(emitters.map((emitter) => emitter.emitter_id)).size).toBe(2)
    expect(emitters.every((emitter) => emitter.array_group_id === 'led_array_001')).toBe(true)

    fireEvent.click(screen.getByRole('button', { name: 'Edit LED Array 1' }))
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Total power (lm)' }), { target: { value: '12' } })
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Aim Sphere Alpha'), { target: { value: '25' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    const updated = workspaceStore.getState().emitters
    expect(updated.map((emitter) => emitter.power_lumen)).toEqual([6, 6])
    expect(updated.every((emitter) => emitter.aim?.sphere_alpha_deg === 25)).toBe(true)
    expect(updated.reduce((sum, emitter) => sum + emitter.ray_count, 0)).toBe(101)
  })

  it('reassigns Copy Setup LED-per-face emitters to newly selected target CAD faces', () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [10, 10, 11, 20, 20]
    const first = {
      ...createFaceEmitter('emitter_001', []),
      array_group_id: 'led_array_001',
      array_index: 0,
      array_power_basis: 'total_array' as const,
    }
    const second = {
      ...createFaceEmitter('emitter_002', []),
      array_group_id: 'led_array_001',
      array_index: 1,
      array_power_basis: 'total_array' as const,
    }
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'copy-target.step', displayName: 'copy-target.step' })
      workspaceStore.getState().actions.upsertEmitter(first)
      workspaceStore.getState().actions.upsertEmitter(second)
    })
    render(<AppProviders><RayTracingPanel scene={scene} cameraFrame={null} /></AppProviders>)

    fireEvent.click(screen.getByRole('button', { name: 'Edit LED Array 1' }))
    expect(screen.getByRole('button', { name: '뷰어에서 CAD Face 선택' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '뷰어에서 CAD Face 선택' }))
    act(() => workspaceStore.getState().actions.setSelectedFaceIds([3, 4]))
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))

    expect(workspaceStore.getState().emitters).toEqual([
      expect.objectContaining({
        emitter_id: 'emitter_001',
        face_indices: [3, 4],
        array_group_id: 'led_array_001',
        array_index: 0,
      }),
    ])
  })

  it('groups tessellation triangles by component and original CAD face', () => {
    const scene = createSceneFixture()
    scene.mesh.face_source_ids = [7, 7, 8, 7, 7]
    expect(groupSelectedCadFaces(scene, [4, 0, 3, 2, 1])).toEqual([
      [0, 1],
      [2],
      [3, 4],
    ])
    expect(distributeIntegerBudget(101, [1, 1])).toEqual([51, 50])
  })

  it('saves a two-sided Emitter while preserving one Emitter identity', () => {
    const emitter = createDatumEmitter('emitter_001', [2, 3, 4], [0, 0, 0])
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'both.step', displayName: 'both.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByRole('button', { name: 'Both Sides' }))
    expect(workspaceStore.getState().placementPreviewEmitter).toMatchObject({
      emission_direction: 'both',
      normal_flip: false,
    })
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    expect(workspaceStore.getState().emitters).toHaveLength(1)
    expect(workspaceStore.getState().emitters[0]).toMatchObject({
      emitter_id: 'emitter_001',
      emission_direction: 'both',
      normal_flip: false,
    })
  })

  it('edits a bidirectional Sphere on an existing surface without adding a new emitter type', async () => {
    const emitter = createDatumEmitter('emitter_001', [2, 3, 4], [20, 0, 0])
    emitter.direction_distribution = 'gaussian'
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    expect(screen.getByText('Aim / Target').closest('details')?.open).toBe(false)
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'sphere' } })
    expect(screen.queryByLabelText('Aim position X')).toBeNull()
    expect((screen.getByLabelText('Aim Sphere Lower') as HTMLInputElement).value).toBe('180')
    for (const [label, value] of [['Upper', '20'], ['Lower', '140'], ['Alpha', '32.5'], ['Beta', '-65']]) {
      const input = screen.getByLabelText(`Aim Sphere ${label}`)
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value } })
      fireEvent.blur(input)
    }
    const draft = workspaceStore.getState().placementPreviewEmitter!
    expect(draft.emitter_type).toBe('datum_plane')
    expect(draft.center).toEqual([2, 3, 4])
    expect(draft.aim).toMatchObject({ mode: 'sphere', sphere_upper_deg: 20, sphere_lower_deg: 140, sphere_alpha_deg: 32.5, sphere_beta_deg: -65 })
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    expect((screen.getByLabelText('Aim Sphere Alpha') as HTMLInputElement).value).toBe('32.5')
    fireEvent.change(screen.getByLabelText('Aim Sphere Lower'), { target: { value: '10' } })
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: '평행광' }))
    expect(workspaceStore.getState().placementPreviewEmitter!.aim).toMatchObject({ sphere_upper_deg: 0, sphere_lower_deg: 0 })
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: '전방위' }))
    expect(workspaceStore.getState().placementPreviewEmitter!.aim?.sphere_lower_deg).toBe(180)
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'area' } })
    expect(screen.getByLabelText('Aim position X')).not.toBeNull()
    expect(screen.queryByLabelText('Aim Sphere Upper')).toBeNull()
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'off' } })
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe('gaussian')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter).toBeNull())
    expect(workspaceStore.getState().emitters[0].aim?.sphere_lower_deg).toBe(140)
  })

  it('keeps all Tilt axes when editing sequentially and reopening the saved emitter', async () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true }
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    for (const [axis, value] of [['X', 20], ['Y', -30], ['Z', 15]] as const) {
      const input = screen.getByLabelText(`Aim tilt ${axis}`)
      fireEvent.focus(input)
      fireEvent.change(input, { target: { value: String(value) } })
      fireEvent.blur(input)
    }
    const draft = workspaceStore.getState().placementPreviewEmitter!.aim!
    rotationFromPlaneAxes(draft.u_axis, draft.v_axis, null).forEach(
      (value, index) => expect(value).toBeCloseTo([20, -30, 15][index], 10),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter).toBeNull())
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    for (const [axis, value] of [['X', 20], ['Y', -30], ['Z', 15]] as const) {
      expect((screen.getByLabelText(`Aim tilt ${axis}`) as HTMLInputElement).value).toBe(String(value))
    }
  })

  it('previews Aim, applies it, and restores the original distribution when switched off', async () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.direction_distribution = 'gaussian'
    const actions = workspaceStore.getState().actions
    act(() => {
      actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      actions.upsertEmitter(emitter)
    })
    const scene = createSceneFixture()
    render(<AppProviders><RayTracingPanel scene={scene} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    const summary = screen.getByText('Aim / Target')
    expect(summary.closest('details')?.open).toBe(false)
    fireEvent.click(summary)
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'area' } })
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).disabled).toBe(false)
    expect((screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement).value).toBe('uniform_target_area')
    fireEvent.change(screen.getByLabelText('Aim shape'), { target: { value: 'circle' } })
    fireEvent.change(screen.getByLabelText('Aim diameter (mm)'), { target: { value: '6' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim).toMatchObject({ enabled: true, shape: 'circle', radius_mm: 3 }))
    expect(workspaceStore.getState().emitters[0].aim).toBeUndefined()
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    await waitFor(() => expect(workspaceStore.getState().emitters[0].aim?.radius_mm).toBe(3))
    expect(workspaceStore.getState().emitters[0].luminance_nit).toBe(500)
    expect(workspaceStore.getState().placementPreviewEmitter).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.change(screen.getByLabelText('Emitter aiming mode'), { target: { value: 'off' } })
    const distribution = screen.getByLabelText('Emitter direction distribution') as HTMLSelectElement
    expect(distribution.disabled).toBe(false)
    expect(distribution.value).toBe('gaussian')
    fireEvent.click(screen.getByRole('button', { name: 'Save Emitter' }))
    expect(workspaceStore.getState().emitters[0].aim?.enabled).toBe(false)
  })

  it('discards edited Target coordinates on Cancel and refuses a zero-size Target', async () => {
    const emitter = createDatumEmitter('emitter_001', [0, 0, 0], [0, 0, 0])
    emitter.aim = { ...createEmitterAim([0, 0, 30]), enabled: true }
    act(() => {
      workspaceStore.getState().actions.addCadCase({ path: 'aim.step', displayName: 'aim.step' })
      workspaceStore.getState().actions.upsertEmitter(emitter)
    })
    render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: /Edit Emitter 1/i }))
    fireEvent.click(screen.getByText('Aim / Target'))
    fireEvent.keyDown(screen.getByText('Aim / Target'), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Save Emitter' })).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Aim position X'), { target: { value: '-1.25' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim?.center[0]).toBe(-1.25))
    fireEvent.change(screen.getByLabelText('Aim tilt Y'), { target: { value: '30' } })
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter?.aim?.u_axis[2]).toBeCloseTo(-0.5))
    fireEvent.click(screen.getByLabelText('Show Aim Target'))
    expect(workspaceStore.getState().placementPreviewEmitter?.aim?.show_in_viewer).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Reset Target' }))
    expect(workspaceStore.getState().placementPreviewEmitter?.aim).toMatchObject({ enabled: true, u_axis: [1, 0, 0], show_in_viewer: true })
    fireEvent.change(screen.getByLabelText('Aim width (mm)'), { target: { value: '0' } })
    expect((screen.getByRole('button', { name: 'Save Emitter' }) as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(workspaceStore.getState().emitters[0].aim).toEqual(emitter.aim)
    await waitFor(() => expect(workspaceStore.getState().placementPreviewEmitter).toBeNull())
  })
})

describe('RayTracingPanel Auto convergence', () => {
  it('keeps Stop available after a shared BSDF edit and discards the stopped stale result', async () => {
    const result = createRayTraceResultFixture()
    const actions = workspaceStore.getState().actions
    const asset = { ...createBsdfFixture(), simulationReady: true,
      transport: { model: 'isotropic_no_shift' as const, wavelengthNm: 550, aoiExtrapolation: 'clamp' as const } }
    act(() => {
      actions.addCadCase({ path: 'shared.step', displayName: 'shared.step' })
      actions.addBsdfAsset(asset)
      actions.upsertMaterialAssignment({ assignmentId: 'part', componentId: 1,
        targetType: 'part', faceIds: [], baseMaterialId: 'pc_black', surfaceId: 'matte_black_resin',
        profileId: '', bsdfAssetId: asset.id, enabled: true })
      actions.upsertEmitter(result.emitters[0])
      actions.upsertReceiver(result.receivers[0])
      actions.setRayTraceConfig({ ...result.config, auto_convergence: false })
    })
    apiHookState.matchJobId = true
    apiHookState.start.mockResolvedValueOnce({ job_id: 'shared-job' })
    const { result: _completedResult, ...runningJob } = createCompletedRayTraceJobFixture()
    apiHookState.job = { ...runningJob, job_id: 'shared-job', status: 'running', phase: 'tracing' }
    const view = render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await screen.findByRole('button', { name: 'Stop and analyze partial result' })
    act(() => actions.addBsdfAsset({ ...asset, transport: { ...asset.transport, wavelengthNm: 540 } }))
    expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
    expect(workspaceStore.getState().singleRayTraceRun?.jobId).toBe('shared-job')
    fireEvent.click(screen.getByRole('button', { name: 'Stop and analyze partial result' }))
    expect(apiHookState.stop).toHaveBeenCalledWith({ jobId: 'shared-job' })
    apiHookState.job = { ...createCompletedRayTraceJobFixture(), job_id: 'shared-job' }
    view.rerender(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    await waitFor(() => expect(workspaceStore.getState().singleRayTraceRun).toBeNull())
    expect(workspaceStore.getState().cadCases[0].latestResult).toBeNull()
    expect(workspaceStore.getState().restoredRayTraceResult).toBeNull()
    expect(screen.getByText(/이전 조건의 결과를 저장하지 않았습니다/)).not.toBeNull()
    expect(apiHookState.start).toHaveBeenCalledTimes(1)
  })

  it('discards a completed single run when its Case inputs changed in flight', async () => {
    const result = createRayTraceResultFixture()
    const actions = workspaceStore.getState().actions
    act(() => {
      actions.addCadCase({ path: 'stale.step', displayName: 'stale.step' })
      actions.upsertEmitter(result.emitters[0])
      actions.upsertReceiver(result.receivers[0])
      actions.setRayTraceConfig({
        ...result.config,
        auto_convergence: false,
      })
    })
    apiHookState.start.mockResolvedValueOnce({ job_id: 'stale-job' })
    const view = render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(workspaceStore.getState().singleRayTraceRun)
      .toMatchObject({ jobId: 'stale-job' }))

    act(() => actions.setRayTraceConfig({
      ...workspaceStore.getState().rayTraceConfig,
      max_depth: workspaceStore.getState().rayTraceConfig.max_depth + 1,
    }))
    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'stale-job'
    apiHookState.job = completed
    view.rerender(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    await waitFor(() => expect(screen.getByText(
      /이전 조건의 결과를 저장하지 않았습니다/,
    )).not.toBeNull())
    expect(workspaceStore.getState().singleRayTraceRun).toBeNull()
    expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
    expect(workspaceStore.getState().cadCases[0].latestResult).toBeNull()
  })

  it('keeps the execution lock while the next segment is starting', async () => {
    const result = createRayTraceResultFixture()
    const actions = workspaceStore.getState().actions
    act(() => {
      actions.addCadCase({ path: 'baseline.step', displayName: 'baseline.step' })
      actions.addCadCase({ path: 'active.step', displayName: 'active.step' })
      actions.upsertEmitter({ ...result.emitters[0], ray_count: 100 })
      actions.upsertReceiver(result.receivers[0])
      actions.setRayTraceConfig({
        ...result.config,
        auto_convergence: true,
        convergence_target_percent: 5,
        max_convergence_multiplier: 8,
      })
    })
    const [baselineCase, activeCase] = workspaceStore.getState().cadCases
    let resolveSecondSegment!: (value: { job_id: string }) => void
    const secondSegment = new Promise<{ job_id: string }>((resolve) => {
      resolveSecondSegment = resolve
    })
    apiHookState.start
      .mockResolvedValueOnce({ job_id: 'segment-1' })
      .mockImplementationOnce(() => secondSegment)

    const view = render(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(workspaceStore.getState().singleRayTraceRun)
      .toEqual({ caseId: activeCase.caseId, jobId: 'segment-1' }))

    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'segment-1'
    completed.result.total_rays = 100
    completed.total_rays = 100
    completed.result.metrics.receiver_001 = {
      ...(completed.result.metrics.receiver_001 as Record<string, unknown>),
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = completed
    view.rerender(
      <AppProviders>
        <RayTracingPanel scene={createSceneFixture()} cameraFrame={null} />
      </AppProviders>,
    )

    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(2))
    expect(workspaceStore.getState().singleRayTraceRun).toEqual({
      caseId: activeCase.caseId,
      jobId: null,
    })
    act(() => {
      actions.setActiveCadCase(baselineCase.caseId)
      actions.addCadCase({ path: 'blocked.step', displayName: 'blocked.step' })
    })
    expect(workspaceStore.getState().activeCadCaseId).toBe(activeCase.caseId)
    expect(workspaceStore.getState().cadCases).toHaveLength(2)

    await act(async () => resolveSecondSegment({ job_id: 'segment-2' }))
    await waitFor(() => expect(workspaceStore.getState().singleRayTraceRun)
      .toEqual({ caseId: activeCase.caseId, jobId: 'segment-2' }))
  })

  it('keeps a retry start response alive so a closed window can stop the created job by id', async () => {
    const result = createRayTraceResultFixture()
    const actions = workspaceStore.getState().actions
    act(() => {
      actions.addCadCase({ path: 'delayed-retry.step', displayName: 'delayed-retry.step' })
      actions.upsertEmitter({ ...result.emitters[0], ray_count: 100 })
      actions.upsertReceiver(result.receivers[0])
      actions.setRayTraceConfig({
        ...result.config,
        auto_convergence: true,
        convergence_target_percent: 5,
        max_convergence_multiplier: 8,
      })
    })
    const caseId = workspaceStore.getState().activeCadCaseId!
    let resolveRetry!: (value: { job_id: string }) => void
    const delayedRetry = new Promise<{ job_id: string }>((resolve) => {
      resolveRetry = resolve
    })
    apiHookState.start
      .mockResolvedValueOnce({ job_id: 'delayed-segment-1' })
      .mockImplementationOnce(() => delayedRetry)

    const { rerender } = render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(1))

    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'delayed-segment-1'
    completed.result.total_rays = 100
    completed.total_rays = 100
    completed.result.metrics.receiver_001 = {
      ...(completed.result.metrics.receiver_001 as Record<string, unknown>),
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = completed
    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(2))
    expect(apiHookState.start.mock.calls[1][0]).not.toHaveProperty('signal')
    expect(workspaceStore.getState().singleRayTraceRun).toEqual({
      caseId,
      jobId: null,
    })

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    // Closing the result window must not release the global lock while the
    // start response (and therefore the server job id) is still in flight.
    await waitFor(() => expect(workspaceStore.getState().singleRayTraceRun)
      .toEqual({ caseId, jobId: null }))
    expect(apiHookState.stop).not.toHaveBeenCalled()

    await act(async () => resolveRetry({ job_id: 'delayed-segment-2' }))
    await waitFor(() => expect(apiHookState.stop).toHaveBeenCalledWith({
      jobId: 'delayed-segment-2',
    }))
    expect(workspaceStore.getState().singleRayTraceRun).toEqual({
      caseId,
      jobId: 'delayed-segment-2',
    })
    expect(workspaceStore.getState().activeRayTraceJobId).toBe(
      'delayed-segment-1',
    )
  })

  it('adds an independent segment when Flux converges but the pixel Peak is noisy', async () => {
    const result = createRayTraceResultFixture()
    act(() => {
      const actions = workspaceStore.getState().actions
      actions.addCadCase({ path: 'peak.step', displayName: 'peak.step' })
      actions.upsertEmitter({ ...result.emitters[0], ray_count: 100 })
      actions.upsertReceiver(result.receivers[0])
      actions.setRayTraceConfig({ ...result.config, auto_convergence: true, convergence_target_percent: 5, max_convergence_multiplier: 8 })
    })
    apiHookState.start.mockResolvedValueOnce({ job_id: 'peak-first' }).mockResolvedValueOnce({ job_id: 'peak-second' })
    const view = render(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(1))
    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'peak-first'
    completed.result.metrics.receiver_001 = {
      ...(completed.result.metrics.receiver_001 as Record<string, unknown>),
      error_estimate_percent: 0.5, peak_area_error_estimate_percent: 0.8,
      peak_error_estimate_percent: 12, peak_effective_sample_count: 100,
    }
    apiHookState.job = completed
    view.rerender(<AppProviders><RayTracingPanel scene={createSceneFixture()} cameraFrame={null} /></AppProviders>)
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(2))
    expect(apiHookState.start.mock.calls[1][0].request.config.seed).not.toBe(apiHookState.start.mock.calls[0][0].request.config.seed)
    expect(screen.queryByText(/연속 2회 Peak 안정성 기준을 충족/)).toBeNull()
  })

  it('stops a closed-window retry without reusing its cancel token on a later run', async () => {
    const result = createRayTraceResultFixture()
    const emitter = { ...result.emitters[0], ray_count: 100, seed: 7 }
    const receiver = result.receivers[0]
    const actions = workspaceStore.getState().actions

    act(() => {
      actions.addCadCase({ path: 'auto.step', displayName: 'auto.step' })
      actions.upsertEmitter(emitter)
      actions.upsertReceiver(receiver)
      actions.setRayTraceConfig({
        ...result.config,
        auto_convergence: true,
        convergence_target_percent: 5,
        max_convergence_multiplier: 8,
      })
    })

    apiHookState.start
      .mockResolvedValueOnce({ job_id: 'job-1' })
      .mockResolvedValueOnce({ job_id: 'job-auto-retry' })
      .mockResolvedValueOnce({ job_id: 'job-2' })
      .mockResolvedValueOnce({ job_id: 'job-2-auto-retry' })

    const { rerender } = render(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(1))

    const completed = createCompletedRayTraceJobFixture()
    completed.job_id = 'job-1'
    completed.result.total_rays = 100
    completed.total_rays = 100
    const receiverMetrics = completed.result.metrics.receiver_001 as Record<
      string,
      unknown
    >
    completed.result.metrics.receiver_001 = {
      ...receiverMetrics,
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = completed

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={0}
        />
      </AppProviders>,
    )

    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(2))
    expect(apiHookState.start.mock.calls[0][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 42,
    })
    expect(apiHookState.start.mock.calls[0][0].request.emitters[0].seed).toBe(7)
    expect(apiHookState.start.mock.calls[1][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 1_000_045,
    })
    expect(apiHookState.start.mock.calls[1][0].request.emitters[0].seed).toBe(
      1_000_010,
    )
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe(
        'job-auto-retry',
      ),
    )
    expect(workspaceStore.getState().singleRayTraceRun?.jobId).toBe(
      'job-auto-retry',
    )

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    await waitFor(() =>
      expect(apiHookState.stop).toHaveBeenCalledWith({
        jobId: 'job-auto-retry',
      }),
    )
    expect(workspaceStore.getState().activeRayTraceJobId).toBeNull()
    expect(workspaceStore.getState().singleRayTraceRun?.jobId).toBe(
      'job-auto-retry',
    )

    // SimulatorShell releases the owner only after its independent tracker
    // confirms that the backend job is terminal.
    act(() => actions.clearSingleRayTraceRun('job-auto-retry'))
    expect(workspaceStore.getState().singleRayTraceRun).toBeNull()

    apiHookState.job = undefined
    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Run Ray Tracing' }))
    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(3))
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe('job-2'),
    )

    const secondCompleted = createCompletedRayTraceJobFixture()
    secondCompleted.job_id = 'job-2'
    secondCompleted.result.total_rays = 100
    secondCompleted.total_rays = 100
    secondCompleted.result.metrics.receiver_001 = {
      ...(secondCompleted.result.metrics.receiver_001 as Record<
        string,
        unknown
      >),
      hit_count: 100,
      error_estimate_percent: 12,
      peak_area_error_estimate_percent: 14,
    }
    apiHookState.job = secondCompleted

    rerender(
      <AppProviders>
        <RayTracingPanel
          scene={createSceneFixture()}
          cameraFrame={null}
          autoConvergenceCancelToken={1}
        />
      </AppProviders>,
    )

    await waitFor(() => expect(apiHookState.start).toHaveBeenCalledTimes(4))
    expect(apiHookState.start.mock.calls[2][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 42,
    })
    expect(apiHookState.start.mock.calls[3][0].request.config).toMatchObject({
      ray_count: 100,
      seed: 1_000_045,
    })
    await waitFor(() =>
      expect(workspaceStore.getState().activeRayTraceJobId).toBe(
        'job-2-auto-retry',
      ),
    )
  })
})
