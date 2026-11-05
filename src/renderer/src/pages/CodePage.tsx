import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useBack } from '@/lib/useBack'
import { AlertCircle, ArrowLeft, Download, MoreHorizontal, RefreshCw, Trash2 } from 'lucide-react'
import { Button, Dialog, EmptyState, IconButton, Menu, Select, Spinner, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { AiPanel } from '@/features/code/AiPanel'
import { CodeViewer } from '@/features/code/CodeViewer'
import { ConsistencyView } from '@/features/code/ConsistencyView'
import { FetchWizardBody, FetchWizardDialog } from '@/features/code/FetchWizard'
import { FileTree, Outline } from '@/features/code/FileTree'
import { FlowView } from '@/features/code/FlowView'
import { MappingView } from '@/features/code/MappingView'
import { OverviewView } from '@/features/code/OverviewView'
import { ReproView } from '@/features/code/repro/ReproView'
import { shortSha } from '@/features/code/codeUtils'
import { useCodeStore, type CodeTab } from '@/features/code/codeStore'
import '@/features/code/code.css'

const TABS: Array<{ value: CodeTab; label: string }> = [
  { value: 'code', label: '代码' },
  { value: 'flow', label: '运行流程' },
  { value: 'mapping', label: '论文 ↔ 代码' },
  { value: 'consistency', label: '一致性' },
  { value: 'repro', label: '复现' },
  { value: 'overview', label: '概览' }
]

const STATUS_TEXT: Record<string, string> = { pending: '正在准备…', downloading: '正在下载代码…', scanning: '正在分析代码…' }

/** 面板开合状态记在本机，下次打开保持（读写失败时按默认值，不影响使用）。 */
function usePersistedFlag(key: string, initial: boolean): [boolean, (next: boolean | ((open: boolean) => boolean)) => void] {
  const [value, setValue] = useState<boolean>(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw === null ? initial : raw === '1'
    } catch {
      return initial
    }
  })
  const set = (next: boolean | ((open: boolean) => boolean)): void => {
    setValue((prev) => {
      const n = typeof next === 'function' ? next(prev) : next
      try {
        localStorage.setItem(key, n ? '1' : '0')
      } catch {
        /* 无法保存时只在本次生效 */
      }
      return n
    })
  }
  return [value, set]
}

