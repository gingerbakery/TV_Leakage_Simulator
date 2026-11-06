// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { CollapsibleSection } from './collapsible-section'

afterEach(cleanup)
it('folds a list without unmounting or losing edited fields', () => {
  render(<CollapsibleSection title="검사 목록" count={12}><input aria-label="보존할 값" defaultValue="원래 값" /></CollapsibleSection>)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: '편집한 값' } })
  fireEvent.click(screen.getByRole('button', { name: '검사 목록 접기' }))
  expect(screen.queryByRole('textbox')).toBeNull()
  expect(screen.getByRole('button', { name: '검사 목록 펼치기' }).getAttribute('aria-expanded')).toBe('false')
  fireEvent.click(screen.getByRole('button', { name: '검사 목록 펼치기' }))
  expect((screen.getByRole('textbox') as HTMLInputElement).value).toBe('편집한 값')
})
it('supports a compact initially folded list', () => {
  render(<CollapsibleSection title="긴 목록" count={100} defaultOpen={false}><button>목록 항목</button></CollapsibleSection>)
  expect(screen.queryByRole('button', { name: '목록 항목' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: '긴 목록 펼치기' }))
  expect(screen.getByRole('button', { name: '목록 항목' })).toBeTruthy()
})
