import { useEffect, useRef } from 'react'
import { Bookmark as BookmarkIcon, BookmarkMinus } from 'lucide-react'
import './references.css'

export interface BookmarkMenuState {
  x: number
  y: number
  /** 这一段上已经有书签：菜单改成“移除” */
  existingId: string | null
  onAdd: () => void
  onRemove: (id: string) => void
}

/** 在正文上右键弹出的菜单：在这一段加书签 / 移除这一段的书签。样式沿用参考文献的右键菜单。 */
export function BookmarkMenu({ menu, onClose }: { menu: BookmarkMenuState; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('button')?.focus()
    const down = (e: PointerEvent): void => {
      if (!ref.current?.contains(e.target as Node)) onClose()
    }
    const key = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    const away = (): void => onClose()
    document.addEventListener('pointerdown', down, true)
    document.addEventListener('keydown', key)
    window.addEventListener('blur', away)
    window.addEventListener('resize', away)
    return () => {
      document.removeEventListener('pointerdown', down, true)
      document.removeEventListener('keydown', key)
      window.removeEventListener('blur', away)
      window.removeEventListener('resize', away)
    }
  }, [onClose])

  const x = Math.max(8, Math.min(menu.x, window.innerWidth - 200))
  const y = Math.max(8, Math.min(menu.y, window.innerHeight - 56))
  const remove = menu.existingId !== null
  return (
    <div ref={ref} className="pd-refmenu" role="menu" aria-label="正文" style={{ left: x, top: y, minWidth: 176 }}>
      <button
        type="button"
        role="menuitem"
        className="pd-refmenu-item"
        onClick={() => {
          onClose()
          if (menu.existingId !== null) menu.onRemove(menu.existingId)
          else menu.onAdd()
        }}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {remove ? <BookmarkMinus size={14} /> : <BookmarkIcon size={14} />}
          {remove ? '移除这里的书签' : '在这里添加书签'}
        </span>
      </button>
    </div>
  )
}
