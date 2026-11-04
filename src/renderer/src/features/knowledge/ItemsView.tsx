import { useEffect, useState } from 'react'
import { MoreHorizontal, Pencil, Plus, Search, Trash2 } from 'lucide-react'
import { Button, Dialog, EmptyState, Field, IconButton, Input, Menu, Select, Skeleton, Tag, TextArea, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import { kbGraphApi } from '@/lib/api.export'
import type { ItemKind, KbItem } from '@/lib/types.discovery'
import { ExportMenu } from './ExportMenu'
import { ItemDetail } from './ItemDetail'
import { TopicPane } from './TopicPane'
import { TopicPapers, TopicRelations } from './TopicExtras'
import { useKb } from './knowledgeStore'
import { KIND_OPTIONS, kindLabel } from './kbUtils'
import { allSelected, bulkSummary, pruneSelected, selectRange, toggleSelected } from './kbBulk'

/** 三栏：主题 | 条目 | 详情（含原文证据，可回到阅读器）。 */
export function ItemsView() {
  const { topics, scope, version, bump, loadTopics } = useKb()
  const [items, setItems] = useState<KbItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [creating, setCreating] = useState(false)
  const [nt, setNt] = useState({ title: '', content: '', kind: 'other' as ItemKind })
  const [busy, setBusy] = useState(false)
  const [renaming, setRenaming] = useState<{ name: string; description: string } | null>(null)
  const [confirmDel, setConfirmDel] = useState(false)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [anchor, setAnchor] = useState<string | null>(null)
  const [bulkTopic, setBulkTopic] = useState('')
  const [bulkBusy, setBulkBusy] = useState(false)
  const [confirmBulkDel, setConfirmBulkDel] = useState(false)

  const topic = topics.find((t) => t.id === scope) ?? null

  useEffect(() => {
    let alive = true
    setLoading(true)
    const t = window.setTimeout(() => {
      kbApi
        .items({ topic_id: topic ? topic.id : undefined, unassigned: scope === 'unassigned' ? true : undefined, q: q.trim() || undefined })
        .then((r) => {
          if (!alive) return
          setItems(r.items)
          setError(null)
          setLoading(false)
        })
        .catch((err) => {
          if (!alive) return
          setError(errorMessage(err))
          setLoading(false)
        })
    }, q ? 250 : 0)
    return () => {
      alive = false
      window.clearTimeout(t)
    }
  }, [scope, topic?.id, q, version])

  // 列表刷新后，去掉已经不在列表里的多选项
  useEffect(() => {
    setPicked((p) => pruneSelected(p, items.map((i) => i.id)))
  }, [items])

  const order = items.map((i) => i.id)
  const pick = (id: string, shift: boolean): void => {
    setPicked((p) => (shift ? selectRange(order, p, anchor, id) : toggleSelected(p, id)))
    setAnchor(id)
  }

  const bulk = async (action: 'add_topic' | 'remove_topic' | 'delete', topicId?: string): Promise<void> => {
    setBulkBusy(true)
    try {
      const r = await kbGraphApi.bulkItems({ item_ids: [...picked], action, topic_id: topicId })
      toast.success('批量操作完成', bulkSummary(action, r))
      setPicked(new Set())
      setConfirmBulkDel(false)
      bump()
      await loadTopics()
    } catch (err) {
      toast.error('批量操作失败', errorMessage(err))
    } finally {
      setBulkBusy(false)
    }
  }

  // 所选条目不在当前列表中时取消选择
  useEffect(() => {
    if (selected && !loading && !items.some((i) => i.id === selected)) setSelected(null)
  }, [items, loading, selected])

  const scopeName = scope === 'all' ? '全部条目' : scope === 'unassigned' ? '未归类' : (topic?.name ?? '')

  const create = async (): Promise<void> => {
    setBusy(true)
    try {
      const it = await kbApi.createItem({ title: nt.title.trim(), content: nt.content, kind: nt.kind, topic_ids: topic ? [topic.id] : [] })
      setCreating(false)
      setNt({ title: '', content: '', kind: 'other' })
      bump()
      await loadTopics()
      setSelected(it.id)
    } catch (err) {
      toast.error('无法创建条目', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const saveTopic = async (): Promise<void> => {
    if (!topic || !renaming) return
    try {
      await kbApi.updateTopic(topic.id, { name: renaming.name.trim(), description: renaming.description })
      setRenaming(null)
      await loadTopics()
    } catch (err) {
      toast.error('无法修改主题', errorMessage(err))
    }
  }
  const deleteTopic = async (): Promise<void> => {
    if (!topic) return
    try {
      await kbApi.deleteTopic(topic.id)
      setConfirmDel(false)
      await loadTopics()
      bump()
    } catch (err) {
      toast.error('无法删除主题', errorMessage(err))
    }
  }

  return (
    <div className="pd-kb-grid">
      <TopicPane />

      <section className="pd-kb-items">
        <div className="pd-kb-pane-head">
          <div style={{ minWidth: 0 }}>
            <h2 className="pd-kb-title">{scopeName}</h2>
            {topic?.description && <p className="pd-dc-sub" style={{ marginTop: 2 }}>{topic.description}</p>}
          </div>
          <div className="pd-dc-actions" style={{ marginTop: 0 }}>
            <ExportMenu topicId={topic?.id} />
            <Button size="sm" variant="primary" icon={<Plus size={14} />} onClick={() => setCreating(true)}>
              新条目
            </Button>
            {topic && (
              <Menu
                align="end"
                trigger={({ toggle, open }) => <IconButton icon={<MoreHorizontal size={16} />} label="主题操作" size="sm" selected={open} onClick={toggle} />}
                items={[
                  { key: 'rename', label: '重命名 / 编辑说明', icon: <Pencil size={15} />, onSelect: () => setRenaming({ name: topic.name, description: topic.description }) },
                  { key: 'delete', label: '删除主题', icon: <Trash2 size={15} />, onSelect: () => setConfirmDel(true) }
                ]}
              />
            )}
          </div>
        </div>
        <div style={{ marginTop: 'var(--sp-md)' }}>
          <Input value={q} onChange={setQ} clearable icon={<Search size={15} />} placeholder="搜索条目标题与内容…" aria-label="搜索知识条目" />
        </div>
        {topic && (
          <details className="pd-kb-extra pd-kb-topicinfo">
            <summary>主题里的论文与条目关系</summary>
            <TopicPapers topic={topic} />
            <TopicRelations topic={topic} items={items} />
          </details>
        )}

        {error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-md)' }}>{error}</p>}
        {loading && items.length === 0 && (
          <div style={{ marginTop: 'var(--sp-lg)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-md)' }}>
            <Skeleton height={16} width="70%" />
            <Skeleton height={12} width="90%" />
          </div>
        )}
        {!loading && items.length === 0 && !error && (
          <EmptyState
            title={q ? '没有匹配的条目' : '这里还没有知识条目'}
            description={q ? undefined : '到“待确认候选”从论文中提取候选并采纳，或点右上角“新条目”手动添加。'}
          />
        )}
        {items.length > 0 && (
          <div className="pd-dc-sub" style={{ marginTop: 'var(--sp-md)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <label className="pd-export-check">
              <input
                type="checkbox"
                checked={allSelected(picked, order)}
                onChange={(e) => setPicked(e.target.checked ? new Set(order) : new Set())}
                aria-label="全选当前列表"
              />
              全选
            </label>
            <span title="可勾选多个条目批量处理；按住 Shift 点击可选一段">批量处理</span>
          </div>
        )}
        {picked.size > 0 && (
          <div className="pd-kb-bulk" role="toolbar" aria-label="批量操作">
            <strong>已选 {picked.size} 项</strong>
            <Select
              size="sm"
              aria-label="选择主题"
              value={bulkTopic}
              onChange={setBulkTopic}
              options={[{ value: '', label: '选择主题…' }, ...topics.map((t) => ({ value: t.id, label: t.name }))]}
              style={{ width: 150 }}
            />
            <Button size="sm" disabled={!bulkTopic} loading={bulkBusy} onClick={() => void bulk('add_topic', bulkTopic)}>
              加入主题
            </Button>
            <Button size="sm" disabled={!bulkTopic} loading={bulkBusy} onClick={() => void bulk('remove_topic', bulkTopic)}>
              移出主题
            </Button>
            <Button size="sm" variant="danger" onClick={() => setConfirmBulkDel(true)}>
              删除…
            </Button>
            <span className="pd-spacer" />
            <Button size="sm" variant="ghost" onClick={() => setPicked(new Set())}>
              取消选择
            </Button>
          </div>
        )}
        <ul className="pd-kb-list">
          {items.map((it) => (
            <li key={it.id} className="pd-kb-itemrow">
              <input
                type="checkbox"
                className="pd-kb-pick"
                checked={picked.has(it.id)}
                aria-label={`选择：${it.title}`}
                onChange={() => undefined}
                onClick={(e) => pick(it.id, e.shiftKey)}
              />
              <button type="button" className={`pd-kb-itembtn${selected === it.id ? ' is-active' : ''}`} onClick={() => setSelected(it.id)}>
                <span className="pd-kb-item-title">{it.title}</span>
                <span className="pd-kb-item-body">{it.content}</span>
                <span className="pd-dc-sub" style={{ margin: 0 }}>
                  <Tag>{kindLabel(it.kind)}</Tag>
                  {it.source_paper && <span>{it.source_paper.title.slice(0, 36)}</span>}
                  <span>{it.evidence.length > 0 ? `${it.evidence.length} 条证据` : '无证据'}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      <section className="pd-kb-detail-pane">
        {selected ? <ItemDetail key={selected} itemId={selected} onDeleted={() => setSelected(null)} /> : <p className="pd-dc-hint">选择一个条目查看详情与原文证据。</p>}
      </section>

      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="新建知识条目"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              取消
            </Button>
            <Button variant="primary" loading={busy} disabled={!nt.title.trim()} onClick={() => void create()}>
              创建
            </Button>
          </>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-lg)' }}>
          <Field label="标题">{(id) => <Input id={id} value={nt.title} onChange={(v) => setNt({ ...nt, title: v })} autoFocus />}</Field>
          <Field label="类型">
            {(id) => <Select id={id} value={nt.kind} onChange={(v) => setNt({ ...nt, kind: v as ItemKind })} options={KIND_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} />}
          </Field>
          <Field label="内容" hint="手动创建的条目没有原文证据；想带证据请从“待确认候选”中采纳。">
            {(id) => <TextArea id={id} value={nt.content} onChange={(v) => setNt({ ...nt, content: v })} rows={5} />}
          </Field>
          {topic && <p className="pd-dc-hint">会放入主题“{topic.name}”。</p>}
        </div>
      </Dialog>

      <Dialog
        open={!!renaming}
        onClose={() => setRenaming(null)}
        title="编辑主题"
        footer={
          <>
            <Button variant="ghost" onClick={() => setRenaming(null)}>
              取消
            </Button>
            <Button variant="primary" disabled={!renaming?.name.trim()} onClick={() => void saveTopic()}>
              保存
            </Button>
          </>
        }
      >
        {renaming && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-lg)' }}>
            <Field label="主题名称">{(id) => <Input id={id} value={renaming.name} onChange={(v) => setRenaming({ ...renaming, name: v })} />}</Field>
            <Field label="说明">{(id) => <TextArea id={id} value={renaming.description} onChange={(v) => setRenaming({ ...renaming, description: v })} rows={3} />}</Field>
          </div>
        )}
      </Dialog>

      <Dialog
        open={confirmBulkDel}
        onClose={() => !bulkBusy && setConfirmBulkDel(false)}
        title={`删除选中的 ${picked.size} 个条目？`}
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmBulkDel(false)} disabled={bulkBusy}>
              取消
            </Button>
            <Button variant="danger" solid loading={bulkBusy} onClick={() => void bulk('delete')}>
              删除
            </Button>
          </>
        }
      >
        <p>这些条目和它们的关系会被删除，无法撤销；条目引用的论文、笔记和卡片本身不受影响。</p>
      </Dialog>

      <Dialog
        open={confirmDel}
        onClose={() => setConfirmDel(false)}
        title="删除这个主题？"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDel(false)}>
              取消
            </Button>
            <Button variant="danger" solid onClick={() => void deleteTopic()}>
              删除
            </Button>
          </>
        }
      >
        <p>只删除主题本身，里面的知识条目会变为“未归类”，不会被删除。</p>
      </Dialog>
    </div>
  )
}
