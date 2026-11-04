import type { KeyboardEvent, MouseEvent, ReactNode } from 'react'
import { useRefStore } from './refStore'
import './references.css'

/**
 * 正文里的引用标记（只有后端确认能映射到参考文献的才会渲染成它）：
 * 点击弹出轻量浮层，右键（或键盘菜单键 / Shift+F10）打开菜单，Enter 打开浮层。多个编号时点哪个对应哪个编号。
 */
export function RefMarker({ refs, sup, children }: { refs: number[]; sup?: boolean; children: ReactNode }) {
  const openPopover = useRefStore((s) => s.openPopover)
  const openMenu = useRefStore((s) => s.openMenu)
  const showHover = useRefStore((s) => s.showHover)
  const hideHover = useRefStore((s) => s.hideHover)
  const onEnter = (el: HTMLElement): void => {
    const r = el.getBoundingClientRect()
    showHover({ refs, rect: { left: r.left, right: r.right, top: r.top, bottom: r.bottom } })
  }
  const label = refs.length === 1 ? `参考文献 ${refs[0]}` : `参考文献 ${refs[0]} 到 ${refs[refs.length - 1]}`

  const onClick = (e: MouseEvent<HTMLButtonElement>): void => {
    e.stopPropagation()
    const r = e.currentTarget.getBoundingClientRect()
    openPopover(refs, { left: r.left, right: r.right, top: r.top, bottom: r.bottom })
  }
  const onContextMenu = (e: MouseEvent<HTMLButtonElement>): void => {
    e.preventDefault()
    e.stopPropagation()
    openMenu(refs, e.clientX, e.clientY)
  }
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>): void => {
    if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
      e.preventDefault()
      const r = e.currentTarget.getBoundingClientRect()
      openMenu(refs, r.left, r.bottom)
    }
  }
  return (
    <button
      type="button"
      className={sup ? 'pd-ref pd-ref--sup' : 'pd-ref'}
      aria-label={label}
      aria-haspopup="dialog"
      onMouseEnter={(e) => onEnter(e.currentTarget)}
      onMouseLeave={hideHover}
      onFocus={(e) => onEnter(e.currentTarget)}
      onBlur={hideHover}
      onClick={onClick}
      onContextMenu={onContextMenu}
      onKeyDown={onKeyDown}
    >
      {children}
    </button>
  )
}

/** 参考文献列表里的一条：点击/右键/键盘都能打开同样的浮层和菜单。 */
export function RefEntryHandlers(index: number | undefined): {
  onClick?: (e: MouseEvent<HTMLElement>) => void
  onContextMenu?: (e: MouseEvent<HTMLElement>) => void
  onKeyDown?: (e: KeyboardEvent<HTMLElement>) => void
  tabIndex?: number
  role?: string
  'aria-label'?: string
} {
  if (index === undefined) return {}
  const st = useRefStore.getState
  return {
    tabIndex: 0,
    role: 'button',
    'aria-label': `参考文献 ${index}：点击查看，右键更多`,
    onClick: (e) => {
      const r = e.currentTarget.getBoundingClientRect()
      st().openPopover([index], { left: r.left, right: r.left + 24, top: r.top, bottom: r.bottom })
    },
    onContextMenu: (e) => {
      e.preventDefault()
      st().openMenu([index], e.clientX, e.clientY)
    },
    onKeyDown: (e) => {
      if (e.key === 'Enter') {
        const r = e.currentTarget.getBoundingClientRect()
        st().openPopover([index], { left: r.left, right: r.left + 24, top: r.top, bottom: r.bottom })
      } else if (e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) {
        e.preventDefault()
        const r = e.currentTarget.getBoundingClientRect()
        st().openMenu([index], r.left, r.bottom)
      }
    }
  }
}
