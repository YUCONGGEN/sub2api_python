import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue2'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 8240,
    allowedHosts: ['www.yucg.cn', 'yucg.cn'],
    proxy: {
      '/api': 'http://localhost:8241',
      '/v1': 'http://localhost:8241',
      '/chat': 'http://localhost:8241',
      '/responses': 'http://localhost:8241'
    }
  },
  build: { outDir: 'dist', sourcemap: false }
})
