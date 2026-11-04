import { cpSync, existsSync, mkdirSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * pdf.js 运行时需要的 cmap / 标准字体 / wasm / icc 资源。
 * 复制到 renderer 的 public/pdfjs：开发期由 dev server 直接提供，构建后随应用打包（离线可用）。
 * 在配置加载时同步完成，避免 dev server 扫描 public 目录时的竞态。
 */
export function ensurePdfjsAssets(): void {
  const src = resolve('node_modules/pdfjs-dist')
  const dst = resolve('src/renderer/public/pdfjs')
  for (const dir of ['cmaps', 'standard_fonts', 'wasm', 'iccs']) {
    const from = resolve(src, dir)
    const to = resolve(dst, dir)
    if (existsSync(from) && !existsSync(to)) {
      mkdirSync(to, { recursive: true })
      cpSync(from, to, { recursive: true })
    }
  }
}
