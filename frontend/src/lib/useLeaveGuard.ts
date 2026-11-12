import { useEffect } from 'react'

export const LEAVE_PROMPT = 'Các thay đổi chưa lưu sẽ mất. Rời trang?'

// The router in use is not a data router, so useBlocker is unavailable: a closed tab or reload is
// caught here, and an in-page back link or button asks with confirmLeave itself.
export function useLeaveGuard(unsaved: boolean) {
  useEffect(() => {
    if (!unsaved) return
    const warn = (event: BeforeUnloadEvent) => event.preventDefault()
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [unsaved])
}

export function confirmLeave(unsaved: boolean): boolean {
  return !unsaved || window.confirm(LEAVE_PROMPT)
}
