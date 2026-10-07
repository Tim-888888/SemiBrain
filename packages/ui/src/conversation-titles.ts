export type ConversationTitle = {
  id: string; title: string; title_revision: number; title_source: string; title_status: string
}

// A late list/poll response must not undo a saved name or alter chat/run state.
export function mergeTitle<T extends Record<string, any>>(current: T, incoming: Partial<ConversationTitle>): T {
  if ((incoming.title_revision ?? 0) < (current.title_revision ?? 0)) return current
  const result = { ...current }
  for (const key of ['title', 'title_revision', 'title_source', 'title_status'] as const) {
    if (incoming[key] !== undefined) (result as any)[key] = incoming[key]
  }
  return result
}
