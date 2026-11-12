// @vitest-environment jsdom
import { act, cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { PreviewRunStatus } from './preview-run-status'
afterEach(()=>{cleanup();vi.useRealTimers()})
it('reports unchanged progress, resets on sample advancement, and clears on completion', () => {
  vi.useFakeTimers()
  const view=render(<PreviewRunStatus busy validating={false} message="계산 중" phase="역추적 · 100 / 200 표본" progress={.5} />)
  act(()=>vi.advanceTimersByTime(61000))
  expect(screen.getByText('진행 갱신 없음 · 61초')).toBeTruthy()
  view.rerender(<PreviewRunStatus busy validating={false} message="계산 중" phase="역추적 · 101 / 200 표본" progress={.505} />)
  expect(screen.queryByText(/진행 갱신 없음/)).toBeNull()
  view.rerender(<PreviewRunStatus busy={false} validating={false} message="완료" progress={1} />)
  act(()=>vi.advanceTimersByTime(61000))
  expect(screen.queryByText(/진행 갱신 없음/)).toBeNull()
})
