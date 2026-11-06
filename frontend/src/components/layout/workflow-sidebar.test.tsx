// @vitest-environment jsdom
import { useState } from 'react'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { AppProviders } from '@/app/providers'
import { createSceneFixture } from '@/test/scene-fixture'
import { createRayTraceResultFixture } from '@/test/raytrace-fixture'
import { createEscapeEnclosureReceivers, detectLeakPreviewCandidates } from '@/features/leak-preview/leak-preview-model'
import { leakPreviewStore } from '@/features/leak-preview/leak-preview-store'
import { workspaceStore } from '@/stores'
import { WorkflowSidebar, type WorkflowSectionId } from './workflow-sidebar'

afterEach(() => {
  cleanup()
  workspaceStore.getState().actions.resetWorkspace()
  leakPreviewStore.getState().clear()
})

it('opens Whole Set Lighting for a viewer pick after navigating away or collapsing the active section', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'pick.step', displayName: 'Pick' })
  const scene = createSceneFixture()
  function Sidebar() {
    const [activeSection, setActiveSection] = useState<WorkflowSectionId>('leak-preview')
    return <WorkflowSidebar activeSection={activeSection} onActiveSectionChange={setActiveSection}
      isWide={false} onToggleWidth={() => undefined} scene={scene} cameraFrame={null}
      onOpenRayTraceResult={() => undefined} onEditMaterial={() => undefined}
      onEditTransform={() => undefined} onDeleteComponent={() => undefined} />
  }
  render(<AppProviders><Sidebar /></AppProviders>)
  const result = createRayTraceResultFixture()
  result.receivers = [createEscapeEnclosureReceivers(scene)[0]]
  result.receiver_grids = [{ receiver_id: result.receivers[0].receiver_id, resolution: [1, 1],
    bin_area_mm2: 1, flux_lumen: [[1]], hit_count: 1 }]
  result.stored_paths = []
  const detection = detectLeakPreviewCandidates(scene, result)
  act(() => leakPreviewStore.getState().setDetection(result, detection.points, detection.candidates))
  const id = detection.candidates[0].id
  fireEvent.click(screen.getByRole('button', { name: 'Step 02 ROI' }))
  expect(screen.getByRole('button', { name: 'Whole Set Lighting' }).getAttribute('aria-expanded')).toBe('false')
  act(() => leakPreviewStore.getState().selectCandidate(id, 'viewer'))
  expect(screen.getByRole('button', { name: 'Whole Set Lighting' }).getAttribute('aria-expanded')).toBe('true')
  expect(screen.getByRole('article', { name: 'Candidate 01' }).getAttribute('aria-current')).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'Whole Set Lighting' }))
  expect(screen.getByRole('button', { name: 'Whole Set Lighting' }).getAttribute('aria-expanded')).toBe('false')
  act(() => leakPreviewStore.getState().selectCandidate(id, 'viewer'))
  expect(screen.getByRole('article', { name: 'Candidate 01' }).getAttribute('aria-current')).toBe('true')
})

it('owns Block creation in Model Import and ends drawing when navigating to Preview', () => {
  workspaceStore.getState().actions.addCadCase({ path: 'blocks.step', displayName: 'blocks' })
  const scene = createSceneFixture()
  function Sidebar() {
    const [activeSection, setActiveSection] = useState<WorkflowSectionId>('model-import')
    return <WorkflowSidebar activeSection={activeSection} onActiveSectionChange={setActiveSection}
      isWide={false} onToggleWidth={() => undefined} scene={scene} cameraFrame={null}
      onOpenRayTraceResult={() => undefined} onEditMaterial={() => undefined}
      onEditTransform={() => undefined} onDeleteComponent={() => undefined} />
  }
  render(<AppProviders><Sidebar /></AppProviders>)
  fireEvent.click(screen.getByRole('button', { name: '공통 보완 형상 펼치기' }))
  fireEvent.click(screen.getByRole('button', { name: 'Block 만들기' }))
  const id = leakPreviewStore.getState().blockerAreaSelectionId!
  act(() => leakPreviewStore.getState().appendBlockerRegion(id, { widthMm: 15 }))
  fireEvent.click(screen.getByRole('button', { name: 'Whole Set Lighting' }))
  expect(screen.queryByRole('button', { name: 'Block 만들기' })).toBeNull()
  expect(leakPreviewStore.getState().blockerAreaSelectionId).toBeNull()
  expect(workspaceStore.getState().roiBoxSelectionArmed).toBe(false)
  expect(leakPreviewStore.getState().blockers[0].regions).toHaveLength(1)
})
