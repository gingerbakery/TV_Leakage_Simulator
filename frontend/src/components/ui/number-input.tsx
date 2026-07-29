import {
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type FocusEvent,
  type KeyboardEvent,
} from 'react'

type NumberInputProps = Omit<
  ComponentProps<'input'>,
  'defaultValue' | 'inputMode' | 'onChange' | 'type' | 'value'
> & {
  value: number
  onValueChange(value: number): void
}

const incompleteNumbers = new Set(['', '+', '-', '.', '+.', '-.'])

function formatNumber(value: number): string {
  return Number.isFinite(value) ? String(value) : '0'
}

function parseDraft(value: string): number | null {
  const trimmed = value.trim()
  if (incompleteNumbers.has(trimmed)) return null
  const parsed = Number(trimmed)
  return Number.isFinite(parsed) ? parsed : null
}

function NumberInput({
  value,
  onValueChange,
  min,
  max,
  step = 'any',
  onBlur,
  onFocus,
  onKeyDown,
  ...props
}: NumberInputProps) {
  const [draft, setDraft] = useState(() => formatNumber(value))
  const focusedRef = useRef(false)

  useEffect(() => {
    if (!focusedRef.current) {
      setDraft(formatNumber(value))
    }
  }, [value])

  const commitDraft = () => {
    const parsed = parseDraft(draft)
    const nextValue = parsed ?? 0
    onValueChange(nextValue)
    setDraft(formatNumber(nextValue))
  }

  const handleFocus = (event: FocusEvent<HTMLInputElement>) => {
    focusedRef.current = true
    if (value === 0) {
      setDraft('')
    } else {
      event.currentTarget.select()
    }
    onFocus?.(event)
  }

  const handleBlur = (event: FocusEvent<HTMLInputElement>) => {
    focusedRef.current = false
    commitDraft()
    onBlur?.(event)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      commitDraft()
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault()
      const parsed = parseDraft(draft) ?? value
      const increment = typeof step === 'number' ? step : 1
      const direction = event.key === 'ArrowUp' ? 1 : -1
      let nextValue = parsed + increment * direction
      if (typeof min === 'number') nextValue = Math.max(min, nextValue)
      if (typeof max === 'number') nextValue = Math.min(max, nextValue)
      onValueChange(nextValue)
      setDraft(formatNumber(nextValue))
    }
    onKeyDown?.(event)
  }

  return (
    <input
      {...props}
      type="text"
      role="spinbutton"
      inputMode="decimal"
      value={draft}
      aria-valuemin={typeof min === 'number' ? min : undefined}
      aria-valuemax={typeof max === 'number' ? max : undefined}
      aria-valuenow={parseDraft(draft) ?? undefined}
      onFocus={handleFocus}
      onBlur={handleBlur}
      onKeyDown={handleKeyDown}
      onChange={(event) => {
        const nextDraft = event.currentTarget.value
        setDraft(nextDraft)
        const parsed = parseDraft(nextDraft)
        if (parsed !== null) {
          onValueChange(parsed)
        }
      }}
    />
  )
}

export { NumberInput }
