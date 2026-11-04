/** 裁掉左右白边：由正文段落的位置框推出内容的左右边界（PDF 坐标）。 */
import type { TextItem } from '@/lib/api.text'

export interface Crop {
  left: number
  right: number
}

function percentile(sorted: number[], q: number): number {
  const i = Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))))
  return sorted[i]
}

/**
 * 取正文类段落（段落、图注、标题）位置框左右的 10%/90% 分位数，避免个别越界的段落（页眉、页码、整页大图）把边界撑开。
 * 样本太少、或算出来的内容宽度不到页宽的 40% 时返回 null（不裁剪）。
 */
export function contentBounds(items: TextItem[], pageWidth: number): Crop | null {
  const x0: number[] = []
  const x1: number[] = []
  for (const it of items) {
    if (!it.bbox || !(it.kind === 'paragraph' || it.kind === 'caption' || it.kind === 'heading')) continue
    if (it.text.length < 30) continue
    x0.push(it.bbox[0])
    x1.push(it.bbox[2])
  }
  if (x0.length < 8 || pageWidth <= 0) return null
  x0.sort((a, b) => a - b)
  x1.sort((a, b) => a - b)
  const pad = 6
  const left = Math.max(0, percentile(x0, 0.1) - pad)
  const right = Math.min(pageWidth, percentile(x1, 0.9) + pad)
  if (right - left < pageWidth * 0.4) return null
  // 本来就几乎没有白边：不值得裁
  if (left < pageWidth * 0.03 && right > pageWidth * 0.97) return null
  return { left, right }
}
