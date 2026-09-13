import { createHttpClient, type HttpClientOptions } from '@/api/http'
import { apiClient, type ScenePayload } from '@/api'
import type { ActiveCad } from '@/stores'
import {
  BitsamProjectError,
  bitsamDownloadFileName,
  compareBitsamProjectScene,
  parseBitsamProject,
  type BitsamProject,
} from './bitsam-project'

interface PackageExport {
  download_url: string
  filename: string
  size_bytes: number
  trace_cached: boolean
}

interface PackageImport {
  project: unknown
  cad: ActiveCad
  trace_cached: boolean
}

export function createPortableProjectClient(options: HttpClientOptions = {}) {
  const http = createHttpClient(options)
  return {
    exportProject(sceneToken: string, project: BitsamProject) {
      return http.requestJson<PackageExport>('/api/projects/export', {
        method: 'POST', json: { scene_token: sceneToken, project },
      })
    },
    importProject(file: File) {
      return http.requestJson<PackageImport>('/api/projects/import', {
        method: 'POST', body: file,
        headers: { 'Content-Type': 'application/vnd.bitsam+zip' },
      })
    },
    download(path: string) {
      return http.requestResponse(path)
    },
  }
}

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
const portableClient = createPortableProjectClient({ baseUrl })

export async function isPortableProject(file: File): Promise<boolean> {
  if (!file.name.toLowerCase().endsWith('.bitsam')) {
    throw new BitsamProjectError('.bitsam 파일을 선택해 주세요.')
  }
  const header = new Uint8Array(await file.slice(0, 4).arrayBuffer())
  return header.length === 4 && header[0] === 0x50 && header[1] === 0x4b && header[2] === 3 && header[3] === 4
}

interface PackageWritable {
  write(data: Uint8Array): Promise<void>
  close(): Promise<void>
  abort?(): Promise<void>
}

interface PackageFileHandle {
  createWritable(): Promise<PackageWritable>
}

type PackagePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string
    types: Array<{ description: string; accept: Record<string, string[]> }>
  }) => Promise<PackageFileHandle>
}

export async function savePortableProject(
  project: BitsamProject,
  sceneToken: string,
  onProgress: (message: string) => void,
  client = portableClient,
): Promise<{ cancelled: boolean; traceCached?: boolean; downloaded?: boolean }> {
  const picker = (window as PackagePickerWindow).showSaveFilePicker
  let handle: PackageFileHandle | undefined
  if (picker) {
    try {
      handle = await picker.call(window, {
        suggestedName: bitsamDownloadFileName(project),
        types: [{ description: 'BITSAM project with CAD', accept: { 'application/zip': ['.bitsam'] } }],
      })
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return { cancelled: true }
      if (!(error instanceof DOMException && ['SecurityError', 'NotAllowedError'].includes(error.name))) throw error
    }
  }
  onProgress('원본 CAD · 형상 · 해석 결과를 묶는 중…')
  const prepared = await client.exportProject(sceneToken, project)
  if (!handle) {
    const anchor = document.createElement('a')
    anchor.href = `${baseUrl}${prepared.download_url}`
    anchor.download = prepared.filename
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    return { cancelled: false, traceCached: prepared.trace_cached, downloaded: true }
  }
  let writable: PackageWritable | undefined
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    writable = await handle.createWritable()
    const response = await client.download(prepared.download_url)
    if (!response.body) throw new BitsamProjectError('프로젝트 다운로드 스트림이 없습니다.')
    reader = response.body.getReader()
    let written = 0
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      await writable.write(value)
      written += value.byteLength
      onProgress(`파일 저장 중 · ${Math.min(100, Math.round(written / prepared.size_bytes * 100))}%`)
    }
    if (written !== prepared.size_bytes) throw new BitsamProjectError('파일 전송이 완료되지 않았습니다. 다시 저장해 주세요.')
    await writable.close()
  } catch (error) {
    await reader?.cancel().catch(() => undefined)
    await writable?.abort?.().catch(() => undefined)
    throw error
  } finally {
    reader?.releaseLock()
  }
  return { cancelled: false, traceCached: prepared.trace_cached, downloaded: false }
}

export async function loadPortableProject(
  file: File,
  onProgress: (message: string) => void,
  client = portableClient,
  sceneClient: { getScene(path: string): Promise<ScenePayload> } = apiClient,
) {
  onProgress('프로젝트 업로드 · 무결성 검증 중…')
  const restored = await client.importProject(file)
  const project = parseBitsamProject(JSON.stringify(restored.project))
  onProgress('저장된 형상 복원 중 · CAD 재변환 없음')
  const scene = await sceneClient.getScene(restored.cad.path)
  const compatibility = compareBitsamProjectScene(project, scene, restored.cad)
  if (!compatibility.compatible) {
    throw new BitsamProjectError(`저장된 형상과 설정이 일치하지 않습니다. ${compatibility.reasons.join(' / ')}`)
  }
  return { project, scene, cad: restored.cad, traceCached: restored.trace_cached }
}
