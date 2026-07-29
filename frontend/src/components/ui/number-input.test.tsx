// @vitest-environment jsdom

import { useState } from 'react'
import {
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import { NumberInput } from './number-input'

afterEach(cleanup)

function NumberInputHarness({
  initialValue = 0,
}: {
  initialValue?: number
}) {
  const [value, setValue] = useState(initialValue)
  return (
    <>
      <NumberInput
        aria-label="Numeric value"
        value={value}
        onValueChange={setValue}
      />
      <output aria-label="Committed value">{value}</output>
    </>
  )
}

describe('NumberInput', () => {
  it('clears zero on focus and accepts negative decimals', () => {
    render(<NumberInputHarness />)
    const input = screen.getByRole('spinbutton', {
      name: 'Numeric value',
    }) as HTMLInputElement

    expect(input.value).toBe('0')
    fireEvent.focus(input)
    expect(input.value).toBe('')

    fireEvent.change(input, { target: { value: '-' } })
    expect(input.value).toBe('-')
    expect(screen.getByLabelText('Committed value').textContent).toBe('0')

    fireEvent.change(input, { target: { value: '-0.25' } })
    expect(input.value).toBe('-0.25')
    expect(screen.getByLabelText('Committed value').textContent).toBe(
      '-0.25',
    )
  })

  it('converts an empty draft to zero on blur', () => {
    render(<NumberInputHarness initialValue={12} />)
    const input = screen.getByRole('spinbutton', {
      name: 'Numeric value',
    }) as HTMLInputElement

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '' } })
    expect(input.value).toBe('')

    fireEvent.blur(input)
    expect(input.value).toBe('0')
    expect(screen.getByLabelText('Committed value').textContent).toBe('0')
  })

  it('converts an unfinished sign to zero on Enter', () => {
    render(<NumberInputHarness />)
    const input = screen.getByRole('spinbutton', {
      name: 'Numeric value',
    }) as HTMLInputElement

    fireEvent.focus(input)
    fireEvent.change(input, { target: { value: '-' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    expect(input.value).toBe('0')
    expect(screen.getByLabelText('Committed value').textContent).toBe('0')
  })
})
