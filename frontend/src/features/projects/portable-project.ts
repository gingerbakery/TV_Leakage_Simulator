import { createHttpClient, type HttpClientOptions } from '@/api/http'
import { apiClient, type ScenePayload } from '@/api'
import type { ActiveCad } from '@/stores'
import {
  BitsamProjectError,
  bitsamDownloadFileName,
  compareBitsamProjectScene,
  parseBitsamProject,
  projectSchemaVersion,
  type BitsamProject,
} from './bitsam-project'

interface PackageExport {
  download_url: string
  filename: string
  size_bytes: number
  trace_cached: boolean
  case_count?: number
}

interface PackageCaseImport {
  project: unknown
  cad: ActiveCad
  trace_cached: boolean
}

interface PackageWorkspaceImportCase extends PackageCaseImport {
  case_id: string
  order: number
  name?: string
  note?: string
  visible: boolean
  accessory_cads?: PortableAccessoryCad[]
}

interface PackageWorkspaceImport {
  scope: 'workspace'
  workspace_name?: string
  active_case_id: string
  cases: PackageWorkspaceImportCase[]
}

type PackageImport = PackageCaseImport | PackageWorkspaceImport

export interface PortableWorkspaceSaveCase {
  caseId: string
  order: number
  name?: string
  note?: string
  visible: boolean
  cadPath: string
  accessoryCads: PortableAccessoryCad[]
  project: BitsamProject
}

export interface PortableAccessoryCad {
  accessoryId: string
  cad: ActiveCad
  visible: boolean
}

export interface PortableCaseRestore {
  project: BitsamProject
  scene: ScenePayload
  cad: ActiveCad
  traceCached: boolean
}

export interface PortableWorkspaceRestore {
  scope: 'workspace'
  workspaceName?: string
  activeCaseId: string
  cases: Array<Omit<PortableCaseRestore, 'scene'> & {
    caseId: string
    order: number
    name?: string
    note?: string
    visible: boolean
    accessoryCads: PortableAccessoryCad[]
    scene?: ScenePayload
  }>
}

