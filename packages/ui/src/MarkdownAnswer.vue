<script setup lang="ts">
import { computed, ref } from 'vue'
import DOMPurify from 'dompurify'
import 'katex/dist/katex.min.css'
import { renderMarkdown } from './markdown.mjs'
import type { Artifact, Citation, DocumentImage } from './api'
const props = defineProps<{ text: string; citations?: Citation[]; artifacts?: Artifact[]; imageRefs?: DocumentImage[]; streaming?: boolean }>()
const expanded = ref<{ url: string; alt: string } | null>(null)
const html = computed(() => DOMPurify.sanitize(renderMarkdown(props.text, props.imageRefs), {
  USE_PROFILES: { html: true, mathMl: true }, FORBID_TAGS: ['iframe', 'form', 'input', 'style'],
}))
function openImage(event: Event) {
  const target = event.target
  if (target instanceof HTMLImageElement && target.closest('.inline-image')) expanded.value = { url: target.src, alt: target.alt }
}
function imageFailed(event: Event) {
  const target = event.target
  if (target instanceof HTMLImageElement) {
    target.hidden = true
    const fallback = target.nextElementSibling
    if (fallback instanceof HTMLElement) fallback.hidden = false
  }
}
async function copy() { await navigator.clipboard.writeText(props.text) }
</script>
<template>
  <div class="answer-wrap">
    <article class="markdown" :class="{ streaming }" @click="openImage" @keydown.enter="openImage" @error.capture="imageFailed" v-html="html"></article>
    <div v-if="expanded" class="modal-backdrop image-lightbox" role="dialog" aria-label="查看配图" aria-modal="true" @click.self="expanded = null" @keydown.esc="expanded = null"><button class="secondary" autofocus @click="expanded = null">关闭图片</button><img :src="expanded.url" :alt="expanded.alt" /></div>
    <div v-if="artifacts?.length" class="answer-artifacts" aria-label="分析产物">
      <figure v-for="artifact in artifacts" :key="artifact.asset_id"><img v-if="artifact.media_type === 'image/png'" :src="`/v1/assets/${artifact.asset_id}/content`" :alt="artifact.name" loading="lazy" /><figcaption><a :href="`/v1/assets/${artifact.asset_id}/content`" target="_blank" rel="noopener noreferrer">下载 {{ artifact.name }}</a></figcaption></figure>
    </div>
    <div v-if="citations?.length" class="citations" aria-label="引用来源">
      <template v-for="citation in citations" :key="citation.evidence_id">
        <span v-if="citation.body_expired" class="citation">▤ {{ citation.marker }} · {{ citation.title }} · 原快照已过期</span>
        <a v-else-if="citation.asset_id" class="citation" :href="`/v1/assets/${citation.asset_id}/content`" target="_blank" rel="noopener noreferrer">▤ {{ citation.marker }} · {{ citation.title }}</a>
        <span v-else class="citation">▦ {{ citation.marker }} · {{ citation.title }}</span>
        <a v-if="typeof citation.location?.url === 'string' && /^https?:\/\//.test(citation.location.url)" class="citation" :href="citation.location.url" target="_blank" rel="noopener noreferrer">原网页 ↗</a>
      </template>
    </div>
    <button v-if="text && !streaming" class="text-button copy-answer" @click="copy" aria-label="复制回答">⧉ 复制</button>
  </div>
</template>
<style scoped>
:deep(.inline-image) { display: block; margin: 1rem 0; }
:deep(.inline-image img) { display: block; max-width: 100%; max-height: 580px; width: auto; height: auto; margin: auto; border-radius: 8px; cursor: zoom-in; }
:deep(.inline-image img[hidden]), :deep(.image-fallback[hidden]) { display: none; }
:deep(.image-fallback) { padding: 1rem; border: 1px dashed #b8cabe; color: #617c6b; }
.image-lightbox { flex-direction: column; gap: 1rem; z-index: 100; }
.image-lightbox img { max-width: 94vw; max-height: 84vh; object-fit: contain; background: white; }
</style>
