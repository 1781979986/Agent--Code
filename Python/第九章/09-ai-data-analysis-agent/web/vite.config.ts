import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  root: fileURLToPath(new URL('.', import.meta.url)),
  // 不读取任何环境文件；两端端口只来自用户已经导出的进程变量。
  envDir: false,
  plugins: [vue()],
  build: { outDir: '../dist/web', emptyOutDir: true },
  server: { host: '127.0.0.1', port: Number(process.env.WEB_PORT || 5187), strictPort: true,
    proxy: { '/api': `http://127.0.0.1:${process.env.PORT || 4317}`,
      '/voice': { target: `ws://127.0.0.1:${process.env.PORT || 4317}`, ws: true } } }
})
