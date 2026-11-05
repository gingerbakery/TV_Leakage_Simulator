// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
import { attachBlockerDrawingEscape } from './viewer-controls'

afterEach(() => { document.body.replaceChildren() })

it('exits Block drawing once and detaches without intercepting other modes', () => {
  let enabled=false
  const cancel=vi.fn()
  const detach=attachBlockerDrawingEscape(() => enabled,cancel)
  try {
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true}))
    expect(cancel).not.toHaveBeenCalled()
    enabled=true
    const escape=new KeyboardEvent('keydown',{key:'Escape',cancelable:true})
    window.dispatchEvent(escape)
    expect(escape.defaultPrevented).toBe(true)
    expect(cancel).toHaveBeenCalledOnce()
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',repeat:true}))
    expect(cancel).toHaveBeenCalledOnce()
  } finally { detach() }
  window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape'}))
  expect(cancel).toHaveBeenCalledOnce()
})

it('leaves input edits and open dialog Escape to their own controls', () => {
  const cancel=vi.fn(),detach=attachBlockerDrawingEscape(() => true,cancel)
  try {
    const input=document.createElement('input'); document.body.append(input); input.focus()
    input.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true,cancelable:true}))
    expect(cancel).not.toHaveBeenCalled()
    input.remove()
    const dialog=document.createElement('div'); dialog.setAttribute('role','dialog'); document.body.append(dialog)
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true}))
    expect(cancel).not.toHaveBeenCalled()
    dialog.remove()
    window.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',cancelable:true}))
    expect(cancel).toHaveBeenCalledOnce()
  } finally { detach() }
})
