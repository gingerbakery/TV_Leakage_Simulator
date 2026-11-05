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
  /** Rounds the displayed/committed value to this many decimal places
   * (e.g. 1 for coordinate fields). Leaves full precision untouched while
   * the user is actively typing - only applied to what's shown when the
   * field isn't focused and to the value committed on blur/Enter/arrow. */
  decimals?: number
  /** Keep edits local until blur/Enter; empty drafts and Escape retain value. */
  commitOnBlur?: boolean
}

const incompleteNumbers = new Set(['', '+', '-', '.', '+.', '-.'])

function roundToDecimals(value: number, decimals?: number): number {
  if (decimals === undefined || !Number.isFinite(value)) return value
  const factor = 10 ** decimals
  return Math.round(value * factor) / factor
}

function formatNumber(value: number, decimals?: number): string {
  if (!Number.isFinite(value)) return '0'
  return decimals === undefined
    ? String(value)
    : roundToDecimals(value, decimals).toFixed(decimals)
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
  decimals,
  commitOnBlur = false,
  onBlur,
  onFocus,
  onKeyDown,
  ...props
}: NumberInputProps) {
  const [draft, setDraft] = useState(() => formatNumber(value, decimals))
  const focusedRef = useRef(false)

  useEffect(() => {
    if (!focusedRef.current) {
      setDraft(formatNumber(value, decimals))
    }
  }, [value, decimals])

  const commitDraft = () => {
    const parsed = parseDraft(draft)
    let nextValue = roundToDecimals(parsed ?? (commitOnBlur ? value : 0), decimals)
    if (commitOnBlur) {
      if (typeof min === 'number') nextValue = Math.max(min, nextValue)
      if (typeof max === 'number') nextValue = Math.min(max, nextValue)
    }
    if (!commitOnBlur || nextValue !== value) onValueChange(nextValue)
    setDraft(formatNumber(nextValue, decimals))
  }

  const handleFocus = (event: FocusEvent<HTMLInputElement>) => {
    focusedRef.current = true
    onFocus?.(event)
  }

  const handleBlur = (event: FocusEvent<HTMLInputElement>) => {
    focusedRef.current = false
    commitDraft()
    onBlur?.(event)
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      commitDraft()
    } else if (event.key === 'Escape' && commitOnBlur) {
      event.preventDefault()
      event.stopPropagation()
      setDraft(formatNumber(value, decimals))
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault()
      const parsed = parseDraft(draft) ?? value
      const increment = typeof step === 'number' ? step : 1
      const direction = event.key === 'ArrowUp' ? 1 : -1
      let nextValue = parsed + increment * direction
      if (typeof min === 'number') nextValue = Math.max(min, nextValue)
      if (typeof max === 'number') nextValue = Math.min(max, nextValue)
      nextValue = roundToDecimals(nextValue, decimals)
      if (!commitOnBlur) onValueChange(nextValue)
      setDraft(formatNumber(nextValue, decimals))
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
        if (parsed !== null && !commitOnBlur) {
          onValueChange(parsed)
        }
      }}
    />
  )
}

export { NumberInput }
