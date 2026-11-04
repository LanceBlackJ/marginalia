import { memo, useEffect, useRef, useState, type CSSProperties } from 'react'
import { pdfjs, type PDFDocumentProxy } from './pdfjs'
import type { Annotation, BBox, HighlightColor } from '@/lib/types'
import type { PageSize } from './layout'
import { PageSmartLayer } from './PageSmartLayer'

export interface SearchOverlay {
  bbox: BBox
  active: boolean
}

interface PageViewProps {
  doc: PDFDocumentProxy
  pageNumber: number
  size: PageSize
  scale: number
  annotations: Annotation[]
  searchHits: SearchOverlay[]
  flash: { key: number; bbox: BBox } | null
  /** 聚光灯照亮的区域在本页的位置（可以有多块：跨栏的段落、整章）：这些地方保持原样，页面其余部分变暗；没有就不传 */
  spots?: BBox[] | null
  /** false = 不显示公式/图表角标（精读页里没有打开阅读器，那些数据不属于这篇论文） */
  smart?: boolean
}

export const HIGHLIGHT_COLORS: Record<HighlightColor, string> = {
  yellow: 'var(--hl-yellow)',
  blue: 'var(--hl-blue)',
  green: 'var(--hl-green)',
  pink: 'var(--hl-pink)'
}

function boxStyle(b: BBox, scale: number): CSSProperties {
  return { left: b[0] * scale, top: b[1] * scale, width: (b[2] - b[0]) * scale, height: (b[3] - b[1]) * scale }
}

/** 把互相重叠或紧贴的矩形并起来：聚光灯用 evenodd 挖洞，重叠的地方不能被挖两次。 */
export function unionRects(rects: BBox[]): BBox[] {
  const out = rects.map((r) => [...r] as BBox)
  let merged = true
  while (merged) {
    merged = false
    for (let i = 0; i < out.length && !merged; i++) {
      for (let j = i + 1; j < out.length; j++) {
        const a = out[i]
        const b = out[j]
        if (a[0] <= b[2] + 1 && b[0] <= a[2] + 1 && a[1] <= b[3] + 1 && b[1] <= a[3] + 1) {
          out[i] = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])]
          out.splice(j, 1)
          merged = true
          break
        }
      }
    }
  }
  return out
}

/** 聚光灯：整页盖一层暗色，只把 rects 挖空（保持原亮度）并描边。 */
function Spotlight({ rects, width, height, scale }: { rects: BBox[]; width: number; height: number; scale: number }) {
  const holes = unionRects(rects)
  const d = `M0 0H${width}V${height}H0Z ` + holes.map((b) => `M${b[0] * scale} ${b[1] * scale}H${b[2] * scale}V${b[3] * scale}H${b[0] * scale}Z`).join(' ')
  return (
    <>
      <div className="pd-spot-dim" style={{ clipPath: `path(evenodd, "${d}")` }} />
      {holes.map((b, i) => (
        <div key={i} className="pd-parahit" style={boxStyle(b, scale)} />
      ))}
    </>
  )
}

function isCancel(err: unknown): boolean {
  return typeof err === 'object' && err !== null && 'name' in err && (err as { name: string }).name === 'RenderingCancelledException'
}

export const PageView = memo(function PageView({ doc, pageNumber, size, scale, annotations, searchHits, flash, spots, smart }: PageViewProps) {
  const canvasHost = useRef<HTMLDivElement>(null)
  const textHost = useRef<HTMLDivElement>(null)
  const [renderScale, setRenderScale] = useState(scale)
  const [error, setError] = useState<string | null>(null)
  const scaleRef = useRef(scale)
  scaleRef.current = scale

  // 缩放过程中（如拖动窗口、连续滚轮缩放）先拉伸旧画面，稳定 120ms 后再重绘
  useEffect(() => {
    if (renderScale === scale) return
    const t = window.setTimeout(() => setRenderScale(scale), 120)
    return () => window.clearTimeout(t)
  }, [scale, renderScale])

  useEffect(() => {
    let cancelled = false
    let renderTask: { cancel: () => void } | null = null
    let textLayer: { cancel: () => void } | null = null
    textHost.current?.replaceChildren()

    void (async () => {
      try {
        const page = await doc.getPage(pageNumber)
        if (cancelled) return
        const viewport = page.getViewport({ scale: renderScale })
        const dpr = window.devicePixelRatio || 1
        const canvas = document.createElement('canvas')
        canvas.width = Math.floor(viewport.width * dpr)
        canvas.height = Math.floor(viewport.height * dpr)
        canvas.style.width = '100%'
        canvas.style.height = '100%'
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('无法创建绘图上下文')
        const task = page.render({
          canvas,
          canvasContext: ctx,
          viewport,
          transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : undefined
        })
        renderTask = task
        await task.promise
        if (cancelled) return
        canvasHost.current?.replaceChildren(canvas)
        setError(null)

        if (renderScale !== scaleRef.current) return
        const layer = document.createElement('div')
        layer.className = 'textLayer'
        const tl = new pdfjs.TextLayer({ textContentSource: page.streamTextContent(), container: layer, viewport })
        textLayer = tl
        await tl.render()
        if (cancelled) return
        textHost.current?.replaceChildren(layer)
      } catch (err) {
        if (!cancelled && !isCancel(err)) setError(err instanceof Error ? err.message : String(err))
      }
    })()

    return () => {
      cancelled = true
      renderTask?.cancel()
      textLayer?.cancel()
    }
  }, [doc, pageNumber, renderScale])

  const style = {
    width: size.width * scale,
    height: size.height * scale,
    '--scale-factor': scale,
    '--total-scale-factor': scale
  } as CSSProperties

  return (
    <div className="pd-page-view" data-page={pageNumber} data-scale={scale} data-hit={spots && spots.length ? 'true' : 'false'} style={style}>
      <div className="pd-page-canvas" ref={canvasHost} />
      <div className="pd-page-overlay" aria-hidden>
        {annotations.flatMap((a) =>
          a.rects.map((r, i) => <div key={`${a.id}-${i}`} className="pd-hl" style={{ ...boxStyle(r, scale), background: HIGHLIGHT_COLORS[a.color] ?? HIGHLIGHT_COLORS.yellow }} />)
        )}
        {searchHits.map((h, i) => (
          <div key={`s${i}`} className={`pd-search-hit${h.active ? ' pd-search-hit--active' : ''}`} style={boxStyle(h.bbox, scale)} />
        ))}
        {spots && spots.length > 0 && <Spotlight rects={spots} width={size.width * scale} height={size.height * scale} scale={scale} />}
        {flash && <div key={flash.key} className="pd-flash" style={boxStyle(flash.bbox, scale)} />}
      </div>
      <div className="pd-page-text" ref={textHost} />
      {smart !== false && <PageSmartLayer page={pageNumber} scale={scale} />}
      {error && <div className="pd-page-error">第 {pageNumber} 页渲染失败：{error}</div>}
    </div>
  )
})
