import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({ plugins: [vue()], base: '/admin/', server: {proxy: {'/foundation-health/conversation': {target: 'http://127.0.0.1:8101', rewrite: () => '/healthz'}, '/foundation-health/agent': {target: 'http://127.0.0.1:8102', rewrite: () => '/healthz'}, '/foundation-health/business': {target: 'http://127.0.0.1:8103', rewrite: () => '/healthz'}}} })
