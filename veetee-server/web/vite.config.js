import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'node:path'

export default defineConfig({
  plugins: [vue()],
  base: '/static/',
  build: {
    outDir: resolve(__dirname, '../static'),
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8003',
      '/health': 'http://127.0.0.1:8003',
      '/ota': 'http://127.0.0.1:8003',
      '/ws': { target: 'ws://127.0.0.1:8003', ws: true },
    },
  },
})
