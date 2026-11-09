import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, FileText, FolderOpen, Globe, Link2, ListTodo, Plus, Quote, Search, Settings } from 'lucide-react'
import { Button, IconButton, Input, Menu, type MenuItem } from '@/components/ui'
import { importFolder, importLocalFiles, useImportUi } from '@/features/importing/importActions'
import { useActiveJobs } from '@/stores/jobsStore'
import { useBack } from '@/lib/useBack'
import { openPaperBrowser } from '@/lib/native'
import { usePathPrompt } from '@/stores/pathPromptStore'

const TITLES: Record<string, string> = {
  '/library': '文献库',
  '/discover': '发现',
  '/compare': '多论文比较',
  '/knowledge': '主题知识库',
  '/notes': '阅读笔记',
  '/tasks': '任务中心',
  '/settings': '设置'
}

function useCrumbs(): { title: string; sub?: string } {
  const { pathname, search } = useLocation()
  const base = '/' + (pathname.split('/')[1] ?? '')
  const title = TITLES[base] ?? 'Marginalia'
  if (base === '/library') {
    const view = new URLSearchParams(search).get('view')
    if (view === 'recent') return { title, sub: '最近阅读' }
    if (view === 'favorite') return { title, sub: '收藏' }
  }
  return { title }
}

export function Topbar() {
  const navigate = useNavigate()
  const { search } = useLocation()
  const crumbs = useCrumbs()
  const activeJobs = useActiveJobs()
  const importUi = useImportUi()
  const { pathname } = useLocation()
  const [q, setQ] = useState('')
  const typing = useRef(false)

  // 地址栏里的 q 变化（例如点击导航）时同步到搜索框；用户正在输入时不覆盖
  useEffect(() => {
    if (typing.current) return
    setQ(new URLSearchParams(search).get('q') ?? '')
  }, [search])

  // 全局搜索：输入后（防抖 250ms）文献库列表即时过滤；在其它页面输入会切到文献库
  useEffect(() => {
    if (!typing.current) return
    const t = window.setTimeout(() => {
      typing.current = false
      const query = q.trim()
      if (query) navigate(`/library?q=${encodeURIComponent(query)}`, { replace: pathname === '/library' })
      else if (pathname === '/library') navigate('/library', { replace: true })
    }, 250)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q])

  const onSearchChange = (v: string): void => {
    typing.current = true
    setQ(v)
  }
  const submit = (): void => {
    typing.current = false
    const query = q.trim()
    navigate(query ? `/library?q=${encodeURIComponent(query)}` : '/library')
  }

  const items: MenuItem[] = [
    { key: 'files', label: '本地 PDF…', icon: <FileText size={15} />, onSelect: () => void importLocalFiles() },
    { key: 'folder', label: '文件夹…', icon: <FolderOpen size={15} />, onSelect: () => void importFolder() },
    { key: 'doi', label: 'DOI…', icon: <Quote size={15} />, onSelect: importUi.openDoi },
    { key: 'url', label: '论文链接…', icon: <Link2 size={15} />, onSelect: importUi.openUrl },
    {
      key: 'browser',
      label: '从网页下载（内置浏览器）…',
      icon: <Globe size={15} />,
      onSelect: () =>
        void usePathPrompt
          .getState()
          .ask({ title: '内置浏览器', label: '输入要打开的网址（出版社、arXiv、机构库都行）。需要登录就在打开的窗口里直接登录，点到 PDF 会自动导入：', placeholder: 'https://' })
          .then((u: string | null) => u && void openPaperBrowser(/^https?:\/\//i.test(u.trim()) ? u.trim() : `https://${u.trim()}`))
    }
  ]

  // 文库、发现的页头自带标题，顶栏不再重复；文库页头已有主按钮“导入”，顶栏也不再放第二个
  const hasOwnHeading = pathname === '/library' || pathname === '/discover'
  // 从论文里点进设置、任务中心等页面后，要能一步回到刚才的论文：回到来时的上一页（论文的阅读位置是自动保存的）
  const goBack = useBack('/library')
  const location = useLocation()
  const canBack = location.key !== 'default' && !hasOwnHeading
  useEffect(() => {
    if (!canBack) return
    const onKey = (e: KeyboardEvent): void => {
      if (!e.altKey || e.ctrlKey || e.metaKey || e.key !== 'ArrowLeft') return
      const t = e.target
      if (t instanceof HTMLElement && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      e.preventDefault()
      goBack()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [canBack, goBack])

  return (
    <header className="pd-topbar">
      {canBack && <IconButton icon={<ArrowLeft size={17} />} label="返回上一页 · Alt+←" onClick={goBack} />}
      {!hasOwnHeading && (
        <div className="pd-crumbs">
          <span>{crumbs.title}</span>
          {crumbs.sub && (
            <>
              <span className="pd-crumb-sep">/</span>
              <span className="pd-crumb-sub">{crumbs.sub}</span>
            </>
          )}
        </div>
      )}

      <div className="pd-topbar-search">
        <Input
          value={q}
          onChange={onSearchChange}
          onEnter={submit}
          icon={<Search size={14} />}
          placeholder="搜索标题、作者、DOI、关键词…"
          aria-label="全局搜索"
          clearable
        />
      </div>

      {pathname !== '/library' && (
        <Menu
          align="end"
          trigger={({ toggle, open }) => (
            <Button icon={<Plus size={15} />} onClick={toggle} aria-haspopup="menu" aria-expanded={open}>
              导入
            </Button>
          )}
          items={items}
        />
      )}

      <span className="pd-topbar-badge">
        <IconButton icon={<ListTodo size={17} />} label={activeJobs.length > 0 ? `任务中心 · ${activeJobs.length} 个进行中` : '任务中心'} onClick={() => navigate('/tasks')} />
        {activeJobs.length > 0 && <span className="pd-nav-badge">{activeJobs.length}</span>}
      </span>
      <IconButton icon={<Settings size={17} />} label="设置" onClick={() => navigate('/settings')} />
    </header>
  )
}
