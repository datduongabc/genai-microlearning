import { useCallback, useEffect, useRef } from 'react'
import { useBlocker, type BlockerFunction } from 'react-router-dom'

const LEAVE_PROMPT = 'Các thay đổi chưa lưu sẽ mất. Rời trang?'

// The pages holding unsaved work right now. Logging out is not a navigation the blocker can
// stop: it clears the session, and the redirect to the login page comes after, when the page
// is already gone. So the logout button asks here first (confirmLeavingUnsavedWork).
const unsavedPages = new Set<symbol>()

// Asks before unsaved edits are lost however the page is left: a link or the sidebar, the
// browser's Back and Forward buttons (useBlocker, which needs the data router of main.tsx),
// a closed tab or a reload (beforeunload). Returns `allowLeave`, for a page that leaves on
// purpose once its edits no longer matter, such as after deleting what was being edited.
export function useLeaveGuard(unsaved: boolean): () => void {
  const allowed = useRef(false)

  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    const page = Symbol('unsaved page')
    unsavedPages.add(page)
    return () => {
      window.removeEventListener('beforeunload', warn)
      unsavedPages.delete(page)
    }
  }, [unsaved])

  // Only a move to another page: a change of the query string or hash keeps the edits on screen.
  const shouldBlock = useCallback<BlockerFunction>(
    ({ currentLocation, nextLocation }) =>
      unsaved && !allowed.current && currentLocation.pathname !== nextLocation.pathname,
    [unsaved],
  )
  const blocker = useBlocker(shouldBlock)

  useEffect(() => {
    if (blocker.state !== 'blocked') return
    if (window.confirm(LEAVE_PROMPT)) blocker.proceed()
    else blocker.reset()
  }, [blocker])

  return useCallback(() => {
    allowed.current = true
  }, [])
}

// For an action on the page that throws the edits away without leaving it (reloading the draft).
export function confirmLeave(unsaved: boolean): boolean {
  return !unsaved || window.confirm(LEAVE_PROMPT)
}

// For leaving by other means than a navigation (logging out): true when nothing is unsaved, or
// the lecturer agrees to lose it.
export function confirmLeavingUnsavedWork(): boolean {
  return confirmLeave(unsavedPages.size > 0)
}
