// @vitest-environment jsdom

import { createRef } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { ProjectFileDialog, type ProjectFileNotice } from './project-file-dialog'

const saved: ProjectFileNotice = {
  operation: 'save',
  fileName: 'tv_leakage_roi_right_bottom_no_gap.bitsam',
  includesCad: true,
  includesResult: true,
  downloadStarted: false,
}

afterEach(cleanup)

function renderNotice(notice: ProjectFileNotice = saved) {
  const onClose = vi.fn()
  render(<ProjectFileDialog notice={notice} onClose={onClose} returnFocusRef={createRef()} />)
  return onClose
}

describe('ProjectFileDialog', () => {
  it('uses the same compact layout for loading and lists the restored items', () => {
    renderNotice({ ...saved, operation: 'load' })
    const dialog = screen.getByRole('dialog', { name: '불러오기 완료' })
    expect(dialog.className).toContain('sm:max-w-[26.25rem]')
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'CAD 모델 형상', '해석 설정 및 결과',
    ])
    expect(screen.getByText('불러온 항목').className).toContain('text-[11px]')
    expect(screen.queryByText('함께 저장된 항목')).toBeNull()
    expect(screen.getByRole('list').parentElement?.className).toContain('text-xs')
  })

  it('does not invent loaded results and preserves the first-trace preparation note', () => {
    renderNotice({ ...saved, operation: 'load', includesResult: false, notes: ['정밀 해석 형상은 첫 해석 시 준비합니다.'] })
    expect(screen.getByText('해석 설정')).not.toBeNull()
    expect(screen.queryByText('해석 설정 및 결과')).toBeNull()
    expect(screen.getByText('정밀 해석 형상은 첫 해석 시 준비합니다.')).not.toBeNull()
  })

  it('uses a compact themed layout with the combined small-text summary', () => {
    renderNotice()
    const dialog = screen.getByRole('dialog', { name: '저장 완료' })
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      '원본 CAD 및 모델 형상', '해석 설정 및 결과',
    ])
    expect(dialog.className).toContain('sm:max-w-[26.25rem]')
    expect(dialog.className).toContain('bg-popover')
    expect(dialog.className).not.toContain('simulator-popup-typography')
    expect(dialog.querySelector('[data-slot="dialog-header"]')?.className).not.toContain('-mx-')
    expect(screen.getByText(saved.fileName).className).toContain('[overflow-wrap:anywhere]')
    expect(screen.getByText('함께 저장된 항목').className).toContain('text-[11px]')
    expect(screen.getByRole('list').parentElement?.className).toContain('text-xs')
    expect(screen.queryByText('Close', { selector: 'button' })).toBeNull()
  })

  it('does not claim an analysis result was saved when there is none', () => {
    renderNotice({ ...saved, includesResult: false })
    expect(screen.getByText('해석 설정')).not.toBeNull()
    expect(screen.queryByText('해석 설정 및 결과')).toBeNull()
  })

  it('retains the separate CAD warning for legacy settings-only saves', () => {
    renderNotice({ ...saved, includesCad: false })
    expect(screen.queryByText('원본 CAD 및 모델 형상')).toBeNull()
    expect(screen.getByText('원본 CAD 파일은 별도로 보관해 주세요.')).not.toBeNull()
  })

  it('distinguishes download handoff from confirmed file writes', () => {
    renderNotice({ ...saved, downloadStarted: true })
    expect(screen.getByRole('dialog', { name: '다운로드 시작' })).not.toBeNull()
    expect(screen.queryByText('저장 완료')).toBeNull()
    expect(screen.getByText('브라우저에서 다운로드 완료를 확인해 주세요.')).not.toBeNull()
  })

  it('focuses the confirmation button and accepts click or Enter', () => {
    const onClose = renderNotice()
    const confirm = screen.getByRole('button', { name: '확인' })
    expect(document.activeElement).toBe(confirm)
    fireEvent.click(confirm)
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(confirm, { key: 'Enter', isComposing: true })
    expect(onClose).toHaveBeenCalledTimes(1)
    fireEvent.keyDown(confirm, { key: 'Enter' })
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('accepts Escape and restores focus after closing', async () => {
    const returnFocusRef = createRef<HTMLButtonElement>()
    const onClose = vi.fn()
    const view = render(<>
      <button ref={returnFocusRef}>Save</button>
      <ProjectFileDialog notice={saved} onClose={onClose} returnFocusRef={returnFocusRef} />
    </>)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalledTimes(1)
    view.rerender(<>
      <button ref={returnFocusRef}>Save</button>
      <ProjectFileDialog notice={null} onClose={onClose} returnFocusRef={returnFocusRef} />
    </>)
    await waitFor(() => expect(document.activeElement).toBe(returnFocusRef.current))
  })
})
