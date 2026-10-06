// @vitest-environment jsdom

import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore, type MaterialAssignment } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBitsamProject, parseBitsamProject, serializeBitsamProject } from '@/features/projects/bitsam-project'
import { buildRayTraceRequest } from '@/features/raytracing/ray-tracing-model'
import { MaterialEditorDialog } from './material-editor-dialog'
import { SurfacePropertyDialog } from './surface-property-dialog'

const scene = createSceneFixture()
scene.mesh.face_source_ids = [10, 10, 11, 20, 20]
const part: MaterialAssignment = { assignmentId: 'material-part-1', componentId: 1, targetType: 'part', faceIds: [],
  baseMaterialId: 'pc_black', surfaceId: 'semi_gloss_black_resin', profileId: '', bsdfAssetId: '', enabled: true,
  opticalOverride: { reflectance: 0.6, loss: 0.4, specularRatio: 0.25, diffuseRatio: 0.75 } }

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverStub

function show(initialTarget: 'part' | 'faces' = 'faces') {
  return render(<AppProviders><SurfacePropertyDialog open onOpenChange={vi.fn()} scene={scene}
    component={scene.components[0]} componentName="Cover Deco" initialTarget={initialTarget} /></AppProviders>)
}

function payload(assignments = workspaceStore.getState().materialAssignments) {
  return buildRayTraceRequest({ scene, projectName: 'optics', emitters: [], receivers: [], materialAssignments: assignments,
    transformRules: [], excludedComponentIds: [], deletedComponentIds: [], roiScopes: [], config: workspaceStore.getState().rayTraceConfig })
}

function selectFace() {
  workspaceStore.getState().actions.setFaceSelection([0], [1])
}

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace() })

