// @vitest-environment jsdom
import {afterEach,expect,it,vi} from 'vitest'
import {attachViewerActionShortcuts} from './viewer-shortcuts'

afterEach(() => document.body.replaceChildren())

function setup() {
  const canvas = document.createElement('canvas'); canvas.tabIndex = 0; document.body.append(canvas)
  const fit = vi.fn(), section = vi.fn(), prepare = vi.fn()
  const detach = attachViewerActionShortcuts(canvas,{fit,section},prepare)
  canvas.dispatchEvent(new MouseEvent('pointerenter'))
  const key = (code:string,options:KeyboardEventInit={}) => {
    const event = new KeyboardEvent('keydown',{code,key:code === 'KeyH' ? 'ㅗ' : 'ㄹ',cancelable:true,...options})
    window.dispatchEvent(event); return event
  }
  return {canvas,fit,section,prepare,detach,key}
}

it('handles physical F/H with a Korean IME, ignores repeat and cleans up handlers', () => {
  const {fit,section,prepare,key,detach} = setup()
  expect(key('KeyH').defaultPrevented).toBe(true)
  key('KeyH',{repeat:true}); key('KeyF')
  expect(section).toHaveBeenCalledOnce(); expect(fit).toHaveBeenCalledOnce(); expect(prepare).toHaveBeenCalledTimes(2)
  detach(); key('KeyF'); key('KeyH')
  expect(fit).toHaveBeenCalledOnce(); expect(section).toHaveBeenCalledOnce()
})

it.each(['range','checkbox','number'])('allows F/H after a %s workflow control is used', (type) => {
  const {fit,section,key,detach} = setup()
  const input = document.createElement('input'); input.type = type
  document.body.append(input); input.focus()
  key('KeyH'); key('KeyF')
  expect(section).toHaveBeenCalledOnce(); expect(fit).toHaveBeenCalledOnce()
  detach()
})

it('allows F/H after a workflow selector retains focus', () => {
  const {fit,section,key,detach} = setup()
  const select = document.createElement('select'); document.body.append(select); select.focus()
  key('KeyH'); key('KeyF')
  expect(section).toHaveBeenCalledOnce(); expect(fit).toHaveBeenCalledOnce()
  detach()
})

it('protects text entry and modal dialogs while supporting nonmodal editors', () => {
  const {fit,section,key,detach} = setup()
  const dialog = document.createElement('div'); dialog.setAttribute('role','dialog')
  const input = document.createElement('input'); dialog.append(input); document.body.append(dialog); input.focus()
  expect(key('KeyH').defaultPrevented).toBe(false)
  expect(section).not.toHaveBeenCalled()
  const button = document.createElement('button'); dialog.append(button); button.focus()
  key('KeyH'); expect(section).toHaveBeenCalledOnce()
  dialog.setAttribute('aria-modal','true'); key('KeyF'); expect(fit).not.toHaveBeenCalled()
  dialog.hidden = true; key('KeyF'); expect(fit).toHaveBeenCalledOnce()
  detach()
})

it('keeps shortcuts scoped to the viewer and preserves Ctrl+F and Alt shortcuts', () => {
  const {canvas,fit,section,key,detach} = setup()
  key('KeyF',{ctrlKey:true}); key('KeyH',{altKey:true})
  expect(fit).not.toHaveBeenCalled(); expect(section).not.toHaveBeenCalled()
  canvas.dispatchEvent(new MouseEvent('pointerleave'))
  key('KeyH'); expect(section).not.toHaveBeenCalled()
  canvas.focus(); key('KeyH'); expect(section).toHaveBeenCalledOnce()
  detach()
})
