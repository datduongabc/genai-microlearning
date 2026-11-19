import type { StudyItemSummary } from "../api/studies";

// One row per RAG/no-RAG pair, which always shares its file, and one per human-written packet.
export function rowsOf(items: StudyItemSummary[]) {
  const rows = new Map<string, StudyItemSummary[]>();
  for (const item of items) {
    const key = item.pair_id ?? item.id;
    rows.set(key, [...(rows.get(key) ?? []), item]);
  }
  return [...rows.values()];
}
