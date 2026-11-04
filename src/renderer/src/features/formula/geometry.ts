import type { BBox } from '@/lib/types'

/** 若干矩形的外接矩形；没有矩形返回 null。 */
export function unionRects(rects: BBox[]): BBox | null {
  if (rects.length === 0) return null
  return [
    Math.min(...rects.map((r) => r[0])),
    Math.min(...rects.map((r) => r[1])),
    Math.max(...rects.map((r) => r[2])),
    Math.max(...rects.map((r) => r[3]))
  ]
}
