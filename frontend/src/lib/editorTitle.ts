const MAX_TITLE_LENGTH = 60

// The page title of an editor named after its focus, cut so a long focus does not wrap the header;
// the full one goes in the tooltip.
export function editorTitle(kind: string, focus: string | null): { title: string; fullTitle?: string } {
  const full = focus ? `${kind}: ${focus}` : kind
  if (full.length <= MAX_TITLE_LENGTH) return { title: full }
  return { title: `${full.slice(0, MAX_TITLE_LENGTH - 1).trimEnd()}…`, fullTitle: full }
}
