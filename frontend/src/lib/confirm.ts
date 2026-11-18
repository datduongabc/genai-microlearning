export interface ConfirmOptions {
  title: string;
  description?: string;
  // The verb on the button, e.g. "Xóa": the button says what will happen.
  action: string;
  destructive?: boolean;
}
export type ConfirmRequest = ConfirmOptions & { resolve: (ok: boolean) => void };

let show: ((request: ConfirmRequest) => void) | null = null;

// ConfirmHost registers itself here while it is mounted.
export function setConfirmHost(host: typeof show) { show = host; }

// Asks before an action that cannot be undone. Without a mounted ConfirmHost (a component rendered
// on its own) it falls back to the browser's dialog, so the question is never skipped.
export function confirmAction(options: ConfirmOptions): Promise<boolean> {
  if (!show) return Promise.resolve(window.confirm([options.title, options.description].filter(Boolean).join("\n\n")));
  const open = show;
  return new Promise((resolve) => open({ ...options, resolve }));
}
