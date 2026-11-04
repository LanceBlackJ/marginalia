import { cloneElement, isValidElement, useEffect, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

interface TooltipProps {
  content: ReactNode
  children: ReactNode
  side?: 'top' | 'bottom'
  delay?: number
}

/** 悬停提示。包住 disabled 按钮时同样有效（disabled 按钮在宿主内 pointer-events:none）。 */
export function Tooltip({ content, children, side = 'bottom', delay = 350 }: TooltipProps) {
  const [pos, setPos] = useState<{ x: number; y: number; up: boolean } | null>(null)
  const hostRef = useRef<HTMLSpanElement>(null)
  const timer = useRef<number | undefined>(undefined)

  useEffect(() => () => window.clearTimeout(timer.current), [])

  if (!content) return <>{children}</>

  const show = (): void => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const r = hostRef.current?.getBoundingClientRect()
      if (!r) return
      // 下方放不下（按钮贴着窗口底边）就改成向上弹，不然提示会被窗口截掉
      const up = side === 'top' || (window.innerHeight - r.bottom < 64 && r.top > 64)
      setPos({ x: r.left + r.width / 2, y: up ? r.top - 6 : r.bottom + 6, up })
    }, delay)
  }
  const hide = (): void => {
    window.clearTimeout(timer.current)
    setPos(null)
  }

  return (
    <span
      ref={hostRef}
      className="pd-tooltip-host"
      style={{ display: 'inline-flex' }}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      onMouseDown={hide}
    >
      {children}
      {pos &&
        createPortal(
          <div
            role="tooltip"
            className="pd-tooltip"
            style={{
              left: `clamp(136px, ${pos.x}px, calc(100vw - 136px))`,
              top: pos.y,
              transform: pos.up ? 'translate(-50%, -100%)' : 'translateX(-50%)'
            }}
          >
            {content}
          </div>,
          document.body
        )}
    </span>
  )
}

/** 给已有元素加 tooltip 的便捷方式：保持原元素类型不变。 */
export function withTooltip(el: ReactElement, content: ReactNode): ReactElement {
  return isValidElement(el) ? <Tooltip content={content}>{cloneElement(el)}</Tooltip> : el
}
