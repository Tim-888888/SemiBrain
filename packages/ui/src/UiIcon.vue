<script setup lang="ts">
withDefaults(defineProps<{ name: string; size?: number }>(), { size: 20 })
const paths: Record<string, string> = {
  knowledge:'M4 4h16v16H4z M8 4v16 M11 8h6 M11 12h6 M11 16h4',
  chat:'M4 4h16v12H9l-5 4z M8 8h8 M8 12h5',
  wiki:'M12 5C8 3 5 3 3 4v15c3-1 6-1 9 1 3-2 6-2 9-1V4c-2-1-5-1-9 1z M12 5v15',
  graph:'M6 7l12 0 M6 7l6 11 M18 7l-6 11 M6 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M18 4a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M12 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6',
  'agent-config':'M6 5v14 M12 5v14 M18 5v14 M3 8h6 M9 15h6 M15 10h6',
  skills:'M13 2L4 14h7l-1 8 10-13h-7z',
  mcp:'M8 3v5 M16 3v5 M5 8h14v3a7 7 0 0 1-14 0z M12 18v4',
  evaluations:'M4 20h17 M6 16v-5 M12 16V5 M18 16V8',
  comparison:'M3 4h7v16H3z M14 4h7v16h-7z M5 8h3 M16 8h3 M5 12h3 M16 12h3',
  operations:'M3 12h4l3-8 4 16 3-8h4',
  users:'M9 3a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M2 21v-3a7 7 0 0 1 14 0v3 M17 4a4 4 0 0 1 0 8 M19 16a5 5 0 0 1 3 5',
  memory:'M5 4h14v16H5z M8 8h8 M8 12h8 M8 16h5',
  menu:'M4 6h16 M4 12h16 M4 18h16',
  collapse:'M4 4h16v16H4z M9 4v16 M16 9l-3 3 3 3',
  plus:'M12 5v14 M5 12h14',
  arrow:'M5 12h14 M13 6l6 6-6 6',
  search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
}
</script>
<template><svg :width="size" :height="size" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="paths[name] || paths.knowledge" /></svg></template>
