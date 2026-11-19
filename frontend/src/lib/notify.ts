import { toast } from 'sonner'

// Every result of an action or a job is a toast, in one corner of the screen; only a field's own
// mistake or a page that could not load is written on the page. A failure stays until closed,
// since it often needs reading twice and the lecturer may be looking elsewhere when a job ends.
export function notifyDone(text: string) {
  toast.success(text)
}

export function notifyFailed(text: string, id?: string) {
  toast.error(text, { duration: Infinity, id })
}
