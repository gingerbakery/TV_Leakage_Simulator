// @vitest-environment jsdom

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createWorkspaceStore } from '@/stores'
import { createSceneFixture } from '@/test/scene-fixture'

import { createBitsamProject, saveBitsamProject } from './bitsam-project'

function createProject() {
  const store = createWorkspaceStore()
  store.getState().actions.setActiveCad({
    path: 'save-model.step',
    displayName: 'save-model.step',
  })
  return createBitsamProject(createSceneFixture(), store.getState())
}

afterEach(() => {
  vi.unstubAllGlobals()
  delete (window as Window & { showSaveFilePicker?: unknown })
    .showSaveFilePicker
})

describe('BITSAM native save', () => {
  it('calls the Edge file picker with Window as its receiver', async () => {
    const write = vi.fn(async () => undefined)
    const close = vi.fn(async () => undefined)
    const picker = vi.fn(function (this: unknown) {
      if (this !== window) throw new TypeError('Illegal invocation')
      return Promise.resolve({
        createWritable: async () => ({ write, close }),
      })
    })
    Object.assign(window, { showSaveFilePicker: picker })

    await expect(saveBitsamProject(createProject())).resolves.toBe('picked')
    expect(picker).toHaveBeenCalledOnce()
    expect(write).toHaveBeenCalledOnce()
    expect(close).toHaveBeenCalledOnce()
  })

  it('falls back to Downloads when the exposed picker rejects writing', async () => {
    Object.assign(window, {
      showSaveFilePicker: vi.fn(async () => {
        throw new DOMException('blocked by policy', 'SecurityError')
      }),
    })
    vi.stubGlobal('URL', {
      ...URL,
      createObjectURL: vi.fn(() => 'blob:bitsam-project'),
      revokeObjectURL: vi.fn(),
    })
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => undefined)

    await expect(saveBitsamProject(createProject())).resolves.toBe(
      'fallback-downloaded',
    )
    expect(click).toHaveBeenCalledOnce()
  })
})
