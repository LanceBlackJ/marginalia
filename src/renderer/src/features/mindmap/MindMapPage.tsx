import { useEffect, useMemo, useState, type ReactElement } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'
import { useBack } from '@/lib/useBack'
import { AlertCircle, ArrowLeft, CornerDownRight, Keyboard, MoreHorizontal, Pencil, Plus, Redo2, Search, Trash2, Undo2 } from 'lucide-react'
import { Button, Dialog, EmptyState, IconButton, Input, Menu, ProgressBar, Spinner, Tag, type MenuItem } from '@/components/ui'
import { CitePaperContext } from '@/components/CiteChip'
import { api } from '@/lib/api'
import { useDebounced } from '@/lib/hooks'
import { formatRelativeTime } from '@/lib/format'
import { MIND_VIEW_HINTS } from '@/lib/types.mindmap'
import '@/features/insight/insight.css'
import { GenerateCard } from './GenerateCard'
import { MindCanvas } from './MindCanvas'
import { selectTree, useMindmapStore } from './mindmapStore'
import { NodeDetail } from './NodeDetail'
import { NodeDirectory } from './NodeDirectory'
import { maxDepth, navigate as navTarget, searchNodes } from './tree'
import './mindmap.css'

const HINT_KEY = 'pd.mindmap.hint'
// 快捷键提示默认收起（不遮挡画布），用户从“更多”里打开后才显示，并记住这个选择
const readHint = (): boolean => {
  try {
    return window.localStorage.getItem(HINT_KEY) === 'on'
  } catch {
    return false
  }
}
const writeHint = (on: boolean): void => {
  try {
    window.localStorage.setItem(HINT_KEY, on ? 'on' : 'off')
  } catch {
    /* 存不了就算了：只是一个提示条的显示偏好 */
  }
}

const SHORTCUTS: Array<[string, string]> = [
  ['双击 / F2', '改名'],
  ['Tab', '新增子节点'],
  ['Enter', '新增同级'],
  ['Delete', '删除分支'],
  ['方向键', '在节点间移动'],
  ['空格', '折叠 / 展开'],
  ['Ctrl+Z', '撤销，Ctrl+Shift+Z 重做'],
  ['拖到别的节点上', '改父级 / 调整顺序'],
  ['双击空白', '整张图适应窗口']
]

