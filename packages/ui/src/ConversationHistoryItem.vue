<script setup lang="ts">
import { ElDropdown, ElDropdownMenu, ElDropdownItem } from 'element-plus'
import 'element-plus/es/components/dropdown/style/css'
import UiIcon from './UiIcon.vue'
defineProps<{ item: { id: string; title: string }; selected: boolean }>()
const emit = defineEmits<{ open: []; edit: [mode: 'rename' | 'generate'] }>()
</script>
<template>
  <div class="conversation-history-item" :class="{ selected }">
    <button class="conversation-history-link" :title="item.title" :aria-current="selected ? 'page' : undefined" @click="emit('open')">{{ item.title }}</button>
    <ElDropdown trigger="click" placement="bottom-end" @command="emit('edit', $event)">
      <button class="conversation-history-more" :aria-label="'会话操作：' + item.title" title="会话操作"><UiIcon name="more" :size="20" /></button>
      <template #dropdown><ElDropdownMenu>
        <ElDropdownItem command="rename">重命名</ElDropdownItem>
        <ElDropdownItem command="generate">重新生成标题</ElDropdownItem>
      </ElDropdownMenu></template>
    </ElDropdown>
  </div>
</template>
