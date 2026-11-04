import { useEffect, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { ChevronDown, ChevronRight, ExternalLink, RefreshCw } from 'lucide-react'
import { Button, Skeleton, Tag } from '@/components/ui'
import { formatRelativeTime } from '@/lib/format'
import { useReaderStore } from '@/features/reader/readerStore'
import { EntityChips, EvidenceChips, useMindJump } from './evidence'
import { GenerateCard } from './GenerateCard'
import { selectTree, useMindmapStore } from './mindmapStore'
import { firstEvidence, outlineRows } from './tree'
import './mindmap.css'

/** 阅读器右侧的“思维导图”标签：紧凑的可折叠树；点节点 → PDF 跳到它的第一处依据并高亮，下方显示摘要与全部依据。 */
export function MindMapPanel() {
  const navigate = useNavigate()
  const paperId = useReaderStore((s) => s.paperId)
  const view = useMindmapStore((s) => s.view)
  const tree = useMindmapStore(selectTree)
  const loading = useMindmapStore((s) => s.loading)
  const error = useMindmapStore((s) => s.error)
  const collapsedMap = useMindmapStore((s) => s.collapsed)
  const selectedId = useMindmapStore((s) => s.selectedId)
  const detail = useMindmapStore((s) => s.detail)
  const progress = useMindmapStore((s) => s.progress[s.view])
  const jump = useMindJump(paperId)

  useEffect(() => {
    if (paperId) void useMindmapStore.getState().open(paperId)
    return () => useMindmapStore.getState().reset()
  }, [paperId])

  const collapsed = collapsedMap[view]
  const rows = useMemo(() => (tree ? outlineRows(tree.nodes, collapsed ?? new Set()) : []), [tree, collapsed])
  const node = tree?.nodes.find((n) => n.id === selectedId) ?? null
  const hasNodes = (tree?.nodes.length ?? 0) > 0
  const status = tree?.status ?? 'none'

  const onSelect = (id: string): void => {
    const n = tree?.nodes.find((x) => x.id === id)
    void useMindmapStore.getState().select(id)
    const ev = n ? firstEvidence(n) : null
    if (ev) jump(ev)
  }

  const openWorkspace = (): void => {
    if (paperId) navigate(`/mindmap/${paperId}?view=${view}${selectedId ? `&node=${selectedId}` : ''}`)
  }

  return (
    <div className="pd-mmp">
      <div className="pd-mmp-head">
        <span className="pd-aux pd-muted">逻辑框架</span>
        <span className="pd-spacer" />
        <Button size="sm" variant="ghost" icon={<ExternalLink size={13} />} onClick={openWorkspace} disabled={!paperId}>
          在工作区打开
        </Button>
      </div>

      {error && !tree && <div className="pd-field-error" style={{ margin: 'var(--sp-md) var(--sp-lg)' }}>思维导图加载失败：{error}</div>}

      {(loading && !tree) || (!tree && !error) ? (
        <div className="pd-mmp-body">
          <Skeleton height={80} />
        </div>
      ) : !hasNodes ? (
        <div className="pd-mmp-body">
          <GenerateCard view={view} status={status} error={tree?.error} progress={progress} compact onGenerate={(v) => void useMindmapStore.getState().generate(v)} />
        </div>
      ) : (
        <>
          <div className="pd-mmp-meta pd-aux pd-weak">
            <span className="pd-ellipsis" style={{ flex: 1 }}>
              {tree?.nodes.length} 个节点 · {tree?.model ? `${tree.model} · ` : ''}
              {tree?.generated_at ? formatRelativeTime(tree.generated_at) : ''}生成
            </span>
            {(status === 'running' || status === 'pending') && <Tag tone="outline">{tree?.scope ? '分支重新生成中' : '重新生成中'}</Tag>}
            {status === 'failed' && <Tag tone="warning" title={tree?.error ?? undefined}>上次重新生成失败</Tag>}
            <Button
              size="sm"
              variant="ghost"
              icon={<RefreshCw size={13} />}
              disabled={status === 'running' || status === 'pending'}
              onClick={() => void useMindmapStore.getState().generate(view)}
            >
              重新生成
            </Button>
          </div>
          <div className="pd-mmp-tree" role="tree" aria-label="思维导图">
            {rows.map(({ node: n, level, open, expandable }) => (
              <div key={n.id} role="treeitem" aria-level={level + 1} aria-expanded={expandable ? open : undefined} aria-selected={n.id === selectedId} className="pd-mmp-row" data-selected={n.id === selectedId} data-root={n.depth === 1} style={{ paddingLeft: 8 + level * 16 }}>
                <button type="button" className="pd-mmp-chev" aria-label={open ? '折叠' : '展开'} disabled={!expandable} onClick={() => useMindmapStore.getState().toggleCollapse(n.id)}>
                  {expandable ? open ? <ChevronDown size={14} /> : <ChevronRight size={14} /> : null}
                </button>
                <button type="button" className="pd-mmp-title" onClick={() => onSelect(n.id)} title={n.summary || n.title}>
                  <span className="pd-ellipsis">{n.title}</span>
                </button>
                {n.origin === 'user' && <span className="pd-aux pd-weak">新增</span>}
              </div>
            ))}
          </div>
          <div className="pd-mmp-detail" aria-live="polite">
            {node ? (
              <>
                <div className="pd-mmp-d-title">{node.title}</div>
                <div className="pd-row" style={{ gap: 6, marginBottom: 4 }}>
                  <Tag>{node.kind_label}</Tag>
                  {node.user_edited && <Tag tone="outline">{node.origin === 'user' ? '用户添加' : '已编辑'}</Tag>}
                </div>
                {node.summary && <p className="pd-mmp-d-sum">{node.summary}</p>}
                <div className="pd-ov-ev">
                  <span className="pd-aux pd-weak" style={{ marginRight: 4 }}>
                    {node.evidence.derived ? '下级依据' : '依据'}
                  </span>
                  {node.refs.length > 0 ? <EvidenceChips refs={node.refs} jump={jump} /> : <span className="pd-aux pd-weak">无，用户添加的节点</span>}
                  {detail && detail.id === node.id && <EntityChips figures={detail.figures} equations={detail.equations} jump={jump} />}
                </div>
              </>
            ) : (
              <p className="pd-aux pd-weak" style={{ margin: 0 }}>
                点击节点，PDF 会跳到它的第一处原文依据并高亮；在工作区里还能编辑节点、让 AI 解释节点。
              </p>
            )}
          </div>
        </>
      )}
    </div>
  )
}
