import { useRef, type ChangeEvent } from 'react'
import { Check, Eye, EyeOff, FolderOpen, LoaderCircle, Trash2 } from 'lucide-react'

import { useUploadCadMutation } from '@/api'
import { Button } from '@/components/ui/button'
import { useWorkspaceStore, workspaceSelectors } from '@/stores'

interface ModelImportCardProps {
  sceneStatus?: string
  onImported(): void
}

export function ModelImportCard({ sceneStatus, onImported }: ModelImportCardProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const cadCases = useWorkspaceStore(workspaceSelectors.cadCases)
  const activeCadCaseId = useWorkspaceStore(workspaceSelectors.activeCadCaseId)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const uploadMutation = useUploadCadMutation()

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return
    try {
      const uploaded = await uploadMutation.mutateAsync({ file, filename: file.name })
      actions.addCadCase({ path: uploaded.path, displayName: uploaded.display_name })
      onImported()
    } catch {
      // The shared API error is rendered below.
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-border bg-background/35 p-2">
        <div className="mb-2 flex items-center justify-between px-1">
          <span className="text-sm font-semibold tracking-wide text-muted-foreground uppercase">Import CAD List</span>
          <span className="text-xs text-muted-foreground">{cadCases.length} Cases</span>
        </div>
        {cadCases.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">No CAD Imported</div>
        ) : (
          <div className="space-y-1.5">
            {cadCases.map((item) => {
              const active = item.caseId === activeCadCaseId
              return (
                <div
                  key={item.caseId}
                  role="button"
                  tabIndex={0}
                  aria-label={`Activate CASE ${item.order}`}
                  className={`flex cursor-pointer items-center gap-2 rounded-lg border px-2.5 py-2 transition ${active ? 'border-primary/35 bg-primary/10' : 'border-border bg-background/50 hover:bg-muted/35'}`}
                  onClick={() => actions.setActiveCadCase(item.caseId)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault()
                      actions.setActiveCadCase(item.caseId)
                    }
                  }}
                >
                  <input
                    type="checkbox"
                    aria-label={`Show CASE ${item.order}`}
                    checked={item.visible}
                    onClick={(event) => event.stopPropagation()}
                    onChange={(event) => actions.setCadCaseVisible(item.caseId, event.currentTarget.checked)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5 text-sm font-semibold">
                      <span>CASE {String(item.order).padStart(2, '0')}</span>
                      {active ? <Check className="size-3 text-primary" /> : null}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">{item.cad.displayName}</div>
                  </div>
                  {item.visible ? <Eye className="size-3.5 text-primary" /> : <EyeOff className="size-3.5 text-muted-foreground" />}
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={`Delete CASE ${item.order}`}
                    title="Remove imported CAD case"
                    className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                    onClick={(event) => {
                      event.stopPropagation()
                      actions.removeCadCase(item.caseId)
                    }}
                  >
                    <Trash2 />
                  </Button>
                </div>
              )
            })}
          </div>
        )}
        <div className="mt-2 px-1 text-xs leading-4 text-muted-foreground">
          {activeCad ? `${sceneStatus ?? 'Loading'} · Step 03 follows the active Case.` : 'STEP (AP214/AP242) · STP · STL · OBJ'}
        </div>
      </div>
      <input ref={inputRef} type="file" className="sr-only" aria-label="Choose CAD file" accept=".step,.stp,.stl,.obj" onChange={handleFileChange} />
      <Button className="w-full" disabled={uploadMutation.isPending} onClick={() => inputRef.current?.click()}>
        {uploadMutation.isPending ? <LoaderCircle className="animate-spin" /> : <FolderOpen data-icon="inline-start" />}
        {uploadMutation.isPending ? 'Uploading CAD…' : 'Import CAD'}
      </Button>
      {uploadMutation.error ? <p className="text-xs leading-4 text-destructive">{uploadMutation.error.message}</p> : null}
    </div>
  )
}