export function createPortableProjectClient(options: HttpClientOptions = {}) {
  const http = createHttpClient(options)
  return {
    exportProject(sceneToken: string, project: BitsamProject) {
      return http.requestJson<PackageExport>('/api/projects/export', {
        method: 'POST', json: { scene_token: sceneToken, project: { ...project, schema_version: projectSchemaVersion(project.workspace, project.leak_preview) } },
      })
    },
    exportWorkspace(
      workspaceName: string,
      activeCaseId: string,
      cases: PortableWorkspaceSaveCase[],
    ) {
      return http.requestJson<PackageExport>('/api/projects/export-workspace', {
        method: 'POST',
        json: {
          workspace_name: workspaceName,
          active_case_id: activeCaseId,
          cases: cases.map((item) => ({
            case_id: item.caseId,
            order: item.order,
            name: item.name,
            note: item.note,
            visible: item.visible,
            cad_path: item.cadPath,
            accessory_paths: item.accessoryCads.map((accessory) => ({
              accessory_id: accessory.accessoryId,
              path: accessory.cad.path,
              display_name: accessory.cad.displayName,
              visible: accessory.visible,
            })),
            project: {
              ...item.project,
              schema_version: projectSchemaVersion(
                item.project.workspace,
                item.project.leak_preview,
              ),
            },
          })),
        },
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
type PortableProjectExportClient = Pick<
  ReturnType<typeof createPortableProjectClient>,
  'exportProject' | 'download'
>
type PortableWorkspaceExportClient = Pick<
  ReturnType<typeof createPortableProjectClient>,
  'exportWorkspace' | 'download'
>
type PortableProjectImportClient = Pick<
  ReturnType<typeof createPortableProjectClient>,
  'importProject'
>

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

export interface PackageSaveTarget {
  handle: PackageFileHandle | undefined | null
}

type PackagePickerWindow = Window & {
  showSaveFilePicker?: (options: {
    suggestedName: string
    types: Array<{ description: string; accept: Record<string, string[]> }>
  }) => Promise<PackageFileHandle>
}

function safeWorkspaceFileName(value: string) {
  const base = value
    .replace(/[<>:"/\\|?*]/g, '_')
    .replace(/[.\s]+$/g, '')
    .trim() || 'leakage-workspace'
  return base.toLowerCase().endsWith('.bitsam') ? base : `${base}.bitsam`
}

async function choosePackageFileHandle(
  suggestedName: string,
): Promise<PackageFileHandle | undefined | null> {
  const picker = (window as PackagePickerWindow).showSaveFilePicker
  if (!picker) return undefined
  try {
    return await picker.call(window, {
      suggestedName,
      types: [{
        description: 'BITSAM project with CAD',
        accept: { 'application/zip': ['.bitsam'] },
      }],
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return null
    if (
      error instanceof DOMException &&
      ['SecurityError', 'NotAllowedError'].includes(error.name)
    ) throw new BitsamProjectError('저장 위치 선택 창을 열 수 없습니다. 저장 버튼을 다시 누르거나 Chrome/Edge의 로컬 실행 화면에서 저장해 주세요.')
    throw error
  }
}

/** Call directly from the save gesture, before loading or packaging CAD. */
export async function chooseWorkspaceSaveTarget(workspaceName: string): Promise<PackageSaveTarget> {
  return { handle: await choosePackageFileHandle(safeWorkspaceFileName(workspaceName)) }
}

async function savePreparedPackage(
  prepared: PackageExport,
  handle: PackageFileHandle | undefined,
  onProgress: (message: string) => void,
  client: Pick<ReturnType<typeof createPortableProjectClient>, 'download'>,
) {
  if (!handle) {
    const anchor = document.createElement('a')
    anchor.href = `${baseUrl}${prepared.download_url}`
    anchor.download = prepared.filename
    document.body.append(anchor)
    anchor.click()
    anchor.remove()
    return { cancelled: false, downloaded: true }
  }
  let writable: PackageWritable | undefined
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  try {
    writable = await handle.createWritable()
    const response = await client.download(prepared.download_url)
    if (!response.body) {
      throw new BitsamProjectError('프로젝트 다운로드 스트림이 없습니다.')
    }
    reader = response.body.getReader()
    let written = 0
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      await writable.write(value)
      written += value.byteLength
      onProgress(
        `파일 저장 중 · ${Math.min(100, Math.round(written / prepared.size_bytes * 100))}%`,
      )
    }
    if (written !== prepared.size_bytes) {
      throw new BitsamProjectError(
        '파일 전송이 완료되지 않았습니다. 다시 저장해 주세요.',
      )
    }
    await writable.close()
  } catch (error) {
    await reader?.cancel().catch(() => undefined)
    await writable?.abort?.().catch(() => undefined)
    throw error
  } finally {
    reader?.releaseLock()
  }
  return { cancelled: false, downloaded: false }
}

export async function savePortableProject(
  project: BitsamProject,
  sceneToken: string,
  onProgress: (message: string) => void,
  client: PortableProjectExportClient = portableClient,
): Promise<{ cancelled: boolean; traceCached?: boolean; downloaded?: boolean }> {
  const handle = await choosePackageFileHandle(bitsamDownloadFileName(project))
  if (handle === null) return { cancelled: true }
  onProgress('원본 CAD · 형상 · 해석 결과를 묶는 중…')
  const prepared = await client.exportProject(sceneToken, project)
  const saved = await savePreparedPackage(prepared, handle, onProgress, client)
  return { ...saved, traceCached: prepared.trace_cached }
}

export async function savePortableWorkspace(
  workspaceName: string,
  activeCaseId: string,
  cases: PortableWorkspaceSaveCase[],
  onProgress: (message: string) => void,
  client: PortableWorkspaceExportClient = portableClient,
  destination?: PackageSaveTarget,
): Promise<{ cancelled: boolean; downloaded?: boolean; caseCount?: number }> {
  if (cases.length === 0) {
    throw new BitsamProjectError('저장할 Case가 없습니다.')
  }
  const handle = destination ? destination.handle : (await chooseWorkspaceSaveTarget(workspaceName)).handle
  if (handle === null) return { cancelled: true }
  onProgress(`전체 Workspace 패키징 중 · ${cases.length}개 Case`)
  const prepared = await client.exportWorkspace(
    workspaceName,
    activeCaseId,
    cases,
  )
  const saved = await savePreparedPackage(prepared, handle, onProgress, client)
  return {
    ...saved,
    caseCount: prepared.case_count ?? cases.length,
  }
}

function isWorkspaceImport(value: PackageImport): value is PackageWorkspaceImport {
  return (
    'scope' in value &&
    value.scope === 'workspace' &&
    Array.isArray(value.cases)
  )
}

export async function loadPortableBundle(
  file: File,
  onProgress: (message: string) => void,
  client: PortableProjectImportClient = portableClient,
  sceneClient: {
    getScene(
      path: string,
      options?: { signal?: AbortSignal },
      accessoryPaths?: string[],
    ): Promise<ScenePayload>
  } = apiClient,
): Promise<PortableCaseRestore | PortableWorkspaceRestore> {
  onProgress('프로젝트 업로드 · 무결성 검증 중…')
  const restored = await client.importProject(file)
  if (isWorkspaceImport(restored)) {
    const cases: PortableWorkspaceRestore['cases'] = []
    for (let index = 0; index < restored.cases.length; index += 1) {
      const item = restored.cases[index]
      onProgress(
        `Case 설정 확인 중 · ${index + 1}/${restored.cases.length}`,
      )
      const project = parseBitsamProject(JSON.stringify(item.project))
      const accessoryCads = item.accessory_cads ?? []
      let scene: ScenePayload | undefined
      if (item.case_id === restored.active_case_id) {
        onProgress('활성 Case 형상 복원 중 · CAD 재변환 없음')
        const accessoryPaths = accessoryCads
          .filter((accessory) => accessory.visible)
          .map((accessory) => accessory.cad.path)
        scene = await sceneClient.getScene(
          item.cad.path,
          undefined,
          accessoryPaths,
        )
        const compatibility = compareBitsamProjectScene(
          project,
          scene,
          item.cad,
        )
        if (!compatibility.compatible) {
          throw new BitsamProjectError(
            `${item.name || item.cad.displayName} 형상과 설정이 일치하지 않습니다. ${compatibility.reasons.join(' / ')}`,
          )
        }
      }
      cases.push({
        caseId: item.case_id,
        order: item.order,
        name: item.name,
        note: item.note,
        visible: item.visible,
        accessoryCads,
        project,
        ...(scene ? { scene } : {}),
        cad: item.cad,
        traceCached: item.trace_cached,
      })
    }
    return {
      scope: 'workspace',
      workspaceName: restored.workspace_name,
      activeCaseId: restored.active_case_id,
      cases,
    }
  }
  const project = parseBitsamProject(JSON.stringify(restored.project))
  onProgress('저장된 형상 복원 중 · CAD 재변환 없음')
  const scene = await sceneClient.getScene(restored.cad.path)
  const compatibility = compareBitsamProjectScene(project, scene, restored.cad)
  if (!compatibility.compatible) {
    throw new BitsamProjectError(`저장된 형상과 설정이 일치하지 않습니다. ${compatibility.reasons.join(' / ')}`)
  }
  return { project, scene, cad: restored.cad, traceCached: restored.trace_cached }
}

export async function loadPortableProject(
  file: File,
  onProgress: (message: string) => void,
  client: PortableProjectImportClient = portableClient,
  sceneClient: {
    getScene(
      path: string,
      options?: { signal?: AbortSignal },
      accessoryPaths?: string[],
    ): Promise<ScenePayload>
  } = apiClient,
): Promise<PortableCaseRestore> {
  const restored = await loadPortableBundle(
    file,
    onProgress,
    client,
    sceneClient,
  )
  if ('cases' in restored) {
    throw new BitsamProjectError(
      '전체 Workspace 파일입니다. Workspace 불러오기를 사용해 주세요.',
    )
  }
  return restored
}
