<template><section class="resource-panel"><div class="empty-card" role="alert"><strong>页面暂时未能加载</strong><p>网络中断或版本更新可能导致资源加载失败。请刷新页面重试。</p><button class="secondary" @click="reload">刷新页面</button></div></section></template>
<script setup lang="ts">function reload(){window.location.reload()}</script>
