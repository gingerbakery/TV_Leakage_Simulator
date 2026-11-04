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
import { createBlockGroup } from './blocker-groups'

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear() })

it('creates named groups for both modes, appends repeated drags, edits names and persists regions', () => {
  const scene = createSceneFixture()
  workspaceStore.getState().actions.addCadCase({ path: 'groups.step', displayName: 'Groups' })
  render(<AppProviders><BlockerEditor scene={scene} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '홀 막기' }))
  expect(workspaceStore.getState().rayTraceConfig.apply_preview_blockers).toBe(true)
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
  expect(screen.getByRole('dialog').textContent).toContain('홀 중심 근처를 클릭')
  fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })
  expect(screen.queryByRole('dialog')).toBeNull()
})

it('keeps hole plugs in point mode and synchronizes group/region selections with the viewer state', () => {
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name:'홀 막기' }))
  const group = leakPreviewStore.getState().blockers[0]
  expect(workspaceStore.getState().roiBoxSelectionArmed).toBe(false)
  expect(group).toMatchObject({ depthMm:5, offsetMm:-2.5, normal:[0,0,1] })
  act(() => {
    leakPreviewStore.getState().appendBlockerRegion(group.id, { baseCenter:[1,2,3] })
    leakPreviewStore.getState().appendBlockerRegion(group.id, { baseCenter:[4,5,6] })
    leakPreviewStore.getState().finishBlockerAreaSelection()
  })
  expect(screen.queryByLabelText('Block 1 Edit')).toBeNull()
  expect(screen.queryByLabelText('Block 1 새 영역 깊이')).toBeNull()
  const regions = leakPreviewStore.getState().blockers[0].regions!
  fireEvent.click(screen.getByLabelText('Block 1 홀 1 선택'))
  expect(leakPreviewStore.getState().selectedBlocker).toEqual({ groupId:group.id, regionId:regions[0].id })
  act(() => leakPreviewStore.getState().selectBlocker(group.id, regions[1].id))
  expect(screen.getByLabelText('Block 1 홀 2 선택').getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('group', { name:'Block 1 목록' }).getAttribute('data-selected')).toBe('true')
  fireEvent.click(screen.getByLabelText('Block 1 홀 2 삭제'))
  expect(leakPreviewStore.getState().selectedBlocker).toEqual({ groupId:group.id, regionId:null })
  fireEvent.click(screen.getByLabelText('Block 1 Delete'))
  expect(leakPreviewStore.getState().selectedBlocker).toBeNull()
})

it('starts hole lists expanded, keeps collapsed rows compact and reveals a hole selected in the viewer', () => {
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByRole('button',{name:'홀 막기'}))
  const store = leakPreviewStore.getState(), groupId = store.blockers[0].id
  act(() => { store.appendBlockerRegion(groupId,{baseCenter:[1,2,3]}); store.finishBlockerAreaSelection() })
  const regionId = leakPreviewStore.getState().blockers[0].regions![0].id
  expect(screen.getByLabelText('Block 1 홀 목록 접기').getAttribute('aria-expanded')).toBe('true')
  fireEvent.click(screen.getByLabelText('Block 1 홀 1 선택'))
  fireEvent.click(screen.getByLabelText('Block 1 홀 목록 접기'))
  expect(screen.queryByLabelText('Block 1 홀 1 선택')).toBeNull()
  expect(screen.getByLabelText('Block 1 홀 목록 펼치기').getAttribute('aria-expanded')).toBe('false')
  act(() => leakPreviewStore.getState().selectBlocker(groupId,regionId))
  expect(screen.getByLabelText('Block 1 홀 1 선택').getAttribute('aria-pressed')).toBe('true')
  fireEvent.click(screen.getByLabelText('Block 1 홀 목록 접기'))
  fireEvent.click(screen.getByLabelText('Block 1 홀 목록 펼치기'))
  expect(screen.getByLabelText('Block 1 홀 1 선택')).not.toBeNull()
  expect(leakPreviewStore.getState().blockers[0].regions).toHaveLength(1)
})

it('applies only entered group dimensions to all blocks while retaining placement and other groups', () => {
  const box = createBlockGroup('Block 1','box'), other = createBlockGroup('Block 2','box')
  box.regions = [
    {...box,id:'a',regions:undefined,baseCenter:[1,2,3],widthMm:11,heightMm:12,enabled:false,visible:false},
    {...box,id:'b',regions:undefined,baseCenter:[4,5,6],widthMm:21,heightMm:22},
  ]
  act(() => { leakPreviewStore.getState().addBlocker(box); leakPreviewStore.getState().addBlocker(other) })
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByLabelText('Block 1 Edit'))
  fireEvent.change(screen.getByLabelText('Block 1 그룹 깊이'),{target:{value:'7'}})
  fireEvent.change(screen.getByLabelText('Block 1 그룹 Offset'),{target:{value:'-2'}})
  fireEvent.click(screen.getByLabelText('Block 1 그룹 치수 전체 적용'))
  const updated = leakPreviewStore.getState().blockers
  expect(updated[0]).toMatchObject({depthMm:7,offsetMm:-2})
  expect(updated[0].regions).toEqual(box.regions.map((region) => ({...region,depthMm:7,offsetMm:-2})))
  expect(updated[1]).toEqual(other)
  expect(screen.getByRole('status').textContent).toContain('2개 적용 완료')
  const constraints = buildPrecisionPreviewConstraints(updated,[],true,false)
  expect(constraints[0]).toMatchObject({width_mm:21,height_mm:22,depth_mm:7})
})

it('rejects invalid bulk dimensions without partially changing the group', () => {
  const box = createBlockGroup('Block 1','box')
  box.regions = [{...box,id:'a',regions:undefined}]
  act(() => leakPreviewStore.getState().addBlocker(box))
  render(<AppProviders><BlockerEditor scene={createSceneFixture()} disabled={false} onStart={() => undefined} /></AppProviders>)
  fireEvent.click(screen.getByLabelText('Block 1 Edit'))
  fireEvent.change(screen.getByLabelText('Block 1 그룹 폭'),{target:{value:'10'}})
  fireEvent.change(screen.getByLabelText('Block 1 그룹 깊이'),{target:{value:'-1'}})
  fireEvent.click(screen.getByLabelText('Block 1 그룹 치수 전체 적용'))
  expect(screen.getByRole('alert').textContent).toContain('0.01 mm')
  expect(leakPreviewStore.getState().blockers[0]).toEqual(box)
})
