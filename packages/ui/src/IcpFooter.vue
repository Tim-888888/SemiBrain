<template>
  <footer class="icp-footer">
    <a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer">粤ICP备2026149841号-1</a>
  </footer>
</template>

<style scoped>
.icp-footer { margin-top: 16px; text-align: center; font-size: 11px; line-height: 1.6; }
.icp-footer a { color: var(--muted, #718096); white-space: nowrap; }
.icp-footer a:hover { color: var(--accent); text-decoration: underline; }
:global(.is-collapsed) .icp-footer { display: none; }
</style>
