import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { AlertTriangle, ChevronDown, ChevronUp, CornerDownRight, Plus, RefreshCw, Sparkles, Square, Trash2, X } from 'lucide-react'
import { Button, Dialog, IconButton, Input, TextArea, Tag, toast } from '@/components/ui'
import { buildEvidenceMap, buildInvalidSet, CiteGroup } from '@/components/CiteChip'
import { Markdown } from '@/components/Markdown'
import { api, errorMessage } from '@/lib/api'
import { formatRelativeTime, truncate } from '@/lib/format'
import type { InsightCard } from '@/lib/types'
import type { MindCardBrief, MindNode, MindNodeDetail } from '@/lib/types.mindmap'
import { EntityChips, useMindJump } from './evidence'
import { useMindmapStore } from './mindmapStore'

const VERIFY_HINT: Record<string, string | null> = {
  verified: null,
  partial: '部分引用无法在论文中核实，请对照原文确认。',
  no_evidence: '这条回答没有附上可核对的论文原文依据，请谨慎对待。',
  unverified: '这条回答的依据尚未核实。'
}

/** 解释卡片（精简版）：回答 + 依据 chip + 展开的详细分析与 AI 推断。卡片本身是普通的研究卡片，也会出现在阅读器的 AI 助手里。 */
function ExplainCardView({ card, onDeleted }: { card: InsightCard; onDeleted: () => void }) {
  const [expanded, setExpanded] = useState(false)
  const evMap = useMemo(() => buildEvidenceMap(card.evidence), [card.evidence])
  const invalid = useMemo(() => buildInvalidSet(card.verification.invalid_citations, card.verification.invalid_refs), [card.verification])
  const hint = card.verification.notes.length > 0 ? card.verification.notes.join(' ') : (VERIFY_HINT[card.verification.status] ?? null)
  const a = card.answer
  const more = !!a.detail || a.inferences.length > 0
  return (
    <article className="pd-mmx-card">
      <div className="pd-row" style={{ gap: 6, marginBottom: 4 }}>
        <Tag>{card.status === 'complete' ? 'AI 解释' : '未完成'}</Tag>
        <span className="pd-aux pd-weak pd-ellipsis" style={{ flex: 1 }} title={card.model}>
          {truncate(card.model, 22)} · {formatRelativeTime(card.created_at)}
        </span>
        <IconButton
          icon={<Trash2 size={13} />}
          label="删除这张卡片"
          size="sm"
          onClick={() => {
            void api.cards.remove(card.id).then(onDeleted, (e) => toast.error('删除失败', errorMessage(e)))
          }}
        />
      </div>
      {card.question && <p className="pd-aux pd-weak" style={{ margin: '0 0 6px' }}>{truncate(card.question, 120)}</p>}
      {hint && (
        <div className="pd-card-hint" role="note">
          <AlertTriangle size={13} />
          <span>{hint}</span>
        </div>
      )}
      <Markdown text={a.direct} evidence={card.evidence} invalid={card.verification.invalid_citations} invalidRefs={card.verification.invalid_refs} />
      {card.evidence.length > 0 && (
        <div className="pd-ov-ev">
          <span className="pd-aux pd-weak" style={{ marginRight: 4 }}>依据</span>
          <CiteGroup refs={card.evidence.map((e) => ({ doc: e.source?.doc ?? 0, ord: e.ord }))} evidence={evMap} invalid={invalid} />
        </div>
      )}
      {expanded && (
        <div className="pd-mmx-more">
          {a.detail && <Markdown text={a.detail} evidence={card.evidence} invalid={card.verification.invalid_citations} invalidRefs={card.verification.invalid_refs} />}
          {a.inferences.length > 0 && (
            <ul className="pd-card-inf">
              {a.inferences.map((inf, i) => (
                <li key={i}>
                  <Tag tone="info">AI 推断</Tag>
                  <span>{inf.text}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      {more && (
        <button type="button" className="pd-card-toggle" onClick={() => setExpanded((e) => !e)} aria-expanded={expanded}>
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
          {expanded ? '收起' : '展开详细分析'}
        </button>
      )}
    </article>
  )
}

function CardBriefRow({ brief, paperId, onChanged }: { brief: MindCardBrief; paperId: string; onChanged: () => void }) {
  const [card, setCard] = useState<InsightCard | null>(null)
  const [open, setOpen] = useState(false)
  const toggle = async (): Promise<void> => {
    if (!open && !card) {
      try {
        const r = await api.cards.list(paperId)
        setCard(r.items.find((c) => c.id === brief.id) ?? null)
      } catch (e) {
        toast.error('加载卡片失败', errorMessage(e))
        return
      }
    }
    setOpen((o) => !o)
  }
  return (
    <div className="pd-mmx-brief">
      <button type="button" className="pd-mmx-brief-h" onClick={() => void toggle()} aria-expanded={open}>
        <Tag tone="outline">{brief.relation === 'explain' ? '节点解释' : '引用同一段原文'}</Tag>
        <span className="pd-ellipsis" style={{ flex: 1, minWidth: 0 }}>{brief.direct || brief.title}</span>
        {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
      </button>
      {open && card && <ExplainCardView card={card} onDeleted={onChanged} />}
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="pd-mmx-sec">
      <h4>{title}</h4>
      {children}
    </section>
  )
}

/** 右侧选中节点详情：可编辑的标题/摘要、依据与关联、解释节点、增删与重新生成分支。 */
export function NodeDetail({ paperId, node, detail, onClose }: { paperId: string; node: MindNode | null; detail: MindNodeDetail | null; onClose?: () => void }) {
  const jump = useMindJump(paperId)
  const explain = useMindmapStore((s) => (node ? s.explain[node.id] : undefined))
  const treeStatus = useMindmapStore((s) => s.trees[s.view]?.status)
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [adding, setAdding] = useState<null | 'child' | 'sibling'>(null)
  const [newTitle, setNewTitle] = useState('')
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [question, setQuestion] = useState('')

  useEffect(() => {
    setTitle(node?.title ?? '')
    setSummary(node?.summary ?? '')
    setAdding(null)
    setNewTitle('')
    setQuestion('')
  }, [node?.id, node?.title, node?.summary])

  if (!node) return null // 工作区只在选中节点时才显示详情栏

  const isRoot = node.depth === 1
  const busy = treeStatus === 'running' || treeStatus === 'pending'
  const d = detail && detail.id === node.id ? detail : null
  const refresh = (): void => void useMindmapStore.getState().select(node.id, { focus: false })
  const commitTitle = (): void => {
    const t = title.trim()
    if (!t) return setTitle(node.title)
    if (t !== node.title) void useMindmapStore.getState().patchNode(node.id, { title: t })
  }
  const commitSummary = (): void => {
    if (summary.trim() !== node.summary) void useMindmapStore.getState().patchNode(node.id, { summary })
  }
  const submitAdd = async (): Promise<void> => {
    const t = newTitle.trim()
    if (!t || !adding) return
    const st = useMindmapStore.getState()
    const r = adding === 'child' ? await st.addChild(node.id, t) : await st.addSibling(node.id, t)
    if (r) {
      setAdding(null)
      setNewTitle('')
    }
  }

  return (
    <aside className="pd-mmx" aria-label="节点详情">
      <div className="pd-mmx-head">
        <span className="pd-aux pd-weak pd-ellipsis pd-mmx-path" title={d?.path.map((p) => p.title).join(' › ')}>
          {d && d.path.length > 0 ? d.path.map((p) => p.title).join(' › ') : '节点详情'}
        </span>
        {onClose && <IconButton icon={<X size={15} />} label="关闭详情 · Esc" size="sm" onClick={onClose} />}
      </div>
      <div className="pd-mmx-body">
        <div className="pd-row" style={{ gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          <Tag>{node.kind_label}</Tag>
          {node.origin === 'user' ? <Tag tone="outline">用户添加</Tag> : node.user_edited ? <Tag tone="outline">已编辑 · 重新生成时保留</Tag> : <Tag tone="outline">AI 生成</Tag>}
        </div>
        <input
          className="pd-mmx-title"
          value={title}
          maxLength={60}
          aria-label="节点标题"
          onChange={(e) => setTitle(e.target.value)}
          onBlur={commitTitle}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.nativeEvent.isComposing) e.currentTarget.blur()
            if (e.key === 'Escape') setTitle(node.title)
          }}
        />
        <TextArea
          className="pd-mmx-sum"
          value={summary}
          rows={3}
          placeholder="节点摘要"
          aria-label="节点摘要"
          onChange={setSummary}
          onBlur={commitSummary}
        />

        <Section title={node.evidence.derived ? '原文依据 · 汇总自下级节点' : '原文依据'}>
          {node.refs.length === 0 ? (
            <p className="pd-aux pd-weak" style={{ margin: 0 }}>
              {node.origin === 'user' ? '这是你添加的节点，没有关联原文依据。' : '没有可显示的原文依据，论文可能被重新解析过。'}
            </p>
          ) : (
            <div className="pd-mmx-evs">
              {node.refs.map((r) => (
                <div key={r.ord} className="pd-ev">
                  <div className="pd-ev-head">
                    <button type="button" className="pd-cite" onClick={() => jump({ page: r.page, bbox: r.bbox })}>
                      p.{r.page}
                    </button>
                    <span className="pd-aux pd-muted pd-ellipsis" style={{ flex: 1, minWidth: 0 }}>{r.section_title ?? '正文'}</span>
                    <Button size="sm" variant="ghost" icon={<CornerDownRight size={12} />} onClick={() => jump({ page: r.page, bbox: r.bbox })}>
                      跳转原文
                    </Button>
                  </div>
                  <blockquote className="pd-ev-quote">{truncate(r.quote, 220)}</blockquote>
                </div>
              ))}
            </div>
          )}
          {d && (d.figures.length > 0 || d.equations.length > 0) && (
            <div className="pd-ov-ev" style={{ marginTop: 8 }}>
              <span className="pd-aux pd-weak" style={{ marginRight: 4 }}>相关图表与公式</span>
              <EntityChips figures={d.figures} equations={d.equations} jump={jump} />
            </div>
          )}
        </Section>

        {d && d.notes.length > 0 && (
          <Section title="相关笔记">
            <ul className="pd-mmx-list">
              {d.notes.map((n) => (
                <li key={n.id}>
                  {truncate(n.text, 90)}
                  {n.page ? <span className="pd-aux pd-weak"> · 第 {n.page} 页</span> : null}
                </li>
              ))}
            </ul>
          </Section>
        )}

        {d && d.code.length > 0 && (
          <Section title="相关代码">
            <ul className="pd-mmx-list">
              {d.code.map((c) => (
                <li key={c.id}>
                  <code>{c.path}:{c.line_start}-{c.line_end}</code>
                  <span className="pd-aux pd-weak"> · {c.module ?? '论文模块'} · {c.status === 'confirmed' ? '已确认' : '待确认'}</span>
                </li>
              ))}
            </ul>
          </Section>
        )}

        <Section title="解释这个节点">
          <p className="pd-aux pd-weak" style={{ margin: '0 0 8px' }}>
            AI 会结合这个节点的原文依据作答，回答里的引用同样经过核实，并保存为一张研究卡片，阅读器的 AI 助手里也能看到。
          </p>
          <div className="pd-row" style={{ gap: 6 }}>
            <Input value={question} onChange={setQuestion} placeholder="也可以问一个具体问题" aria-label="对这个节点提问" onEnter={() => void useMindmapStore.getState().explainNode(node.id, question.trim() || undefined)} />
            {explain?.status === 'streaming' ? (
              <Button icon={<Square size={13} />} onClick={() => useMindmapStore.getState().stopExplain()}>停止</Button>
            ) : (
              <Button variant="primary" icon={<Sparkles size={14} />} onClick={() => void useMindmapStore.getState().explainNode(node.id, question.trim() || undefined)}>
                解释
              </Button>
            )}
          </div>
          {explain && explain.status !== 'done' && (
            <div className="pd-mmx-stream" aria-live="polite">
              {explain.status === 'error' ? (
                <div className="pd-field-error">{explain.error}</div>
              ) : explain.text ? (
                <Markdown text={explain.text} />
              ) : (
                <span className="pd-aux pd-weak">正在检索原文并生成…</span>
              )}
            </div>
          )}
          {explain?.status === 'done' && explain.card && <ExplainCardView card={explain.card} onDeleted={() => { useMindmapStore.setState((s) => { const next = { ...s.explain }; delete next[node.id]; return { explain: next } }); refresh() }} />}
          {d && d.cards.filter((c) => !(explain?.card && c.id === explain.card.id)).map((c) => <CardBriefRow key={c.id} brief={c} paperId={paperId} onChanged={refresh} />)}
        </Section>

        <Section title="编辑">
          <div className="pd-row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <Button size="sm" icon={<Plus size={13} />} onClick={() => { setAdding('child'); setNewTitle('') }}>添加子节点</Button>
            <Button size="sm" icon={<Plus size={13} />} disabled={isRoot} disabledReason="根节点没有同级节点" onClick={() => { setAdding('sibling'); setNewTitle('') }}>添加同级</Button>
            <Button size="sm" icon={<RefreshCw size={13} />} disabled={busy} disabledReason="正在生成，请稍后" onClick={() => setConfirmRegen(true)}>{isRoot ? '重新生成整张图' : '重新生成此分支'}</Button>
            <Button size="sm" variant="danger" icon={<Trash2 size={13} />} disabled={isRoot} disabledReason="根节点不能删除" onClick={() => setConfirmDelete(true)}>删除分支</Button>
          </div>
          {adding && (
            <div className="pd-row" style={{ gap: 6, marginTop: 8 }}>
              <Input
                value={newTitle}
                onChange={setNewTitle}
                placeholder={adding === 'child' ? '新子节点的标题' : '新同级节点的标题'}
                aria-label="新节点标题"
                maxLength={60}
                autoFocus
                onEnter={() => void submitAdd()}
                onKeyDown={(e) => e.key === 'Escape' && setAdding(null)}
              />
              <Button variant="primary" disabled={!newTitle.trim()} onClick={() => void submitAdd()}>添加</Button>
              <Button variant="ghost" onClick={() => setAdding(null)}>取消</Button>
            </div>
          )}
        </Section>
      </div>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="删除这个分支？"
        width={420}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDelete(false)}>取消</Button>
            <Button variant="danger" solid onClick={() => { setConfirmDelete(false); void useMindmapStore.getState().deleteNode(node.id) }}>删除</Button>
          </>
        }
      >
        <p style={{ margin: 0 }}>
          「{node.title}」{node.child_count > 0 ? `及其下的 ${node.child_count} 个子节点` : ''}会从思维导图里删除，不影响论文和已生成的研究卡片。重新生成思维导图时，AI 生成过的内容可能再次出现。
        </p>
      </Dialog>
      <Dialog
        open={confirmRegen}
        onClose={() => setConfirmRegen(false)}
        title={isRoot ? '重新生成整张图？' : '重新生成这个分支？'}
        width={440}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmRegen(false)}>取消</Button>
            <Button variant="primary" onClick={() => { setConfirmRegen(false); void useMindmapStore.getState().generate(useMindmapStore.getState().view, isRoot ? undefined : node.id) }}>重新生成</Button>
          </>
        }
      >
        <p style={{ margin: 0 }}>
          会调用云端模型，用新的结果取代{isRoot ? '整张图里' : '这个分支里'}你<b>没有改过</b>的 AI 节点；你修改过、新增的节点会原样保留。
        </p>
      </Dialog>
    </aside>
  )
}
