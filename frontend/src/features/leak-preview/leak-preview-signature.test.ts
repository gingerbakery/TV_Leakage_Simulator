import { expect, it } from 'vitest'
import { previewSignature, samePreviewSignature } from './leak-preview-signature'

it('accepts legacy signatures with reordered nested properties', () => {
  const old = JSON.stringify({ observer: { spacing: 2, samples: 4 }, sceneToken: 'same' })
  const restored = previewSignature({ sceneToken: 'same', observer: { samples: 4, spacing: 2 } })
  expect(samePreviewSignature(old, restored)).toBe(true)
})

it('preserves real changes, array ordering, scene identity and malformed-input rejection', () => {
  const before = previewSignature({ sceneToken: 'one', spacing: 2, emitters: ['a', 'b'] })
  for (const changed of [
    { sceneToken: 'one', spacing: 3, emitters: ['a', 'b'] },
    { sceneToken: 'two', spacing: 2, emitters: ['a', 'b'] },
    { sceneToken: 'one', spacing: 2, emitters: ['b', 'a'] },
  ]) expect(samePreviewSignature(before, previewSignature(changed))).toBe(false)
  expect(samePreviewSignature('{broken', before)).toBe(false)
  expect(samePreviewSignature(null, before)).toBe(false)
})
