<template>
  <a
    class="github-profile-link"
    href="https://github.com/Tim-888888"
    target="_blank"
    rel="noopener noreferrer"
    aria-label="访问作者 GitHub 主页（在新标签页打开）"
    title="访问作者 GitHub 主页 · Tim-888888"
  >
    <svg class="github-mark" width="18" height="18" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 .75a11.25 11.25 0 0 0-3.558 21.922c.563.104.768-.244.768-.542 0-.267-.01-.975-.015-1.914-3.13.68-3.79-1.508-3.79-1.508-.512-1.3-1.25-1.646-1.25-1.646-1.023-.7.078-.686.078-.686 1.132.08 1.728 1.162 1.728 1.162 1.005 1.722 2.638 1.225 3.28.937.103-.729.394-1.225.716-1.507-2.5-.285-5.128-1.25-5.128-5.563 0-1.229.439-2.233 1.159-3.021-.116-.285-.502-1.43.11-2.98 0 0 .944-.303 3.094 1.154A10.788 10.788 0 0 1 12 6.18c.956.005 1.919.13 2.818.379 2.148-1.457 3.09-1.155 3.09-1.155.615 1.551.229 2.696.113 2.981.721.788 1.158 1.792 1.158 3.021 0 4.324-2.632 5.275-5.14 5.553.405.349.766 1.034.766 2.084 0 1.504-.014 2.718-.014 3.087 0 .3.202.65.774.54A11.25 11.25 0 0 0 12 .75Z" />
    </svg>
    <span>GitHub</span>
    <svg class="github-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M7 17 17 7M7 7h10v10" /></svg>
  </a>
</template>

<style scoped>
.github-profile-link {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 36px;
  padding: 7px 12px;
  border: 1px solid #35464b;
  border-radius: 9px;
  background: linear-gradient(160deg, #34464b 0%, #202e33 100%);
  box-shadow: inset 0 1px 0 #ffffff1c, 0 2px 4px #172b3420;
  color: #fff;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
  letter-spacing: .2px;
  text-decoration: none;
  white-space: nowrap;
  transition: background .18s, border-color .18s, box-shadow .18s, transform .18s;
}
.github-profile-link:hover {
  border-color: #54756f;
  background: linear-gradient(160deg, #3b5557 0%, #243a3b 100%);
  box-shadow: inset 0 1px 0 #ffffff24, 0 4px 10px #172b3426;
  color: #fff;
  transform: translateY(-1px);
}
.github-profile-link:active { transform: translateY(0); box-shadow: inset 0 1px 3px #00000026; }
.github-profile-link:focus-visible { outline: 3px solid #15796780; outline-offset: 3px; }
.github-mark, .github-arrow { flex-shrink: 0; }
.github-arrow { opacity: .65; }
@media (pointer: coarse) { .github-profile-link { min-height: 44px; } }
@media (prefers-reduced-motion: reduce) {
  .github-profile-link { transition: none; }
  .github-profile-link:hover, .github-profile-link:active { transform: none; }
}
</style>
