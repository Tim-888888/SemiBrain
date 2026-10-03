<script setup lang="ts">
import UiIcon from './UiIcon.vue'
import { conversationStarters, type ConversationStarter } from './conversation-starters'
defineProps<{ multiAvailable: boolean; disabled: boolean }>()
defineEmits<{ select: [starter: ConversationStarter] }>()
</script>

<template>
  <div class="welcome feature-welcome">
    <span class="welcome-symbol">✳</span>
    <p class="eyebrow">SEMI BRAIN / KNOWLEDGE ASSISTANT</p>
    <h1>从一个工程问题开始</h1>
    <p>查资料、追证据、做分析，把结论变成可交付的成果。</p>
    <div class="feature-grid" aria-label="快速开始">
      <button v-for="starter in conversationStarters" :key="starter.id" type="button"
        class="feature-card" :disabled="disabled || (starter.multiAgent && !multiAvailable)"
        :aria-label="starter.title" @click="$emit('select', starter)">
        <span class="feature-heading"><span class="feature-icon"><UiIcon :name="starter.icon" /></span><strong>{{ starter.title }}</strong><UiIcon class="feature-arrow" name="arrow" :size="16" /></span>
        <span class="feature-description">{{ starter.description }}</span>
        <span class="feature-tags"><span v-for="tag in starter.tags" :key="tag">{{ tag }}</span></span>
        <small v-if="starter.multiAgent && !multiAvailable" class="feature-unavailable">当前未启用多 Agent</small>
      </button>
    </div>
    <p class="feature-hint">点击填入问题与推荐设置，确认后发送；问题和设置都可以修改。</p>
  </div>
</template>

<style scoped>
.feature-welcome{max-width:880px;width:100%;margin:20px auto 0;padding:0 30px 15px}
.feature-welcome .welcome-symbol{font-size:28px;line-height:1.2;margin-bottom:8px}.feature-welcome h1{font-size:26px;margin:10px 0 6px}.feature-welcome>.eyebrow{margin:8px 0 0}.feature-welcome>p:not(.eyebrow){font-size:13px}
.feature-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:18px;text-align:left}
.feature-card{display:flex;flex-direction:column;gap:7px;min-width:0;padding:12px 16px;border:1px solid var(--border);border-radius:12px;background:#fcfdfc;text-align:left;transition:background .15s,border-color .15s,box-shadow .15s}
.feature-card:hover:not(:disabled){background:#f4faf7;border-color:#a6cabb;box-shadow:0 3px 12px #21594109}.feature-card:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.feature-heading{display:flex;gap:10px;align-items:center}.feature-heading strong{font-size:14px;font-weight:600;color:#294a40}.feature-icon{display:grid;place-items:center;width:30px;height:30px;border-radius:8px;background:#edf5f0;color:var(--accent);flex-shrink:0}.feature-arrow{margin-left:auto;color:#88a397;flex-shrink:0}
.feature-description{font-size:12px;color:#76877f;line-height:1.6}.feature-tags{display:flex;flex-wrap:wrap;gap:6px}.feature-tags>span{font-size:10px;line-height:1.5;border:1px solid #e4ece7;border-radius:5px;padding:2px 7px;color:#6b8277;background:white}
.feature-welcome .feature-hint{font-size:11px;color:#8b9b92;margin:16px 0 0}.feature-unavailable{font-size:11px;color:#7c877e}
@media(max-width:650px){.feature-welcome{padding:0 18px 15px;margin-top:18px}.feature-welcome h1{font-size:24px}.feature-grid{grid-template-columns:1fr;gap:9px;margin-top:18px}.feature-card{padding:13px 15px;gap:7px}.feature-welcome .welcome-symbol{font-size:28px}}
</style>
