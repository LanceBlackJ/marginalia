import { useCallback, useEffect, useLayoutEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import { createPortal } from 'react-dom'
import { Maximize, Minus, Plus, X } from 'lucide-react'
import { Skeleton } from './Feedback'
import './lightbox.css'

export const LB_MIN = 1
export const LB_MAX = 8
/** 矢量图默认显示的宽度：占页面（视口）水平宽度的比例 */
export const VECTOR_FIT = 0.99

/** 缩放倍数限制在 [LB_MIN, LB_MAX]。 */
export function clampScale(s: number): number {
  return Math.min(LB_MAX, Math.max(LB_MIN, s))
}

/**
 * 以光标位置为中心缩放：图以舞台中心为变换原点，保持光标下的那一点不动。
 * d 是光标相对舞台中心的偏移，t 是当前平移。
 */
export function zoomAround(
  t: { x: number; y: number },
  from: number,
  to: number,
  d: { x: number; y: number },
  home: { x: number; y: number } = { x: 0, y: 0 }
): { x: number; y: number } {
  if (to <= LB_MIN) return home
  const k = to / from
  return { x: d.x - (d.x - t.x) * k, y: d.y - (d.y - t.y) * k }
}

// ---- 背景滚动锁（可嵌套）：html 设 overflow:hidden，并给滚动条的宽度补上 padding，页面不会跳动 ----
let locks = 0
let saved: { overflow: string; paddingRight: string } | null = null

export function lockScroll(): void {
  if (locks++ > 0) return
  const el = document.documentElement
  saved = { overflow: el.style.overflow, paddingRight: el.style.paddingRight }
  const bar = window.innerWidth - el.clientWidth
  el.style.overflow = 'hidden'
  if (bar > 0) el.style.paddingRight = `${bar}px`
}

export function unlockScroll(): void {
  if (locks === 0) return
  if (--locks > 0) return
  const el = document.documentElement
  if (saved) {
    el.style.overflow = saved.overflow
    el.style.paddingRight = saved.paddingRight
    saved = null
  }
}

const FOCUSABLE = 'button:not(:disabled), [href], [tabindex]:not([tabindex="-1"])'

interface LightboxProps {
  open: boolean
  onClose: () => void
  /** 图片地址；为 null 时显示加载骨架 */
  src: string | null
  alt: string
  /** 清晰版还在加载：先显示低清图，并提示“正在加载清晰版本” */
  loadingLarge?: boolean
}

/**
 * 点击放大的图片查看器：挂到 document.body，固定铺满当前视口，图在可见窗口正中，不受滚动容器、
 * content-visibility 或 transform 影响。滚轮或按钮缩放，双击放大/还原，放大后可拖动平移；
 * 点空白处、按 Esc 或点右上角关闭；打开时锁定背景滚动，关闭后把焦点还给打开前的元素。
 */
export function Lightbox({ open, onClose, src, alt, loadingLarge = false }: LightboxProps) {
  const stageRef = useRef<HTMLDivElement>(null)
  const rootRef = useRef<HTMLDivElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const [scale, setScale] = useState(1)
  const [tr, setTr] = useState({ x: 0, y: 0 })
  // 矢量图（SVG）：不能用 CSS 的 scale 放大（浏览器会把已经画好的位图拉大，所以放大后还是糊）。
  // 改成按放大后的尺寸直接设置宽高，浏览器每次都按新尺寸重新绘制矢量，放多大都清晰；默认宽度是页面宽度的 VECTOR_FIT。
  const [vector, setVector] = useState(false)
  const [nat, setNat] = useState<{ w: number; h: number } | null>(null)
  const [stage, setStage] = useState({ w: 0, h: 0 })
  const base = vector && nat && stage.w > 0 && nat.w > 0 ? { w: stage.w * VECTOR_FIT, h: (stage.w * VECTOR_FIT * nat.h) / nat.w } : null
  // 默认位置：图比窗口高时顶边对齐窗口（先看到图的上部，往下拖动再看），否则居中
  const home = base ? { x: 0, y: Math.max(0, (base.h - stage.h) / 2) } : { x: 0, y: 0 }
  const homeRef = useRef(home)
  homeRef.current = home
  const moved = useRef(false)
  const drag = useRef<{ id: number; sx: number; sy: number; ox: number; oy: number; moved: boolean } | null>(null)
  const swallowClick = useRef(false)
  const state = useRef({ scale: 1, tr: { x: 0, y: 0 } })
  // 没动过（也没缩放）时显示默认位置：直接在渲染里算，不等 effect 再多渲染一轮
  const pos = moved.current || scale !== 1 ? tr : home
  state.current = { scale, tr: pos }

  const reset = useCallback(() => {
    moved.current = false
    setScale(1)
    setTr(homeRef.current)
  }, [])

  // 光标相对舞台中心的偏移（没有光标位置时用 0,0，即围绕中心缩放）
  const offsetOf = useCallback((cx?: number, cy?: number) => {
    const r = stageRef.current?.getBoundingClientRect()
    if (!r || cx === undefined || cy === undefined) return { x: 0, y: 0 }
    return { x: cx - (r.left + r.width / 2), y: cy - (r.top + r.height / 2) }
  }, [])

  const zoomTo = useCallback(
    (next: number, cx?: number, cy?: number) => {
      const from = state.current.scale
      const to = clampScale(next)
      if (to === from) return
      moved.current = true
      setTr(zoomAround(state.current.tr, from, to, offsetOf(cx, cy), homeRef.current))
      setScale(to)
    },
    [offsetOf]
  )

  // 是不是矢量图：看这个 blob 的类型（本地对象地址，读取不联网）
  useEffect(() => {
    setVector(false)
    setNat(null)
    if (!open || !src || !src.startsWith('blob:')) return
    let alive = true
    fetch(src)
      .then((r) => r.blob())
      .then((b) => alive && setVector(b.type.includes('svg')))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [open, src])

  // 舞台（视口）尺寸：默认宽度按它算，窗口改变大小时跟着变
  useLayoutEffect(() => {
    if (!open) return
    const el = stageRef.current
    if (!el) return
    const measure = (): void => setStage({ w: el.clientWidth, h: el.clientHeight })
    measure()
    const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null
    ro?.observe(el)
    return () => ro?.disconnect()
  }, [open])

  // 默认尺寸算出来之后，还没动过就放到默认位置
  useEffect(() => {
    if (open && base && !moved.current && state.current.scale === 1) setTr(homeRef.current)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, base?.w, base?.h, stage.h])

  useEffect(() => {
    if (!open) return
    reset()
    const previous = document.activeElement as HTMLElement | null
    lockScroll()
    closeRef.current?.focus()
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        e.preventDefault()
        onClose()
        return
      }
      if (e.key === '+' || e.key === '=') zoomTo(state.current.scale * 1.25)
      else if (e.key === '-') zoomTo(state.current.scale / 1.25)
      else if (e.key === '0') reset()
      if (e.key === 'Tab' && rootRef.current) {
        const items = Array.from(rootRef.current.querySelectorAll<HTMLElement>(FOCUSABLE))
        if (items.length === 0) return
        const first = items[0]
        const last = items[items.length - 1]
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault()
          last.focus()
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault()
          first.focus()
        } else if (!rootRef.current.contains(document.activeElement)) {
          e.preventDefault()
          first.focus()
        }
      }
    }
    document.addEventListener('keydown', onKey, true)
    return () => {
      document.removeEventListener('keydown', onKey, true)
      unlockScroll()
      previous?.focus?.()
    }
  }, [open, onClose, reset, zoomTo])

  // 滚轮缩放：必须是非 passive 监听才能 preventDefault
  useEffect(() => {
    if (!open) return
    const el = stageRef.current
    if (!el) return
    const onWheel = (e: WheelEvent): void => {
      e.preventDefault()
      zoomTo(state.current.scale * (e.deltaY < 0 ? 1.15 : 1 / 1.15), e.clientX, e.clientY)
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [open, zoomTo])

  if (!open) return null

  const canPan = scale > 1 || Boolean(base) // 矢量图默认就比窗口大（宽度 99%，高度可能超出），放大前也要能拖
  const onPointerDown = (e: ReactPointerEvent<HTMLImageElement>): void => {
    if (!canPan) return
    e.currentTarget.setPointerCapture(e.pointerId)
    drag.current = { id: e.pointerId, sx: e.clientX, sy: e.clientY, ox: pos.x, oy: pos.y, moved: false }
  }
  const onPointerMove = (e: ReactPointerEvent<HTMLImageElement>): void => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    const dx = e.clientX - d.sx
    const dy = e.clientY - d.sy
    if (!d.moved && Math.hypot(dx, dy) > 4) d.moved = true
    if (d.moved) {
      moved.current = true
      setTr({ x: d.ox + dx, y: d.oy + dy })
    }
  }
  const onPointerUp = (e: ReactPointerEvent<HTMLImageElement>): void => {
    const d = drag.current
    if (!d || d.id !== e.pointerId) return
    swallowClick.current = d.moved
    drag.current = null
    e.currentTarget.releasePointerCapture(e.pointerId)
  }

  return createPortal(
    <div ref={rootRef} className="pd-lb" role="dialog" aria-modal="true" aria-label={`放大 ${alt}`}>
      <div
        ref={stageRef}
        className="pd-lb-stage"
        onClick={(e) => {
          if (e.target === e.currentTarget && !swallowClick.current) onClose()
          swallowClick.current = false
        }}
      >
        {src ? (
          <img
            className={base ? 'pd-lb-img pd-lb-img--vec' : 'pd-lb-img'}
            src={src}
            alt={alt}
            draggable={false}
            onLoad={(e) => setNat({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
            style={
              base
                ? {
                    width: base.w * scale,
                    height: base.h * scale,
                    transform: `translate(calc(-50% + ${pos.x}px), calc(-50% + ${pos.y}px))`,
                    cursor: drag.current ? 'grabbing' : 'grab'
                  }
                : { transform: `translate(${tr.x}px, ${tr.y}px) scale(${scale})`, cursor: scale > 1 ? (drag.current ? 'grabbing' : 'grab') : 'zoom-in' }
            }
            onPointerDown={onPointerDown}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
            onDoubleClick={(e) => (scale > 1 ? reset() : zoomTo(2.5, e.clientX, e.clientY))}
          />
        ) : (
          <div className="pd-lb-wait">
            <Skeleton height={240} />
          </div>
        )}
      </div>
      <button ref={closeRef} type="button" className="pd-lb-btn pd-lb-close" onClick={onClose} aria-label="关闭" title="关闭，Esc">
        <X size={18} />
      </button>
      <div className="pd-lb-bar">
        <button type="button" className="pd-lb-btn" onClick={() => zoomTo(scale / 1.25)} disabled={scale <= LB_MIN} aria-label="缩小" title="缩小">
          <Minus size={16} />
        </button>
        <span className="pd-lb-pct" aria-live="polite">
          {Math.round(scale * 100)}%
        </span>
        <button type="button" className="pd-lb-btn" onClick={() => zoomTo(scale * 1.25)} disabled={scale >= LB_MAX} aria-label="放大" title="放大">
          <Plus size={16} />
        </button>
        <button type="button" className="pd-lb-btn" onClick={reset} disabled={scale === 1 && pos.x === home.x && pos.y === home.y} aria-label="适应窗口" title="适应窗口">
          <Maximize size={16} />
        </button>
        <span className="pd-lb-hint">{loadingLarge ? '正在加载清晰版本…' : base ? '矢量图，放大不糊；滚轮缩放，双击放大，拖动平移' : '滚轮缩放，双击放大，拖动平移'}</span>
      </div>
    </div>,
    document.body
  )
}
