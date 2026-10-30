// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { AppProviders } from '@/app/providers'
import { workspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'
import { createBitsamProject, serializeBitsamProject, parseBitsamProject } from '@/features/projects/bitsam-project'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { buildLeakPreviewRequest } from './leak-preview-model'
import { buildPrecisionPreviewConstraints } from '@/features/raytracing/precision-preview-constraints'
import { BlockerEditor } from './blocker-editor'
import { leakPreviewStore, snapshotLeakPreviewProjectState } from './leak-preview-store'
import { expandBlockerGroups } from './blocker-groups'

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear() })

it('creates named groups for both modes, appends repeated drags, edits names and persists regions', () => {
  const scene = createSceneFixture()
  workspaceStore.getState().actions.addCadCase({ path: 'groups.step', displayName: 'Groups' })
  render(<AppProviders><BlockerEditor scene={scene} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '홀 막기' }))
  const id = leakPreviewStore.getState().blockers[0].id
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBe(id)
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ label: 'Block 1', shape: 'ellipse', regions: [] })
  act(() => {
    leakPreviewStore.getState().appendBlockerRegion(id, { baseCenter: [10, 20, 3], widthMm: 5, heightMm: 5 })
    leakPreviewStore.getState().appendBlockerRegion(id, { baseCenter: [30, 20, 3], widthMm: 6, heightMm: 4 })
  })
  expect(leakPreviewStore.getState().blockers[0].regions).toHaveLength(2)
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBe(id)
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  fireEvent.change(screen.getByLabelText('Block 1 name'), { target: { value: 'Screw holes' } })
  fireEvent.click(screen.getByRole('button', { name: 'Block 만들기' }))
  const boxId = leakPreviewStore.getState().blockerAreaSelectionId!
  expect(leakPreviewStore.getState().blockers[1].label).toBe('Block 2')
  act(() => leakPreviewStore.getState().appendBlockerRegion(boxId, { widthMm: 10, heightMm: 20 }))
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  const groups = leakPreviewStore.getState().blockers
  const preview = buildLeakPreviewRequest({ scene, sourceFaceIds: [0], blockers: groups, quality: 'fast', computeBackend: 'cpu',
    materialAssignments: [], transformRules: [], excludedComponentIds: [], deletedComponentIds: [] })
  expect(preview.preview_blockers).toHaveLength(3)
  expect(preview.preview_blockers?.map((item) => item.shape)).toEqual(['ellipse', 'ellipse', 'box'])
  const precision = buildPrecisionPreviewConstraints(groups, [], true, false)
  expect(precision.map((item) => item.shape)).toEqual(['ellipse', 'ellipse', 'box'])
  const project = createBitsamProject(scene, workspaceStore.getState(), new Date(), createRayTraceResultFixture(), snapshotLeakPreviewProjectState())
  const loaded = parseBitsamProject(serializeBitsamProject(project))
  expect(loaded.leak_preview?.blockers).toEqual(JSON.parse(JSON.stringify(groups)))
  const damaged = JSON.parse(serializeBitsamProject(project))
  damaged.leak_preview.blockers[0].regions[0].widthMm = 'invalid'
  expect(() => parseBitsamProject(JSON.stringify(damaged))).toThrow()

  const firstRegionId = groups[0].regions![0].id
  act(() => leakPreviewStore.getState().removeBlockerRegion(id, firstRegionId))
  expect(expandBlockerGroups(leakPreviewStore.getState().blockers)).toHaveLength(2)
  fireEvent.click(screen.getByLabelText('Screw holes Hide'))
  expect(expandBlockerGroups(leakPreviewStore.getState().blockers)[0]).toMatchObject({ enabled: true, visible: false })
  fireEvent.click(screen.getByLabelText('Screw holes 적용'))
  expect(buildPrecisionPreviewConstraints(leakPreviewStore.getState().blockers, [], true, false)).toHaveLength(1)
})

it('drops an empty draft and reuses a group for later drags without changing existing regions', () => {
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: 'Block 만들기' }))
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  expect(leakPreviewStore.getState().blockers).toEqual([])
  fireEvent.click(screen.getByRole('button', { name: 'Block 만들기' }))
  const id = leakPreviewStore.getState().blockers[0].id
  act(() => leakPreviewStore.getState().appendBlockerRegion(id, { widthMm: 15 }))
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  fireEvent.click(screen.getByLabelText('Block 1 영역 추가'))
  act(() => leakPreviewStore.getState().appendBlockerRegion(id, { widthMm: 25 }))
  expect(leakPreviewStore.getState().blockers[0].regions?.map((item) => item.widthMm)).toEqual([15, 25])
  fireEvent.click(screen.getByRole('button', { name: '선택 완료' }))
  fireEvent.click(screen.getByRole('button', { name: '홀 막기' }))
  expect(leakPreviewStore.getState().blockers[1].label).toBe('Block 2')
})

it('keeps supplementary help closed and opens it by click or keyboard', () => {
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  expect(screen.queryByRole('dialog')).toBeNull()
  const help = screen.getByRole('button', { name: 'Blocker 도움말' })
  fireEvent.click(help)
  expect(screen.getByRole('dialog').textContent).toContain('CAD 홀 윤곽을 자동 인식하지 않으므로')
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
})
