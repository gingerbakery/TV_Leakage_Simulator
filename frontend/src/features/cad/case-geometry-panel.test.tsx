// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { AppProviders } from '@/app/providers'
import { createSceneFixture } from '@/test/scene-fixture'
import { workspaceStore } from '@/stores'
import { leakPreviewStore } from '@/features/leak-preview/leak-preview-store'
import { CaseGeometryPanel } from './case-geometry-panel'
import { createBlockGroup } from '@/features/leak-preview/blocker-groups'

afterEach(() => { cleanup(); workspaceStore.getState().actions.resetWorkspace(); leakPreviewStore.getState().clear() })
const panel = (disabled=false) => <AppProviders><CaseGeometryPanel scene={createSceneFixture()} disabled={disabled}><div>ACC CAD 목록</div></CaseGeometryPanel></AppProviders>

it('starts folded and preserves created blocks while closing or leaving the shared editor', () => {
  workspaceStore.getState().actions.addCadCase({ path:'main.step', displayName:'main' })
  const view = render(panel())
  expect(screen.queryByRole('button',{name:'Block 만들기'})).toBeNull()
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  fireEvent.click(screen.getByRole('button',{name:'Block 만들기'}))
  const id = leakPreviewStore.getState().blockerAreaSelectionId
  expect(id).toBeTruthy()
  act(() => leakPreviewStore.getState().appendBlockerRegion(id!, { widthMm:10 }))
  expect(workspaceStore.getState().roiBoxSelectionArmed).toBe(true)
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 접기'}))
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBeNull()
  expect(workspaceStore.getState().roiBoxSelectionArmed).toBe(false)
  expect(leakPreviewStore.getState().blockers.map(item=>item.id)).toContain(id)
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  fireEvent.click(screen.getByRole('button',{name:'Block 만들기'}))
  act(() => leakPreviewStore.getState().appendBlockerRegion(leakPreviewStore.getState().blockerAreaSelectionId!, { widthMm:12 }))
  view.unmount()
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBeNull()
  expect(leakPreviewStore.getState().blockers).toHaveLength(2)
})

it('prevents drawing while the model is being prepared', () => {
  workspaceStore.getState().actions.addCadCase({ path:'main.step', displayName:'main' })
  render(panel(true))
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  expect(screen.queryByRole('button',{name:'Block 만들기'})).toBeNull()
  expect(screen.getByText('형상 준비 또는 해석 중 · Block 편집 대기')).toBeTruthy()
})

it('unlinks ACC independently while preserving its files and the Block link', () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({path:'source.step',displayName:'source'})
  const sourceId = workspaceStore.getState().activeCadCaseId!
  actions.addAccessoryCad(sourceId,{path:'shared.step',displayName:'shared'})
  actions.addCadCase({path:'target.step',displayName:'target'})
  const targetId = workspaceStore.getState().activeCadCaseId!
  actions.setActiveCadCase(sourceId)
  actions.copyActiveSetupToCases([{caseId:targetId,componentIdMap:{}}],['accessoryCads','blockers'])
  actions.setActiveCadCase(targetId)
  render(panel())
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  fireEvent.click(screen.getByRole('button',{name:'ACC 연동 해제'}))
  const target = workspaceStore.getState().cadCases.find(item=>item.caseId===targetId)!
  expect(target.setupLinks?.accessoryCads).toBeUndefined()
  expect(target.setupLinks?.blockers).toBe(sourceId)
  expect(target.accessoryCads[0].cad.path).toBe('shared.step')
})

it('does not reset a different Case before the shell restores its Preview state', () => {
  const actions = workspaceStore.getState().actions
  actions.addCadCase({path:'first.step',displayName:'first'})
  leakPreviewStore.getState().ensureScene('first-scene')
  const oldBlock = createBlockGroup('old-case-block', 'box')
  leakPreviewStore.getState().addBlocker(oldBlock)
  leakPreviewStore.getState().beginBlockerAreaSelection(oldBlock.id)
  const oldIdentity = leakPreviewStore.getState().sceneIdentity
  actions.addCadCase({path:'second.step',displayName:'second'})
  const targetId = workspaceStore.getState().activeCadCaseId!
  const before = workspaceStore.getState().cadCases.find(item=>item.caseId===targetId)!.leakPreviewState
  render(panel())
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  expect(leakPreviewStore.getState().sceneIdentity).toBe(oldIdentity)
  expect(workspaceStore.getState().cadCases.find(item=>item.caseId===targetId)!.leakPreviewState).toEqual(before)
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBe(oldBlock.id)
  act(()=>leakPreviewStore.getState().restoreProjectState(before))
  expect(leakPreviewStore.getState().sceneIdentity).not.toBe(oldIdentity)
})

it('migrates Allowed Areas to Block groups with independent visibility and application', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'visibility.step', displayName: 'Visibility' })
  const store = leakPreviewStore.getState()
  store.beginIgnoreAreaSelection()
  store.addIgnoreAreaRegion({ plane: 'xy', xMin: 0, xMax: 1, yMin: 0, yMax: 1 })
  store.finishIgnoreAreaSelection()
  store.addBlocker({ id: 'block', label: 'Block', enabled: true, baseCenter: [0,0,0], uAxis: [1,0,0], vAxis: [0,1,0], normal: [0,0,1], widthMm: 1, heightMm: 1, offsetMm: 0, depthMm: 1, reverse: false })
  render(panel())
  fireEvent.click(screen.getByRole('button',{name:'공통 보완 형상 펼치기'}))
  fireEvent.click(screen.getByLabelText('Block 2 Hide'))
  fireEvent.click(screen.getByLabelText('Block Hide'))
  expect(leakPreviewStore.getState().blockers[1]).toMatchObject({ label: 'Block 2', enabled: true, visible: false })
  expect(leakPreviewStore.getState().ignoreAreas).toEqual([])
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: true, visible: false })
  fireEvent.click(screen.getByLabelText('Block 2 적용'))
  fireEvent.click(screen.getByLabelText('Block 적용'))
  expect(leakPreviewStore.getState().blockers[1]).toMatchObject({ enabled: false, visible: false })
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: false, visible: false })
  fireEvent.click(screen.getByLabelText('Block Show'))
  expect(leakPreviewStore.getState().blockers[0]).toMatchObject({ enabled: false, visible: true })
})
