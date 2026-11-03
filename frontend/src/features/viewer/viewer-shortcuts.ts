export function viewerShortcutAllowed(canvas:HTMLCanvasElement, hovered:boolean, functionKey:boolean):boolean {
  if (!hovered && document.activeElement !== canvas) return false
  for (const dialog of document.querySelectorAll('[role="dialog"][aria-modal="true"]')) {
    if (!dialog.closest('[hidden],[aria-hidden="true"]') && !dialog.contains(canvas)) return false
  }
  // Function keys do not enter characters. Let them navigate even when a
  // workflow input retains focus; F/H must still respect actual text entry.
  if (functionKey) return true
  const active = document.activeElement
  if (active?.closest('textarea,[contenteditable="true"],[role="textbox"]')) return false
  return !(active instanceof HTMLInputElement && !['button','checkbox','radio','range','number','submit','reset','color','file'].includes(active.type) &&
    active.getAttribute('role') !== 'spinbutton' && active.inputMode !== 'decimal')
}

export function attachViewerActionShortcuts(canvas:HTMLCanvasElement, actions:{ fit():void; section():void }, beforeAction?:()=>void):()=>void {
  let hovered = false
  const enter = () => { hovered = true }
  const leave = () => { hovered = false }
  const keydown = (event:KeyboardEvent) => {
    if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey) return
    const key = event.code || event.key.toUpperCase()
    const action = key === 'KeyF' || key === 'F' ? actions.fit : key === 'KeyH' || key === 'H' ? actions.section : null
    if (!action || !viewerShortcutAllowed(canvas,hovered,false)) return
    event.preventDefault()
    if (event.repeat) return
    beforeAction?.()
    action()
  }
  canvas.addEventListener('pointerenter',enter)
  canvas.addEventListener('pointerleave',leave)
  window.addEventListener('keydown',keydown)
  return () => {
    canvas.removeEventListener('pointerenter',enter)
    canvas.removeEventListener('pointerleave',leave)
    window.removeEventListener('keydown',keydown)
  }
}
