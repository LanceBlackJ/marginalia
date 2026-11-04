import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'node:path'
import { ensurePdfjsAssets } from './config/pdfjsAssets'

/**
 * Web 调试模式：不启动 Electron，只在普通浏览器里运行渲染进程。
 * 打开 http://127.0.0.1:5173/?backend=http://127.0.0.1:<端口>&token=<令牌>，
 * 文件选择退化为输入路径的对话框，API Key 直接推送到后端内存。
 */
ensurePdfjsAssets()

export default defineConfig({
  root: resolve('src/renderer'),
  plugins: [react()],
  resolve: {
    alias: {
      '@': resolve('src/renderer/src'),
      '@shared': resolve('src/shared')
    }
  },
  server: { host: '127.0.0.1', port: 5173, strictPort: true }
})
