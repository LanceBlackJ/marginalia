import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

export interface MenuItem {
  key: string
  label?: string
  icon?: ReactNode
  hint?: string
  disabled?: boolean
  danger?: boolean
  separator?: boolean
  onSelect?: () => void
}

interface MenuProps {
  trigger: (ctl: { open: boolean; toggle: () => void }) => ReactNode
  items: MenuItem[]
  align?: 'start' | 'end'
  width?: number
}

export function Menu({ trigger, items, align = 'start', width }: MenuProps) {
  const [open, setOpen] = useState(false)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)
  const hostRef = useRef<HTMLSpanElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)

  const close = useCallback(() => setOpen(false), [])

  useLayoutEffect(() => {
    if (!open) {
      setPos(null)
      return
    }
    const host = hostRef.current?.getBoundingClientRect()
    const menu = menuRef.current?.getBoundingClientRect()
    if (!host) return
    const w = menu?.width ?? width ?? 180
    const h = menu?.height ?? 0
    let left = align === 'end' ? host.right - w : host.left
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8))
    let top = host.bottom + 4
    if (top + h > window.innerHeight - 8) top = Math.max(8, host.top - h - 4)
    setPos({ left, top })
  }, [open, align, width, items.length])

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent): void => {
      const t = e.target as Node
      if (menuRef.current?.contains(t) || hostRef.current?.contains(t)) return
      close()
    }
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        close()
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const els = Array.from(menuRef.current?.querySelectorAll<HTMLButtonElement>('button.pd-menu-item:not(:disabled)') ?? [])
        if (els.length === 0) return
        const idx = els.indexOf(document.activeElement as HTMLButtonElement)
        const next = e.key === 'ArrowDown' ? (idx + 1) % els.length : (idx - 1 + els.length) % els.length
        els[next].focus()
      }
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey, true)
    window.addEventListener('resize', close)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey, true)
      window.removeEventListener('resize', close)
    }
  }, [open, close])

  useEffect(() => {
    if (open && pos) menuRef.current?.querySelector<HTMLButtonElement>('button.pd-menu-item:not(:disabled)')?.focus({ preventScroll: true })
    // 仅在菜单首次定位完成时聚焦第一项
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, pos === null])

  return (
    <>
      <span ref={hostRef} style={{ display: 'inline-flex' }}>
        {trigger({ open, toggle: () => setOpen((o) => !o) })}
      </span>
      {open &&
        createPortal(
          <div
            ref={menuRef}
            className="pd-menu"
            role="menu"
            style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999, width }}
            // 菜单经 portal 渲染到 body，但 React 事件仍会沿组件树冒泡到触发它的父元素（如整行可点击的论文行），
            // 导致点“删除”同时触发“打开阅读”、页面跳走、确认框消失。所以在菜单这一层拦住点击和按键。
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => e.stopPropagation()}
          >
            {items.map((it) =>
              it.separator ? (
                <div key={it.key} className="pd-menu-sep" role="separator" />
              ) : (
                <button
                  key={it.key}
                  type="button"
                  role="menuitem"
                  className="pd-menu-item"
                  disabled={it.disabled}
                  data-danger={it.danger ? 'true' : undefined}
                  onClick={() => {
                    close()
                    it.onSelect?.()
                  }}
                >
                  {it.icon}
                  <span>{it.label}</span>
                  {it.hint && <span className="pd-menu-hint">{it.hint}</span>}
                </button>
              )
            )}
          </div>,
          document.body
        )}
    </>
  )
}