/** 思维导图工作区（/mindmap/:paperId）：一行工具栏 + 可收起的目录 + 画布 + 选中节点时才出现的详情。与阅读器右侧面板共用同一份数据。 */
export function MindMapPage() {
  const { paperId } = useParams()
  const back = useBack(paperId ? `/paper/${paperId}` : '/library')
  const [params] = useSearchParams()
  const [title, setTitle] = useState<string>('')
  const [query, setQuery] = useState('')
  const [dirOpen, setDirOpen] = useState(() => (typeof window === 'undefined' ? true : window.innerWidth >= 1360))
  const [hintOpen, setHintOpen] = useState(readHint)
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null)
  const debounced = useDebounced(query, 250)
  const view = useMindmapStore((s) => s.view)
  const tree = useMindmapStore(selectTree)
  const loading = useMindmapStore((s) => s.loading)
  const error = useMindmapStore((s) => s.error)
  const hits = useMindmapStore((s) => s.hits)
  const selectedId = useMindmapStore((s) => s.selectedId)
  const detail = useMindmapStore((s) => s.detail)
  const progress = useMindmapStore((s) => s.progress[s.view])
  const canUndo = useMindmapStore((s) => s.canUndo)
  const canRedo = useMindmapStore((s) => s.canRedo)

  useEffect(() => {
    if (!paperId) return
    void useMindmapStore.getState().open(paperId, { nodeId: params.get('node') })
    api.papers.get(paperId).then((p) => setTitle(p.title), () => undefined)
    return () => useMindmapStore.getState().reset()
    // 只在切换论文时初始化；视角/节点之后由界面操作驱动
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId])

  useEffect(() => {
    void useMindmapStore.getState().runSearch(debounced)
  }, [debounced])

  const nodes = tree?.nodes ?? []
  const matchIds = useMemo(() => new Set(searchNodes(nodes, debounced)), [nodes, debounced])
  const node = nodes.find((n) => n.id === selectedId) ?? null
  const status = tree?.status ?? 'none'
  const active = status === 'running' || status === 'pending'
  const deleting = nodes.find((n) => n.id === confirmDeleteId) ?? null

  const stepMatch = (): void => {
    const ids = searchNodes(nodes, query)
    if (ids.length === 0) return
    const i = Math.max(0, ids.indexOf(selectedId ?? ''))
    void useMindmapStore.getState().select(ids[(i + (ids.includes(selectedId ?? '') ? 1 : 0)) % ids.length], { focus: true })
  }
  const toggleHint = (): void => {
    setHintOpen((o) => {
      writeHint(!o)
      return !o
    })
  }

  // 键盘操作（焦点不在输入框时）
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement | null
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      const st = useMindmapStore.getState()
      const mod = e.ctrlKey || e.metaKey
      if (mod) {
        const k = e.key.toLowerCase()
        if (k === 'z') {
          e.preventDefault()
          void (e.shiftKey ? st.redo() : st.undo())
        } else if (k === 'y') {
          e.preventDefault()
          void st.redo()
        } else if (k === 'f') {
          e.preventDefault()
          document.querySelector<HTMLInputElement>('.pd-mm-search input')?.focus()
        }
        return
      }
      if (e.altKey || !node) return
      const cur = st.trees[st.view]
      const collapsed = st.collapsed[st.view] ?? new Set<string>()
      const isRoot = node.depth === 1
      const go = (id: string | null): void => {
        if (id) {
          e.preventDefault()
          void st.select(id, { focus: true })
        }
      }
      switch (e.key) {
        case 'F2':
          e.preventDefault()
          st.requestEdit(node.id)
          break
        case 'Tab':
          e.preventDefault()
          void st.quickAdd('child', node.id)
          break
        case 'Enter':
          e.preventDefault()
          if (!isRoot) void st.quickAdd('sibling', node.id)
          break
        case 'Delete':
        case 'Backspace':
          if (!isRoot) {
            e.preventDefault()
            setConfirmDeleteId(node.id)
          }
          break
        case ' ':
          if (node.child_count > 0) {
            e.preventDefault()
            st.toggleCollapse(node.id)
          }
          break
        case 'ArrowLeft':
          if (cur) go(navTarget(cur.nodes, collapsed, node.id, 'parent'))
          break
        case 'ArrowRight':
          if (cur) {
            if (collapsed.has(node.id) && node.child_count > 0) {
              e.preventDefault()
              st.toggleCollapse(node.id)
            } else go(navTarget(cur.nodes, collapsed, node.id, 'child'))
          }
          break
        case 'ArrowUp':
          if (cur) go(navTarget(cur.nodes, collapsed, node.id, 'prev'))
          break
        case 'ArrowDown':
          if (cur) go(navTarget(cur.nodes, collapsed, node.id, 'next'))
          break
        case 'Escape':
          void st.select(null)
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [node])

  if (!paperId) return null

  const depthMax = maxDepth(nodes)
  const st = useMindmapStore.getState
  const expandItems: MenuItem[] = [
    { key: 'all', label: '全部展开', onSelect: () => st().collapseToDepth(Infinity) },
    ...[2, 3, 4].filter((d) => d < depthMax).map((d) => ({ key: `d${d}`, label: `展开到第 ${d} 层`, onSelect: () => st().collapseToDepth(d) })),
    { key: 'root', label: '只看根节点', onSelect: () => st().collapseToDepth(1) },
    ...(nodes.some((n) => n.x !== null && n.y !== null)
      ? [{ key: 'layout', label: '整理布局，清除手动位置', onSelect: () => void st().resetLayout().then(() => window.dispatchEvent(new Event('pd-mindmap-fit'))) }]
      : [])
  ]

  const body = (): ReactElement => {
    if (error && !tree) {
      return (
        <div className="pd-gate">
          <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="无法打开思维导图" description={error} action={<Button onClick={back}>返回</Button>} />
        </div>
      )
    }
    if (!tree && loading) {
      return (
        <div className="pd-gate">
          <EmptyState icon={<Spinner size={20} />} title="正在打开思维导图…" />
        </div>
      )
    }
    if (nodes.length === 0) {
      return (
        <div className="pd-gate">
          <GenerateCard view={view} status={status} error={tree?.error} progress={progress} onGenerate={(v) => void useMindmapStore.getState().generate(v)} />
        </div>
      )
    }
    const verification = tree?.verification
    return (
      <div className="pd-mm-body" data-dir={dirOpen ? 'open' : 'closed'} data-detail={node ? 'open' : 'closed'}>
        {dirOpen && <NodeDirectory nodes={nodes} hits={hits} query={debounced} />}
        <section className="pd-mm-canvas" aria-label="思维导图画布">
          <MindCanvas matchIds={matchIds} />
          {active && (
            <div className="pd-mm-banner" role="status">
              <Spinner size={13} />
              <span className="pd-ellipsis">{tree?.scope ? '正在重新生成一个分支' : '正在重新生成逻辑框架'}：{progress?.message || '请稍候'}</span>
              <div style={{ width: 90 }}>
                <ProgressBar value={status === 'running' && progress ? progress.progress : undefined} />
              </div>
            </div>
          )}
          {status === 'failed' && (
            <div className="pd-mm-banner pd-mm-banner--warn" role="note">
              <AlertCircle size={13} />
              <span className="pd-ellipsis" title={tree?.error ?? undefined}>上次重新生成没有成功，当前显示的是之前的内容：{tree?.error}</span>
            </div>
          )}
          {hintOpen && (
            <div className="pd-mm-hint" role="note" aria-label="快捷键">
              <dl>
                {SHORTCUTS.map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{v}</dd>
                  </div>
                ))}
              </dl>
              <button type="button" onClick={toggleHint}>
                关闭
              </button>
            </div>
          )}
          <div className="pd-mm-status pd-aux">
            <span>{nodes.length} 个节点</span>
            {tree?.model && <span>{tree.model}</span>}
            {tree?.generated_at && <span>{formatRelativeTime(tree.generated_at)}生成</span>}
            {tree && tree.stats.edited_nodes > 0 && <span>你编辑过 {tree.stats.edited_nodes} 个，重新生成时保留</span>}
            {verification && (verification.report.dropped_refs > 0 || verification.report.dropped_nodes > 0) && (
              <Tag tone="outline" title={verification.report.notes.join(' ')}>
                已丢弃 {verification.report.dropped_refs} 处无效引用{verification.report.dropped_nodes > 0 ? `、${verification.report.dropped_nodes} 个无依据节点` : ''}
              </Tag>
            )}
          </div>
        </section>
        {node && (
          <CitePaperContext.Provider value={paperId}>
            <NodeDetail paperId={paperId} node={node} detail={detail} onClose={() => void useMindmapStore.getState().select(null)} />
          </CitePaperContext.Provider>
        )}
      </div>
    )
  }

  const empty = nodes.length === 0
  const moreItems: MenuItem[] = [
    { key: 'md', label: '导出 Markdown 大纲', hint: '.md', disabled: empty, onSelect: () => void useMindmapStore.getState().exportView('markdown') },
    { key: 'json', label: '导出 JSON，含依据', hint: '.json', disabled: empty, onSelect: () => void useMindmapStore.getState().exportView('json') },
    { key: 'sep', separator: true },
    { key: 'hint', label: hintOpen ? '隐藏快捷键提示' : '显示快捷键提示', icon: <Keyboard size={15} />, disabled: empty, onSelect: toggleHint }
  ]

  return (
    <div className="pd-mm">
      <header className="pd-mw-head">
        <div className="pd-mw-top">
          <IconButton icon={<ArrowLeft size={18} />} label="返回" onClick={back} />
          <div className="pd-mw-titles">
            <h1 className="pd-mw-title" title={title}>
              {title || '思维导图'}
            </h1>
            <div className="pd-mw-sub">
              逻辑框架{empty ? '' : ` · ${nodes.length} 个节点`}
            </div>
          </div>
          <div className="pd-mw-actions">
            <Menu
              align="end"
              trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={17} />} label="更多" selected={open} onClick={toggle} />}
              items={moreItems}
            />
            <Button variant={empty ? 'primary' : 'secondary'} loading={active} onClick={() => void useMindmapStore.getState().generate(view)}>
              {empty ? '生成' : '重新生成'}
            </Button>
          </div>
        </div>
        <div className="pd-mw-tools">
          <div className="pd-mw-hint pd-aux pd-muted" title={MIND_VIEW_HINTS.logic}>
            {MIND_VIEW_HINTS.logic}
          </div>
          {!empty && (
            <div className="pd-mm-edit" role="group" aria-label="修改导图">
              {node ? (
                <>
                  <Button size="sm" variant="ghost" icon={<Pencil size={14} />} title="改这个节点的名字 · 双击或 F2" onClick={() => useMindmapStore.getState().requestEdit(node.id)}>
                    改名
                  </Button>
                  <Button size="sm" variant="ghost" icon={<Plus size={14} />} title="在它下面加一个子节点 · Tab" onClick={() => void useMindmapStore.getState().quickAdd('child', node.id)}>
                    加子节点
                  </Button>
                  <Button size="sm" variant="ghost" icon={<CornerDownRight size={14} />} disabled={node.depth === 1} title="在它后面加一个同级节点 · Enter" onClick={() => void useMindmapStore.getState().quickAdd('sibling', node.id)}>
                    加同级
                  </Button>
                  <Button size="sm" variant="ghost" icon={<Trash2 size={14} />} disabled={node.depth === 1} title="删除这个节点和它的子节点 · Delete" onClick={() => setConfirmDeleteId(node.id)}>
                    删除
                  </Button>
                </>
              ) : (
                <span className="pd-aux pd-muted">点一个节点，就能改名、加子节点、删除；拖动节点可以调整位置和归属</span>
              )}
            </div>
          )}
          <div className="pd-mw-tools-right">
            <div className="pd-mm-search">
            <Input
              value={query}
              onChange={setQuery}
              icon={<Search size={14} />}
              clearable
              placeholder="搜索节点"
              aria-label="搜索节点"
              onEnter={stepMatch}
            />
            {debounced.trim() && <span className="pd-aux pd-weak">{matchIds.size > 0 ? `${matchIds.size} 个` : '无匹配'}</span>}
          </div>
            <span className="pd-mm-sep" />
            <Button size="sm" variant="ghost" aria-pressed={dirOpen} disabled={empty} onClick={() => setDirOpen((o) => !o)} title="节点目录与按类型过滤">
              目录
            </Button>
            <Menu
              align="end"
              trigger={({ toggle, open }) => (
                <Button size="sm" variant="ghost" disabled={empty} onClick={toggle} aria-haspopup="menu" aria-expanded={open}>
                  展开 ▾
                </Button>
              )}
              items={expandItems}
            />
            <span className="pd-mm-sep" />
            <IconButton icon={<Undo2 size={15} />} label="撤销 · Ctrl+Z" size="sm" disabled={!canUndo} onClick={() => void useMindmapStore.getState().undo()} />
            <IconButton icon={<Redo2 size={15} />} label="重做 · Ctrl+Shift+Z" size="sm" disabled={!canRedo} onClick={() => void useMindmapStore.getState().redo()} />
          </div>
        </div>
      </header>
      {body()}
      <Dialog
        open={deleting !== null}
        onClose={() => setConfirmDeleteId(null)}
        title="删除这个分支？"
        width={420}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDeleteId(null)}>取消</Button>
            <Button
              variant="danger"
              solid
              onClick={() => {
                const id = confirmDeleteId
                setConfirmDeleteId(null)
                if (id) void useMindmapStore.getState().deleteNode(id)
              }}
            >
              删除
            </Button>
          </>
        }
      >
        {deleting && (
          <p style={{ margin: 0 }}>
            「{deleting.title}」{deleting.child_count > 0 ? `及其下的 ${deleting.child_count} 个子节点` : ''}会从思维导图里删除，不影响论文和已生成的研究卡片。删错了可以按 Ctrl+Z 撤销；如果之后重新生成思维导图，AI 生成过的内容可能再次出现。
          </p>
        )}
      </Dialog>
    </div>
  )
}
