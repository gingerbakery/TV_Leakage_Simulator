import { useRef, type ChangeEvent } from 'react'
import { FolderOpen, LoaderCircle } from 'lucide-react'

import { useUploadCadMutation } from '@/api'
import { Button } from '@/components/ui/button'
import {
  useWorkspaceStore,
  workspaceSelectors,
} from '@/stores'

interface ModelImportCardProps {
  sceneStatus?: string
  onImported(): void
}

export function ModelImportCard({
  sceneStatus,
  onImported,
}: ModelImportCardProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const activeCad = useWorkspaceStore(workspaceSelectors.activeCad)
  const actions = useWorkspaceStore(workspaceSelectors.actions)
  const uploadMutation = useUploadCadMutation()

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.currentTarget.files?.[0]
    event.currentTarget.value = ''
    if (!file) return

    try {
      const uploaded = await uploadMutation.mutateAsync({
        file,
        filename: file.name,
      })
      actions.setActiveCad({
        path: uploaded.path,
        displayName: uploaded.display_name,
      })
      onImported()
    } catch {
      // The mutation error is rendered below with the shared API message.
    }
  }

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-dashed border-border bg-muted/20 px-3 py-3">
        <div className="truncate text-xs font-medium">
          {activeCad?.displayName ?? 'No CAD selected'}
        </div>
        <div className="mt-1 text-[0.68rem] leading-4 text-muted-foreground">
          {sceneStatus ?? 'STEP (AP214/AP242) · STP · STL · OBJ'}
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        className="sr-only"
        aria-label="Choose CAD file"
        accept=".step,.stp,.stl,.obj"
        onChange={handleFileChange}
      />
      <Button
        className="w-full"
        disabled={uploadMutation.isPending}
        onClick={() => inputRef.current?.click()}
      >
        {uploadMutation.isPending ? (
          <LoaderCircle className="animate-spin" />
        ) : (
          <FolderOpen data-icon="inline-start" />
        )}
        {uploadMutation.isPending ? 'Uploading CAD…' : 'Import CAD'}
      </Button>
      {uploadMutation.error ? (
        <p className="text-[0.68rem] leading-4 text-destructive">
          {uploadMutation.error.message}
        </p>
      ) : null}
    </div>
  )
}
