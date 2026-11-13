import { useEffect, useState, type ReactNode } from 'react'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import { PageBoundary } from '@/components/PageBoundary'
import { AlertCircle } from 'lucide-react'
import { Button, EmptyState, ProgressBar, Spinner, ToastHost } from '@/components/ui'
import { DropImport } from '@/features/importing/DropImport'
import { ImportDialogs } from '@/features/importing/ImportDialogs'
import { importDownloadedPdf, summarizeImportJob } from '@/features/importing/importActions'
import { useMediaQuery } from '@/lib/hooks'
import { isActiveJob } from '@/lib/format'
import { useBackendStore } from '@/stores/backendStore'
import { useJobsStore, useActiveJobs } from '@/stores/jobsStore'
import { usePrefs } from '@/stores/prefsStore'
import { useShellStore } from '@/stores/shellStore'
import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'
import './shell.css'

/** 后台任务轮询：有进行中的任务 1 秒一次，否则 5 秒一次。 */
function useJobsPolling(enabled: boolean): void {
  const refresh = useJobsStore((s) => s.refresh)
  const jobs = useJobsStore((s) => s.jobs)
  const anyActive = jobs.some((j) => isActiveJob(j.status))
  useEffect(() => {
    if (!enabled) return
    let stopped = false
    let timer: number | undefined
    const tick = async (): Promise<void> => {
      try {
        await refresh()
      } catch {
        /* 下一轮重试 */
      }
      if (!stopped) timer = window.setTimeout(() => void tick(), anyActive ? 1000 : 5000)
    }
    void tick()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [enabled, anyActive, refresh])
}

function ImportProgressBar() {
  const navigate = useNavigate()
  const active = useActiveJobs().filter((j) => ['import', 'import_doi', 'import_url', 'attach_pdf', 'fetch_fulltext'].includes(j.type))
  if (active.length === 0) return null
  const job = active[0]
  return (
    <div className="pd-jobbar" role="status">
      <Spinner size={14} />
      <span className="pd-ellipsis" style={{ flex: 1, minWidth: 0 }}>
        {job.title}
        {job.message ? `：${job.message}` : ''}
        {active.length > 1 ? ` · 另有 ${active.length - 1} 个导入任务` : ''}
      </span>
      <ProgressBar value={job.status === 'running' ? job.progress : undefined} />
      <Button size="sm" variant="ghost" onClick={() => navigate('/tasks')}>
        查看任务
      </Button>
    </div>
  )
}

function BackendGate({ children }: { children: ReactNode }) {
  const { status, detail, retry, electron } = useBackendStore()
  if (status === 'ready') return <>{children}</>
  if (status === 'starting') {
    return (
      <div className="pd-gate">
        <EmptyState icon={<Spinner size={20} />} title="正在启动本地服务…" description="首次启动可能需要几秒钟。" />
      </div>
    )
  }
  return (
    <div className="pd-gate">
      <EmptyState
        tone="danger"
        icon={<AlertCircle size={22} />}
        title="本地服务不可用"
        description={
          <>
            {electron ? '后端进程没有正常启动或已退出。' : '无法连接到后端。'}
            {detail && <pre>{detail}</pre>}
          </>
        }
        action={
          <Button variant="primary" onClick={() => void retry()}>
            重试
          </Button>
        }
      />
    </div>
  )
}

export function AppShell() {
  const location = useLocation()
  const status = useBackendStore((s) => s.status)
  const init = useBackendStore((s) => s.init)
  const navCollapsed = usePrefs((s) => s.navCollapsed)
  const setPrefs = usePrefs((s) => s.set)
  const readerExpanded = useShellStore((s) => s.readerNavExpanded)
  const setReaderExpanded = useShellStore((s) => s.setReaderNavExpanded)
  const narrow = useMediaQuery('(max-width: 1199px)')
  const isReader = location.pathname.startsWith('/reader/') || location.pathname.startsWith('/code/') || location.pathname.startsWith('/mindmap/')

  useEffect(() => {
    void init()
  }, [init])
  useEffect(() => {
    const unsub = useJobsStore.getState().onFinish(summarizeImportJob)
    return unsub
  }, [])
  useJobsPolling(status === 'ready')
  // 内置“论文下载浏览器”里点到的 PDF：截下来后自动导入
  useEffect(() => {
    if (!window.paperdesk?.onPaperDownloaded) return
    return window.paperdesk.onPaperDownloaded((d) => void importDownloadedPdf(d.path, d.filename))
  }, [])

  // 窄窗口（<1200）默认收成图标条；也可以手动展开，展开的导航作为浮层盖在内容上，换页或点空白处收回
  const [narrowOpen, setNarrowOpen] = useState(false)
  useEffect(() => {
    setNarrowOpen(false)
  }, [location.pathname, narrow])
  const collapsed = narrow ? !narrowOpen : isReader ? !readerExpanded : navCollapsed
  const toggle = (): void => {
    if (narrow) setNarrowOpen((v) => !v)
    else if (isReader) setReaderExpanded(!readerExpanded)
    else setPrefs({ navCollapsed: !navCollapsed })
  }
  const sidebar = <Sidebar collapsed={collapsed} onToggle={toggle} instant={narrow && !narrowOpen} />

  return (
    <div className="pd-shell">
      {narrow ? (
        <>
          {narrowOpen && <div className="pd-nav-backdrop" onClick={() => setNarrowOpen(false)} aria-hidden />}
          <div className="pd-nav-slot">{sidebar}</div>
        </>
      ) : (
        sidebar
      )}
      <div className="pd-main">
        {!isReader && <Topbar />}
        {status === 'ready' && <ImportProgressBar />}
        <main className="pd-content">
          <BackendGate>
            <PageBoundary>
              <Outlet />
            </PageBoundary>
          </BackendGate>
        </main>
      </div>
      <ImportDialogs />
      {status === 'ready' && <DropImport />}
      <ToastHost />
    </div>
  )
}
