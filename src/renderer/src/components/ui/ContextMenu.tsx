import { useCallback, useEffect, useLayoutEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import type { MenuItem } from './Menu'

interface State {
  x: number
  y: number
  items: MenuItem[]
}

function ContextMenuView({ state, onClose }: { state: State; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ left: number; top: number } | null>(null)

  useLayoutEffect(() => {
    const r = ref.current?.getBoundingClientRect()
    const w = r?.width ?? 180
    const h = r?.height ?? 0
    setPos({ left: Math.max(8, Math.min(state.x, window.innerWidth - w - 8)), top: Math.max(8, Math.min(state.y, window.innerHeight - h - 8)) })
  }, [state])

  useEffect(() => {
    const down = (e: globalThis.MouseEvent): void => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        onClose()
      } else if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const els = Array.from(ref.current?.querySelectorAll<HTMLButtonElement>('button.pd-menu-item:not(:disabled)') ?? [])
        if (els.length === 0) return
        const idx = els.indexOf(document.activeElement as HTMLButtonElement)
        els[e.key === 'ArrowDown' ? (idx + 1) % els.length : (idx - 1 + els.length) % els.length].focus()
      }
    }
    const away = (): void => onClose()
    document.addEventListener('mousedown', down, true)
    document.addEventListener('contextmenu', down, true) // 在别处再右键：先关掉这一个
    document.addEventListener('keydown', key, true)
    window.addEventListener('resize', away)
    window.addEventListener('blur', away)
    window.addEventListener('wheel', away, { passive: true })
    return () => {
      document.removeEventListener('mousedown', down, true)
      document.removeEventListener('contextmenu', down, true)
      document.removeEventListener('keydown', key, true)
      window.removeEventListener('resize', away)
      window.removeEventListener('blur', away)
      window.removeEventListener('wheel', away)
    }
  }, [onClose])

  useEffect(() => {
    if (pos) ref.current?.querySelector<HTMLButtonElement>('button.pd-menu-item:not(:disabled)')?.focus({ preventScroll: true })
    // 只在第一次定位完成时聚焦第一项
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pos === null])

  return createPortal(
    <div
      ref={ref}
      className="pd-menu"
      role="menu"
      style={{ left: pos?.left ?? -9999, top: pos?.top ?? -9999 }}
      onClick={(e) => e.stopPropagation()}
      onContextMenu={(e) => e.preventDefault()}
      onKeyDown={(e) => e.stopPropagation()}
    >
      {state.items.map((it) =>
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
              onClose()
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
  )
}

/**
 * 右键菜单：`const cm = useContextMenu()`，在元素上写 `onContextMenu={(e) => cm.open(e, items)}`，并在组件里渲染 `{cm.element}`。
 * 菜单在鼠标位置弹出，Esc、点别处、滚动、再右键别处都会关掉；方向键选择、Enter 执行。
 * 选中文字时右键会保留选区（不吞掉），items 为空数组时不拦截系统默认菜单。
 */
export function useContextMenu(): { open: (e: MouseEvent, items: MenuItem[]) => void; element: ReactNode } {
  const [state, setState] = useState<State | null>(null)
  const close = useCallback(() => setState(null), [])
  const open = useCallback((e: MouseEvent, items: MenuItem[]) => {
    if (items.length === 0 || e.defaultPrevented) return
    e.preventDefault()
    e.stopPropagation()
    setState({ x: e.clientX, y: e.clientY, items })
  }, [])
  return { open, element: state ? <ContextMenuView state={state} onClose={close} /> : null }
}
