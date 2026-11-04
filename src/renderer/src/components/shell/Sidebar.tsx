import logoUrl from '@/assets/logo.png'
import { useEffect, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Compass,
  GitCompare,
  Landmark,
  Library,
  ListTodo,
  Network,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  Settings
} from 'lucide-react'
import { IconButton, Tooltip } from '@/components/ui'
import { api } from '@/lib/api'
import { useJobsStore, useActiveJobs } from '@/stores/jobsStore'
import { useBackendStore } from '@/stores/backendStore'
import type { PaperSummary } from '@/lib/types'

interface NavEntry {
  key: string
  label: string
  icon: ReactNode
  to: string
  /** 判断当前是否选中 */
  match: (pathname: string, params: URLSearchParams) => boolean
}

const ICON = 17

/** 主导航：不再分组加小标题；“最近阅读”“收藏”在文库页左栏里有，这里不重复 */
const MAIN: NavEntry[] = [
  { key: 'library', label: '文献库', icon: <Library size={ICON} />, to: '/library', match: (p) => p === '/library' },
  { key: 'discover', label: '发现', icon: <Compass size={ICON} />, to: '/discover', match: (p) => p === '/discover' },
  { key: 'venues', label: '期刊会议', icon: <Landmark size={ICON} />, to: '/venues', match: (p) => p === '/venues' || p === '/venue' },
  { key: 'compare', label: '多论文比较', icon: <GitCompare size={ICON} />, to: '/compare', match: (p) => p === '/compare' },
  { key: 'knowledge', label: '主题知识库', icon: <Network size={ICON} />, to: '/knowledge', match: (p) => p === '/knowledge' },
  { key: 'notes', label: '阅读笔记', icon: <NotebookPen size={ICON} />, to: '/notes', match: (p) => p === '/notes' }
]

/** 固定在底部的系统入口 */
const FOOT: NavEntry[] = [
  { key: 'tasks', label: '任务中心', icon: <ListTodo size={ICON} />, to: '/tasks', match: (p) => p === '/tasks' },
  { key: 'settings', label: '设置', icon: <Settings size={ICON} />, to: '/settings', match: (p) => p.startsWith('/settings') }
]

interface SidebarProps {
  collapsed: boolean
  onToggle: () => void
  toggleDisabledReason?: string
  /** 窗口宽度变化引起的自动收缩：不做宽度动画 */
  instant?: boolean
}

export function Sidebar({ collapsed, onToggle, toggleDisabledReason, instant }: SidebarProps) {
  const location = useLocation()
  const navigate = useNavigate()
  const params = new URLSearchParams(location.search)
  const activeJobs = useActiveJobs()
  const backendReady = useBackendStore((s) => s.status === 'ready')
  const finishedTick = useJobsStore((s) => s.finishedTick)
  const [recent, setRecent] = useState<PaperSummary[]>([])

  // 最近阅读：路由变化（例如从阅读器返回）、导入完成后刷新
  useEffect(() => {
    if (!backendReady) return
    let alive = true
    api.papers
      .list({ view: 'recent', limit: 5 })
      .then((r) => alive && setRecent(r.items.filter((p) => p.last_opened_at)))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [backendReady, location.pathname, finishedTick])

  const renderItem = (it: NavEntry): ReactNode => {
    const active = it.match(location.pathname, params)
    const badge = it.key === 'tasks' && activeJobs.length > 0 ? activeJobs.length : 0
    const el = (
      <button type="button" className="pd-nav-item" data-active={active} aria-current={active ? 'page' : undefined} onClick={() => navigate(it.to)}>
        {it.icon}
        <span className="pd-nav-label pd-ellipsis">{it.label}</span>
        {badge > 0 && <span className="pd-nav-badge">{badge}</span>}
      </button>
    )
    return collapsed ? (
      <Tooltip key={it.key} content={it.label} side="bottom">
        {el}
      </Tooltip>
    ) : (
      <div key={it.key}>{el}</div>
    )
  }

  return (
    <nav className="pd-nav" data-collapsed={collapsed} data-instant={instant ? 'true' : undefined} aria-label="主导航">
      <div className="pd-nav-head">
        <img className="pd-logo" src={logoUrl} alt="" aria-hidden />
        {!collapsed && <span className="pd-brand">Marginalia</span>}
        {!collapsed && (
          <IconButton
            icon={<PanelLeftClose size={16} />}
            label="收起导航"
            onClick={onToggle}
            disabled={!!toggleDisabledReason}
            disabledReason={toggleDisabledReason}
          />
        )}
      </div>

      <div className="pd-nav-scroll">
        <div className="pd-nav-group">{MAIN.map(renderItem)}</div>

        {recent.length > 0 && (
          <div className="pd-nav-group pd-nav-recent">
            <div className="pd-nav-group-title">最近打开</div>
            {recent.map((p) => (
              <button key={p.id} type="button" className="pd-nav-recent-item pd-ellipsis" title={p.title} onClick={() => navigate(`/reader/${p.id}`)}>
                {p.title}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="pd-nav-system">{FOOT.map(renderItem)}</div>

      {collapsed && (
        <div className="pd-nav-foot" style={{ display: 'flex', justifyContent: 'center' }}>
          <IconButton
            icon={<PanelLeftOpen size={16} />}
            label="展开导航"
            onClick={onToggle}
            disabled={!!toggleDisabledReason}
            disabledReason={toggleDisabledReason}
            tooltipSide="top"
          />
        </div>
      )}
    </nav>
  )
}
