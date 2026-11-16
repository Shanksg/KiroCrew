import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'

/** How long a row the reader was sent to (a `link` jump) keeps its landing
 *  highlight: the length of the `msg-landed` animation in `tailwind-theme.css`. */
export const LANDED_HIGHLIGHT_MS = 5000
/** How long a jump made inside the chat (a pin, a quote) keeps its flash. */
export const FLASH_HIGHLIGHT_MS = 3000

/** Marks the row a jump landed on. `landed` picks the landing highlight over the flash. */
export type MarkJump = (ts: string, landed: boolean) => void

/**
 * The one owner of a jump's highlight and its expiry. Each mark replaces the
 * last: it clears the other kind, and it cancels the previous expiry, so an
 * older jump's timer can never clear a newer jump's target (and with it the
 * fold reveal and row scroll that target drives).
 */
export function useJumpMark(
  setHighlightTs: Dispatch<SetStateAction<string | null>>,
  setLandedTs: Dispatch<SetStateAction<string | null>>,
): { markJump: MarkJump; jumpSeq: number } {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  // Counts every mark, so a repeat jump to the row already marked (a second click
  // on the same pin) still reruns the effects keyed on the target.
  const [jumpSeq, setJumpSeq] = useState(0)
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current) }, [])
  const markJump = useCallback((ts: string, landed: boolean) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    setJumpSeq(n => n + 1)
    const mark = landed ? setLandedTs : setHighlightTs
    if (landed) setHighlightTs(null)
    else setLandedTs(null)
    mark(ts)
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      mark(null)
    }, landed ? LANDED_HIGHLIGHT_MS : FLASH_HIGHLIGHT_MS)
  }, [setHighlightTs, setLandedTs])
  return { markJump, jumpSeq }
}
