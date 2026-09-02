import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue2'

const proxy = {
  '/api': 'http://localhost:8241',
  '/v1': 'http://localhost:8241',
  '/chat': 'http://localhost:8241',
  '/responses': 'http://localhost:8241'
}

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 8240,
    allowedHosts: ['www.yucg.cn', 'yucg.cn'],
    proxy
  },
  preview: {
    host: '0.0.0.0',
    port: 8240,
    allowedHosts: ['www.yucg.cn', 'yucg.cn'],
    proxy
  },
  build: { outDir: 'dist', sourcemap: false }
})
