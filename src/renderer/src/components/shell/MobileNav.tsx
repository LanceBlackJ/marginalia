import { BarChart3, Compass, GitCompare, Library, ListTodo, MoreHorizontal, Network, NotebookPen, Settings, X } from 'lucide-react'
import { useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

const ITEMS = [
  { to: '/library', label: '文库', icon: Library, match: (p: string) => p === '/library' || p.startsWith('/paper/') || p.startsWith('/reader/') || p.startsWith('/code/') || p.startsWith('/mindmap/') || p.startsWith('/materials/') },
  { to: '/notes', label: '笔记', icon: NotebookPen, match: (p: string) => p === '/notes' },
  { to: '/discover', label: '发现', icon: Compass, match: (p: string) => p === '/discover' || p === '/venues' || p.startsWith('/venue') },
  { to: '', label: '更多', icon: MoreHorizontal, match: () => false }
]

const MORE_ITEMS = [
  { to: '/knowledge', label: '知识库', icon: Network },
  { to: '/compare', label: '论文比较', icon: GitCompare },
  { to: '/tasks', label: '任务中心', icon: ListTodo },
  { to: '/stats', label: '统计', icon: BarChart3 },
  { to: '/settings', label: '设置', icon: Settings }
]

/** 手机端固定底部导航；桌面端不渲染，避免和左侧主导航重复。 */
export function MobileNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)
  const go = (to: string): void => {
    setMoreOpen(false)
    navigate(to)
  }
  return (
    <>
      {moreOpen && (
        <>
          <button type="button" className="pd-mobile-more-scrim" aria-label="关闭更多菜单" onClick={() => setMoreOpen(false)} />
          <div className="pd-mobile-more" role="dialog" aria-label="更多功能">
            <div className="pd-mobile-more-head">
              <span>更多功能</span>
              <button type="button" className="pd-mobile-more-close" aria-label="关闭" onClick={() => setMoreOpen(false)}><X size={18} /></button>
            </div>
            <div className="pd-mobile-more-grid">
              {MORE_ITEMS.map(({ to, label, icon: Icon }) => (
                <button key={to} type="button" className="pd-mobile-more-item" data-active={pathname.startsWith(to)} onClick={() => go(to)}>
                  <Icon size={20} />
                  <span>{label}</span>
                </button>
              ))}
            </div>
          </div>
        </>
      )}
      <nav className="pd-mobile-nav" aria-label="手机端主导航">
      {ITEMS.map(({ to, label, icon: Icon, match }) => {
        const active = match(pathname)
        const more = !to
        return (
          <button key={label} type="button" className="pd-mobile-nav-item" data-active={active || (more && moreOpen)} aria-current={active ? 'page' : undefined} onClick={() => more ? setMoreOpen((value) => !value) : go(to)}>
            <Icon size={19} />
            <span>{label}</span>
          </button>
        )
      })}
      </nav>
    </>
  )
}
