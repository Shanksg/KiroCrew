import { Suspense, lazy, useEffect } from 'react'
import { setMemoryProviderPreviewOff } from '../app-sdk/memoryProviderPreview'
import { useMemoryProviderPreviewSetting } from '../pages/settings/memoryProviderPreviewSetting'

// The sheets load on demand: the preview is off by default, so the first load never
// carries them.
const MemoryProviderSheetLayer = lazy(() => import('./MemoryProviderSheetLayer'))

/** The sheets an app's memory verbs open, mounted only while this gateway runs the
 *  memory provider preview. Off, nothing mounts and the verbs answer `'not_enabled'`. */
export default function MemoryProviderSheetHosts() {
  const preview = useMemoryProviderPreviewSetting()
  const active = !!preview.setting?.active
  const off = preview.settled && !active
  useEffect(() => {
    setMemoryProviderPreviewOff(off)
  }, [off])
  if (!active) return null
  return (
    <Suspense fallback={null}>
      <MemoryProviderSheetLayer />
    </Suspense>
  )
}
