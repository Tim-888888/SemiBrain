<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import 'katex/dist/katex.min.css'
import { renderMarkdown } from './markdown.mjs'
import type { Artifact, Citation } from './api'
const props = defineProps<{ text: string; citations?: Citation[]; artifacts?: Artifact[]; streaming?: boolean }>()
const html = computed(() => DOMPurify.sanitize(renderMarkdown(props.text), {
  USE_PROFILES: { html: true, mathMl: true }, FORBID_TAGS: ['img', 'iframe', 'form', 'input', 'style'],
}))
async function copy() { await navigator.clipboard.writeText(props.text) }
</script>
<template>
  <div class="answer-wrap">
    <article class="markdown" :class="{ streaming }" v-html="html"></article>
    <div v-if="artifacts?.length" class="answer-artifacts" aria-label="分析产物">
      <figure v-for="artifact in artifacts" :key="artifact.asset_id"><img v-if="artifact.media_type === 'image/png'" :src="`/v1/assets/${artifact.asset_id}/content`" :alt="artifact.name" loading="lazy" /><figcaption><a :href="`/v1/assets/${artifact.asset_id}/content`" target="_blank" rel="noopener noreferrer">下载 {{ artifact.name }}</a></figcaption></figure>
    </div>
    <div v-if="citations?.length" class="citations" aria-label="引用来源">
      <template v-for="citation in citations" :key="citation.evidence_id">
        <a v-if="citation.asset_id" class="citation" :href="`/v1/assets/${citation.asset_id}/content`" target="_blank" rel="noopener noreferrer">▤ {{ citation.marker }} · {{ citation.title }}</a>
        <span v-else class="citation">▦ {{ citation.marker }} · {{ citation.title }}</span>
        <a v-if="typeof citation.location?.url === 'string' && /^https?:\/\//.test(citation.location.url)" class="citation" :href="citation.location.url" target="_blank" rel="noopener noreferrer">原网页 ↗</a>
      </template>
    </div>
    <button v-if="text && !streaming" class="text-button copy-answer" @click="copy" aria-label="复制回答">⧉ 复制</button>
  </div>
</template>