export function CodePage() {
  const { paperId } = useParams()
  const back = useBack(paperId ? `/paper/${paperId}` : '/library')
  const loading = useCodeStore((s) => s.loading)
  const loadError = useCodeStore((s) => s.loadError)
  const sources = useCodeStore((s) => s.sources)
  const repos = useCodeStore((s) => s.repos)
  const repoId = useCodeStore((s) => s.repoId)
  const tab = useCodeStore((s) => s.tab)
  const reanalyzing = useCodeStore((s) => s.reanalyzing)
  const explainRun = useCodeStore((s) => s.explainRun)
  const setTab = useCodeStore((s) => s.setTab)
  const [title, setTitle] = useState('')
  const [wizard, setWizard] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)
  const [notesOpen, setNotesOpen] = useState(false)
  const [leftOpen, setLeftOpen] = usePersistedFlag('pd.code.left', true)
  const [rightOpen, setRightOpen] = usePersistedFlag('pd.code.right', false)
  const repo = repos.find((r) => r.id === repoId) ?? null

  useEffect(() => {
    if (!paperId) return
    void useCodeStore.getState().init(paperId)
    let alive = true
    api.papers
      .get(paperId)
      .then((p) => alive && setTitle(p.title))
      .catch(() => undefined)
    return () => {
      alive = false
      useCodeStore.getState().reset()
    }
  }, [paperId])

  // 用户请求 AI 解释时，把默认收起的右栏打开，让回答出现在眼前
  useEffect(() => {
    if (explainRun) setRightOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [explainRun])

  if (!paperId) return null

  const started = (id: string): void => {
    setWizard(false)
    void useCodeStore.getState().refreshRepos(id)
  }

  const remove = async (): Promise<void> => {
    if (!repo) return
    setConfirmDel(false)
    try {
      await useCodeStore.getState().removeRepo(repo.id)
      toast.info('已删除代码仓库')
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }

  const ready = repo?.status === 'ready'
  const hasLeft = tab === 'code'
  const hasRight = tab === 'code' || tab === 'flow'
  const repoMeta = repo
    ? [repo.owner && repo.name ? `${repo.owner}/${repo.name}` : (repo.name ?? repo.origin), repo.branch, repo.commit_sha ? shortSha(repo.commit_sha) : null].filter(Boolean).join(' · ')
    : ''

  const header = (
    <header className="pd-cw-head">
      <div className="pd-cw-head-top">
        <IconButton icon={<ArrowLeft size={18} />} label="返回" onClick={back} />
        <div className="pd-cw-head-text">
          <h1 className="pd-cw-title" title={title}>
            {title || '代码'}
          </h1>
          {repo && (
            <div className="pd-cw-meta">
              {repos.length > 1 ? (
                <Select
                  aria-label="选择代码仓库"
                  size="sm"
                  value={repoId ?? ''}
                  onChange={(v) => void useCodeStore.getState().selectRepo(v)}
                  options={repos.map((r) => ({ value: r.id, label: `${r.name ?? r.origin} · ${shortSha(r.commit_sha)}${r.status !== 'ready' ? ` · ${STATUS_TEXT[r.status] ?? r.status}` : ''}` }))}
                  style={{ maxWidth: 280 }}
                />
              ) : (
                <span className="pd-cw-meta-text" title={repoMeta}>
                  {repoMeta}
                </span>
              )}
              {repo.warnings.length > 0 && (
                <button type="button" className="pd-cw-note-btn" aria-expanded={notesOpen} onClick={() => setNotesOpen((o) => !o)}>
                  提示 {repo.warnings.length}
                </button>
              )}
            </div>
          )}
        </div>
        <div className="pd-cw-head-actions">
          {repo && (
            <Menu
              align="end"
              trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={17} />} label="更多" selected={open} onClick={toggle} />}
              items={[
                { key: 'fetch', label: '获取其它代码…', icon: <Download size={15} />, onSelect: () => setWizard(true) },
                { key: 'reanalyze', label: reanalyzing ? '正在重新分析…' : '重新分析', icon: <RefreshCw size={15} />, disabled: reanalyzing || repo.status !== 'ready', onSelect: () => void useCodeStore.getState().reanalyze() },
                { key: 'sep', separator: true },
                { key: 'delete', label: '删除这个仓库…', icon: <Trash2 size={15} />, danger: true, onSelect: () => setConfirmDel(true) }
              ]}
            />
          )}
        </div>
      </div>
      {ready && (
        <div className="pd-cw-tools">
          <div className="pd-cw-tabs" role="tablist" aria-label="视图">
            {TABS.map((t) => (
              <button key={t.value} type="button" role="tab" className="pd-cw-tab" aria-selected={tab === t.value} onClick={() => setTab(t.value)}>
                {t.label}
              </button>
            ))}
          </div>
          {hasRight && (
            <div className="pd-cw-panels">
              {hasLeft && (
                <button type="button" className="pd-cw-ptoggle" aria-pressed={leftOpen} onClick={() => setLeftOpen((o) => !o)} title="显示或隐藏文件树">
                  文件
                </button>
              )}
              <button type="button" className="pd-cw-ptoggle" aria-pressed={rightOpen} onClick={() => setRightOpen((o) => !o)} title="显示或隐藏 AI 解释面板">
                AI 解释
              </button>
            </div>
          )}
        </div>
      )}
      {repo?.analysis_outdated && (
        <div className="pd-cw-upgrade" role="status">
          <span>这个仓库是用旧版分析的：入口排序、第三方拷贝识别和论文对应都有改进。重新分析不用重新下载，已确认的对应会保留。</span>
          <Button size="sm" disabled={reanalyzing} onClick={() => void useCodeStore.getState().reanalyze()}>
            {reanalyzing ? '正在重新分析…' : '重新分析'}
          </Button>
        </div>
      )}
      {notesOpen && repo && repo.warnings.length > 0 && (
        <ul className="pd-cw-notes" aria-label="提示">
          {repo.warnings.map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      )}
    </header>
  )

  let body: React.ReactNode
  if (loading) {
    body = (
      <div className="pd-gate">
        <EmptyState icon={<Spinner size={20} />} title="正在读取代码信息…" />
      </div>
    )
  } else if (loadError) {
    body = (
      <div className="pd-gate">
        <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="无法读取代码信息" description={loadError} action={<Button onClick={() => void useCodeStore.getState().init(paperId)}>重试</Button>} />
      </div>
    )
  } else if (!repo) {
    body = (
      <div className="pd-code-start">
        <div className="pd-code-start-inner">
          <h1 className="pd-page-title">获取这篇论文的代码</h1>
          <p className="pd-page-subtitle">先把代码放进来，之后就能浏览、解释，并和论文里的方法对照。</p>
          <FetchWizardBody paperId={paperId} sources={sources} onStarted={started} />
        </div>
      </div>
    )
  } else if (repo.status === 'failed') {
    body = (
      <div className="pd-gate">
        <EmptyState
          tone="danger"
          icon={<AlertCircle size={22} />}
          title="获取或分析代码失败"
          description={repo.error ?? '未知错误'}
          action={
            <div className="pd-row" style={{ gap: 8 }}>
              <Button variant="primary" onClick={() => setWizard(true)}>
                重新获取
              </Button>
              <Button onClick={() => setConfirmDel(true)}>删除这条记录</Button>
            </div>
          }
        />
      </div>
    )
  } else if (repo.status !== 'ready') {
    body = (
      <div className="pd-gate">
        <EmptyState
          icon={<Spinner size={20} />}
          title={STATUS_TEXT[repo.status] ?? '处理中…'}
          description={repo.status === 'pending' ? '如果这里一直没有变化，可能是下载还在等你确认，或任务已中断。可以删除后重新获取。' : '只做下载和静态分析，不会运行任何代码。'}
          action={repo.status === 'pending' ? <Button onClick={() => setConfirmDel(true)}>删除这条记录</Button> : undefined}
        />
      </div>
    )
  } else {
    body = (
      <div className="pd-code-body">
        {tab === 'code' && (
          <>
            {leftOpen && (
              <div className="pd-code-left">
                <FileTree />
                <Outline />
              </div>
            )}
            <div className="pd-code-center">
              <CodeViewer />
            </div>
            {rightOpen && <AiPanel />}
          </>
        )}
        {tab === 'flow' && (
          <>
            <div className="pd-code-center">
              <FlowView />
            </div>
            {rightOpen && <AiPanel />}
          </>
        )}
        {tab === 'mapping' && (
          <div className="pd-code-center">
            <MappingView />
          </div>
        )}
        {tab === 'consistency' && (
          <div className="pd-code-center">
            <ConsistencyView />
          </div>
        )}
        {tab === 'repro' && (
          <div className="pd-code-center">
            <ReproView />
          </div>
        )}
        {tab === 'overview' && (
          <div className="pd-code-center">
            <OverviewView />
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="pd-code">
      {header}
      {body}
      <FetchWizardDialog open={wizard} onClose={() => setWizard(false)} paperId={paperId} sources={sources} onStarted={started} />
      <Dialog
        open={confirmDel}
        onClose={() => setConfirmDel(false)}
        title="删除代码仓库"
        width={440}
        footer={
          <>
            <Button onClick={() => setConfirmDel(false)}>取消</Button>
            <Button variant="danger" solid onClick={() => void remove()}>
              删除
            </Button>
          </>
        }
      >
        <p>
          将删除“{repo?.name ?? repo?.url ?? '这个仓库'}”的本地副本和全部分析结果。论文本身不受影响。
        </p>
      </Dialog>
    </div>
  )
}
