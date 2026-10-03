import { createApp } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'
import Workspace from '@semibrain/ui'
import { adminPages } from '@semibrain/ui/admin-navigation'
import { confirmNavigation } from '@semibrain/ui/editor-state'
const router = createRouter({ history: createWebHashHistory('/admin/'), routes: [
  { path: '/', redirect: '/knowledge' },
  ...adminPages.map(page => ({ path: '/' + page.id, name: page.id, component: { render: () => null } })),
  { path: '/:pathMatch(.*)*', redirect: '/knowledge' },
] })
router.beforeEach(() => confirmNavigation())
const app = createApp(Workspace, { audience: 'admin' })
app.use(router)
router.isReady().then(() => app.mount('#app'))
