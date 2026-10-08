import { onMounted, onUnmounted } from 'vue'
import { invalidateReadCache } from './api'

// Keep ingestion status live; idle catalogs need only a slow freshness check.
export function useCatalogPolling(load: () => Promise<void>, active: () => boolean) {
  let timer: ReturnType<typeof setInterval>, pending = false, last = 0, mounted = false
  async function refresh(force = false) {
    if (!mounted || pending || document.visibilityState === 'hidden') return
    if (!force && !active() && Date.now() - last < 30_000) return
    pending = true
    try { await load() } finally { pending = false; last = Date.now() }
  }
  function visible() { if (document.visibilityState === 'visible') { invalidateReadCache(); void refresh(true) } }
  onMounted(() => { mounted = true; void refresh(true); timer = setInterval(() => void refresh(), 5000); document.addEventListener('visibilitychange', visible) })
  onUnmounted(() => { mounted = false; clearInterval(timer); document.removeEventListener('visibilitychange', visible) })
}
