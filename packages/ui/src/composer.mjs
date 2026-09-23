export const IMAGE_TYPES = ['image/png', 'image/jpeg', 'image/webp']
export const MAX_IMAGE_BYTES = 3 * 1024 ** 2

export function clipboardImages(data) {
  if (!data) return []
  const files = Array.from(data.items || [])
    .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
    .map(item => item.getAsFile()).filter(Boolean)
  return files.length ? files : Array.from(data.files || []).filter(file => file.type.startsWith('image/'))
}

export function imageError(file) {
  if (!IMAGE_TYPES.includes(file.type)) return '请选择 PNG、JPEG 或 WebP 图片。'
  if (!file.size || file.size > MAX_IMAGE_BYTES) return '图片不能为空，且不能超过 3 MB。'
  return ''
}

// Follow user intent, not content height: new SSE content must not look like a manual scroll.
export class ScrollFollow {
  following = true
  lastTop = 0
  constructor(element, changed = () => {}) { this.element = element; this.changed = changed }
  set(value) { this.following = value; this.changed(value) }
  reset() { this.lastTop = 0; this.set(true) }
  pause() { this.set(false) }
  resume() { this.set(true) }
  scrolled() {
    const area = this.element(); if (!area) return
    const delta = area.scrollTop - this.lastTop
    if (delta < -1) this.pause()
    else if (delta > 1 && area.scrollHeight - area.clientHeight - area.scrollTop <= 48) this.resume()
    this.lastTop = area.scrollTop
  }
  sync() {
    const area = this.element(); if (!area || !this.following) return
    // Instant following avoids queued smooth animations pulling a user back down.
    area.scrollTop = Math.max(0, area.scrollHeight - area.clientHeight)
    this.lastTop = area.scrollTop
  }
}
