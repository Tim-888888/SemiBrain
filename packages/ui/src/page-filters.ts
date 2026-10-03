import { inject, watch, type Ref } from 'vue'

/** Per-account, per-tab preferences; never store drafts, credentials or API data. */
export function rememberFilters(page: string, fields: Record<string, Ref<string>>) {
  const account = inject<Ref<string> | undefined>('filter-account', undefined)
  if (!account?.value) return
  const key = `semibrain:filters:${account.value}:${page}`
  try {
    const saved = JSON.parse(sessionStorage.getItem(key) || '{}')
    for (const [name, field] of Object.entries(fields)) {
      if (typeof saved[name] === 'string' && saved[name].length <= 500) field.value = saved[name]
    }
  } catch { /* Storage can be unavailable in private browsing. */ }
  watch(Object.values(fields), () => {
    try { sessionStorage.setItem(key, JSON.stringify(Object.fromEntries(Object.entries(fields).map(([name, field]) => [name, field.value])))) } catch {}
  })
}