describe('surface optical editing', () => {
  it('shows effective inherited custom values and does not replace them with catalog values on a finish change', () => {
    workspaceStore.getState().actions.upsertMaterialAssignment(part)
    selectFace()
    show()
    expect(within(screen.getByLabelText('현재 적용 광학 값')).getByText('0.600')).not.toBeNull()
    fireEvent.change(screen.getByLabelText('Face surface property'), { target: { value: 'matte_black_resin' } })
    expect(screen.getByLabelText('Optical settings mode')).toHaveProperty('value', 'custom')
    expect(screen.getByLabelText('Custom Reflectance')).toHaveProperty('value', '0.6')
    expect(screen.getByLabelText('Custom Reflection model')).toHaveProperty('value', 'lambertian')
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    const face = workspaceStore.getState().materialAssignments.find((item) => item.targetType === 'faces')!
    expect(face).toMatchObject({ faceIds: [0, 1], opticalOverride: { reflectance: 0.6, scatterModel: 'lambertian' } })
    expect(workspaceStore.getState().materialAssignments.find((item) => item.targetType === 'part')).toEqual(part)
    fireEvent.click(screen.getByRole('button', { name: 'Use part default' }))
    expect(workspaceStore.getState().materialAssignments).toEqual([part])
    expect(screen.getByLabelText('Optical settings mode')).toHaveProperty('value', 'inherit')
  })

  it('requires explicit preset mode to discard a custom reflectance', () => {
    workspaceStore.getState().actions.upsertMaterialAssignment(part)
    selectFace()
    show()
    fireEvent.change(screen.getByLabelText('Optical settings mode'), { target: { value: 'preset' } })
    fireEvent.change(screen.getByLabelText('Face surface property'), { target: { value: 'matte_black_resin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    expect(payload().optical_profiles[1].reflectance).toBeCloseTo(0.0576)
  })

  it('distinguishes unassigned faces from the proposed material default', () => {
    selectFace()
    show()
    expect(screen.getByText('부품 광학 값 미지정 · 반사 없음')).not.toBeNull()
    expect(within(screen.getByLabelText('현재 적용 광학 값')).getByText(/^none · roughness/)).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    expect(payload().optical_profiles).toEqual([])
  })

  it('preserves full custom models through Apply, reopening, BITSAM restore and the request', () => {
    const actions = workspaceStore.getState().actions
    actions.setActiveCad({ path: 'fixture.step', displayName: 'fixture.step' })
    actions.upsertMaterialAssignment(part)
    selectFace()
    const view = show()
    fireEvent.change(screen.getByLabelText('Optical settings mode'), { target: { value: 'custom' } })
    fireEvent.change(screen.getByLabelText('Custom Reflection model'), { target: { value: 'gaussian' } })
    fireEvent.change(screen.getByLabelText('Custom Reflectance'), { target: { value: '0.6134567' } })
    fireEvent.change(screen.getByLabelText('Custom Gaussian sigma (deg)'), { target: { value: '7.321' } })
    fireEvent.change(screen.getByLabelText('Custom Roughness'), { target: { value: '0.13' } })
    expect(screen.queryByLabelText('Custom Specular / Gaussian ratio')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    view.unmount()
    show()
    expect(screen.getByLabelText('Custom Reflectance')).toHaveProperty('value', '0.6134567')
    expect(screen.getByLabelText('Custom Gaussian sigma (deg)')).toHaveProperty('value', '7.321')
    const project = parseBitsamProject(serializeBitsamProject(createBitsamProject(scene, workspaceStore.getState())))
    expect(payload(project.workspace.materialAssignments).optical_profiles[1]).toMatchObject({ reflectance: 0.6134567,
      scatter_model: 'gaussian', roughness: 0.13, gaussian_sigma_deg: 7.321 })
    const corrupt = JSON.parse(serializeBitsamProject(project))
    corrupt.workspace.materialAssignments[1].opticalOverride.scatterModel = 'invalid'
    expect(() => parseBitsamProject(JSON.stringify(corrupt))).toThrow()
  })

  it('uses coupled lobe weights only for Mixed and validates sigma', () => {
    workspaceStore.getState().actions.upsertMaterialAssignment(part)
    selectFace()
    show()
    fireEvent.change(screen.getByLabelText('Optical settings mode'), { target: { value: 'custom' } })
    fireEvent.change(screen.getByLabelText('Custom Specular / Gaussian ratio'), { target: { value: '0.35' } })
    expect(screen.getByLabelText('Custom Diffuse')).toHaveProperty('value', '0.65')
    fireEvent.change(screen.getByLabelText('Custom Gaussian sigma (deg)'), { target: { value: '0' } })
    expect(screen.getByRole('button', { name: 'Apply to selected faces' })).toHaveProperty('disabled', true)
    fireEvent.change(screen.getByLabelText('Custom Gaussian sigma (deg)'), { target: { value: '0.01' } })
    fireEvent.change(screen.getByLabelText('Custom Reflection model'), { target: { value: 'specular' } })
    expect(screen.queryByLabelText('Custom Gaussian sigma (deg)')).toBeNull()
    expect(screen.queryByLabelText('Custom Specular / Gaussian ratio')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    expect(payload().optical_profiles[1].scatter_model).toBe('specular')
  })

  it('does not overwrite mixed selections until an edit is made and keeps independent faces on part edits', () => {
    const actions = workspaceStore.getState().actions
    actions.upsertMaterialAssignment(part)
    actions.upsertMaterialAssignment({ ...part, assignmentId: 'face', targetType: 'faces', faceIds: [0, 1], opticalOverride: { ...part.opticalOverride!, reflectance: 0.2, loss: 0.8 } })
    actions.setFaceSelection([0, 2], [1])
    show()
    expect(screen.getByRole('button', { name: 'Apply to selected faces' })).toHaveProperty('disabled', true)
    fireEvent.change(screen.getByLabelText('Surface edit target'), { target: { value: 'part' } })
    fireEvent.change(screen.getByLabelText('Custom Reflectance'), { target: { value: '0.9' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply to part default' }))
    expect(workspaceStore.getState().materialAssignments.find((item) => item.assignmentId === 'face')!.opticalOverride!.reflectance).toBe(0.2)
    expect(workspaceStore.getState().materialAssignments.find((item) => item.targetType === 'part')!.opticalOverride!.reflectance).toBe(0.9)
    act(() => actions.setFaceSelection([2], [1]))
    fireEvent.change(screen.getByLabelText('Surface edit target'), { target: { value: 'faces' } })
    expect(within(screen.getByLabelText('현재 적용 광학 값')).getByText('0.900')).not.toBeNull()
  })

  it('commits a cleared sigma on Enter without applying stale optical values', () => {
    workspaceStore.getState().actions.upsertMaterialAssignment(part)
    selectFace()
    show()
    fireEvent.change(screen.getByLabelText('Optical settings mode'), { target: { value: 'custom' } })
    const sigma = screen.getByLabelText('Custom Gaussian sigma (deg)')
    fireEvent.change(sigma, { target: { value: '' } })
    fireEvent.keyDown(sigma, { key: 'Enter' })
    expect(workspaceStore.getState().materialAssignments).toEqual([part])
    expect(screen.getByRole('button', { name: 'Apply to selected faces' })).toHaveProperty('disabled', true)
    fireEvent.change(sigma, { target: { value: '3.5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Apply to selected faces' }))
    expect(payload().optical_profiles[1].gaussian_sigma_deg).toBe(3.5)
  })

  it('saves a separate custom dialog into both the part and Material preview without changing face overrides', () => {
    const actions = workspaceStore.getState().actions
    actions.upsertMaterialAssignment(part)
    const face: MaterialAssignment = { ...part, assignmentId: 'face', targetType: 'faces', faceIds: [0, 1],
      opticalOverride: { ...part.opticalOverride!, reflectance: 0.2, loss: 0.8 } }
    actions.upsertMaterialAssignment(face)
    render(<AppProviders><MaterialEditorDialog open onOpenChange={vi.fn()} scene={scene}
      component={scene.components[0]} componentName="Cover Deco" /></AppProviders>)
    expect(screen.queryByLabelText('Use Custom Optical Values')).toBeNull()
    expect(screen.queryByLabelText('Custom Reflectance')).toBeNull()
    const materialDialog = screen.getByRole('dialog', { name: 'Material Assignment' })
    fireEvent.click(screen.getByRole('button', { name: 'Surface Property · 커스텀 편집' }))
    const editor = screen.getByRole('dialog', { name: 'Surface Property · 커스텀 편집' })
    expect(document.body.contains(materialDialog)).toBe(true)
    fireEvent.change(within(editor).getByLabelText('Custom Reflectance'), { target: { value: '0.6134567' } })
    fireEvent.change(within(editor).getByLabelText('Custom Specular / Gaussian ratio'), { target: { value: '0.35' } })
    expect(workspaceStore.getState().materialAssignments).toEqual([part, face])
    fireEvent.click(within(editor).getByRole('button', { name: 'Save' }))
    expect(screen.queryByRole('dialog', { name: 'Surface Property · 커스텀 편집' })).toBeNull()
    const preview = within(screen.getByLabelText('Compiled optical preview'))
    expect(preview.getByText('0.613')).not.toBeNull()
    expect(preview.getByText('0.35')).not.toBeNull()
    expect(preview.getByText('0.65')).not.toBeNull()
    expect(workspaceStore.getState().materialAssignments.find((item) => item.assignmentId === 'face')).toEqual(face)
    expect(payload().optical_profiles.find((item) => item.profile_id === 'compiled-material-part-1'))
      .toMatchObject({ reflectance: 0.6134567, specular_ratio: 0.35, diffuse_ratio: 0.65 })
    // The original Material draft must also be updated, so Apply cannot restore stale values.
    fireEvent.click(screen.getByRole('button', { name: 'Apply to part' }))
    expect(payload().optical_profiles.find((item) => item.profile_id === 'compiled-material-part-1')!.reflectance).toBe(0.6134567)
    fireEvent.click(screen.getByRole('button', { name: 'Surface Property · 커스텀 편집' }))
    expect(screen.getByLabelText('Custom Reflectance')).toHaveProperty('value', '0.6134567')
  })

  it('discards cancelled custom edits and blocks invalid Save', () => {
    workspaceStore.getState().actions.upsertMaterialAssignment(part)
    render(<AppProviders><MaterialEditorDialog open onOpenChange={vi.fn()} scene={scene}
      component={scene.components[0]} componentName="Cover Deco" /></AppProviders>)
    fireEvent.click(screen.getByRole('button', { name: 'Surface Property · 커스텀 편집' }))
    fireEvent.change(screen.getByLabelText('Custom Reflectance'), { target: { value: '0.9' } })
    fireEvent.change(screen.getByLabelText('Custom Gaussian sigma (deg)'), { target: { value: '0' } })
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
    fireEvent.keyDown(screen.getByLabelText('Custom Reflectance'), { key: 'Enter' })
    expect(workspaceStore.getState().materialAssignments).toEqual([part])
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(within(screen.getByLabelText('Compiled optical preview')).getByText('0.600')).not.toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Surface Property · 커스텀 편집' }))
    expect(screen.getByLabelText('Custom Reflectance')).toHaveProperty('value', '0.6')
    fireEvent.change(screen.getByLabelText('Custom Gaussian sigma (deg)'), { target: { value: '' } })
    fireEvent.keyDown(screen.getByLabelText('Custom Gaussian sigma (deg)'), { key: 'Enter' })
    expect(screen.getByRole('button', { name: 'Save' })).toHaveProperty('disabled', true)
    expect(workspaceStore.getState().materialAssignments).toEqual([part])
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Surface Property · 커스텀 편집' })).getByRole('button', { name: 'Close' }))
    expect(workspaceStore.getState().materialAssignments).toEqual([part])
  })

  it('allows saving custom optics for a new part using the current Material selection', () => {
    render(<AppProviders><MaterialEditorDialog open onOpenChange={vi.fn()} scene={scene}
      component={scene.components[0]} componentName="Cover Deco" /></AppProviders>)
    fireEvent.change(screen.getByLabelText('Base Material'), { target: { value: 'secc_bare' } })
    fireEvent.click(screen.getByRole('button', { name: 'Surface Property · 커스텀 편집' }))
    fireEvent.change(screen.getByLabelText('Custom Reflection model'), { target: { value: 'lambertian' } })
    fireEvent.change(screen.getByLabelText('Custom Reflectance'), { target: { value: '0.3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    expect(workspaceStore.getState().materialAssignments).toHaveLength(1)
    expect(workspaceStore.getState().materialAssignments[0]).toMatchObject({ baseMaterialId: 'secc_bare', targetType: 'part',
      opticalOverride: { reflectance: 0.3, scatterModel: 'lambertian' } })
    expect(within(screen.getByLabelText('Compiled optical preview')).getByText('0.300')).not.toBeNull()
  })
})
