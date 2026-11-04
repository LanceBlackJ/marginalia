import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Dialog, Input, Select, Tag, TextArea, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import type { ItemKind, KbItem } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { EvidenceList } from './EvidenceList'
import { useKb } from './knowledgeStore'
import { KIND_OPTIONS } from './kbUtils'

/** 右栏：条目详情与编辑。证据列表点击可回到阅读器里的原文位置。 */
export function ItemDetail({ itemId, onDeleted }: { itemId: string; onDeleted: () => void }) {
  const navigate = useNavigate()
  const { topics, bump, loadTopics } = useKb()
  const [item, setItem] = useState<KbItem | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [content, setContent] = useState('')
  const [kind, setKind] = useState<ItemKind>('other')
  const [topicIds, setTopicIds] = useState<string[]>([])
  const [saving, setSaving] = useState(false)
  const [confirmDel, setConfirmDel] = useState(false)

  useEffect(() => {
    let alive = true
    setItem(null)
    setError(null)
    kbApi
      .item(itemId)
      .then((it) => {
        if (!alive) return
        setItem(it)
        setTitle(it.title)
        setContent(it.content)
        setKind(it.kind)
        setTopicIds(it.topic_ids)
      })
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [itemId])

  if (error) return <p className="pd-status-text pd-status-text--danger">{error}</p>
  if (!item) return <p className="pd-dc-hint">加载中…</p>

  const dirty = title !== item.title || content !== item.content || kind !== item.kind || topicIds.join() !== item.topic_ids.join()

  const save = async (): Promise<void> => {
    setSaving(true)
    try {
      const next = await kbApi.updateItem(item.id, { title: title.trim(), content, kind, topic_ids: topicIds })
      setItem({ ...item, ...next })
      bump()
      await loadTopics()
      toast.success('已保存')
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    } finally {
      setSaving(false)
    }
  }
  const del = async (): Promise<void> => {
    try {
      await kbApi.deleteItem(item.id)
      setConfirmDel(false)
      bump()
      await loadTopics()
      onDeleted()
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }
  const toggleTopic = (id: string): void => setTopicIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]))

  return (
    <div className="pd-kb-detail">
      <Input value={title} onChange={setTitle} aria-label="条目标题" />
      <div className="pd-dc-meta">
        <Select
          size="sm"
          value={kind}
          onChange={(v) => setKind(v as ItemKind)}
          options={KIND_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
          aria-label="条目类型"
        />
        <Tag>{item.origin === 'candidate' ? '来自 AI 候选，已确认' : '手动创建'}</Tag>
      </div>
      <TextArea value={content} onChange={setContent} rows={8} aria-label="条目内容" />
      <div className="pd-dc-actions">
        <Button size="sm" variant="primary" loading={saving} disabled={!dirty || !title.trim()} onClick={() => void save()}>
          保存修改
        </Button>
        <Button size="sm" variant="ghost" disabled={!dirty} onClick={() => { setTitle(item.title); setContent(item.content); setKind(item.kind); setTopicIds(item.topic_ids) }}>
          还原
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setConfirmDel(true)}>
          删除
        </Button>
      </div>

      <h3 className="pd-dc-h">所属主题</h3>
      {topics.length === 0 ? (
        <p className="pd-dc-hint">还没有主题。</p>
      ) : (
        <div className="pd-kb-chips">
          {topics.map((t) => (
            <label key={t.id} className={`pd-kb-chip${topicIds.includes(t.id) ? ' is-on' : ''}`}>
              <input type="checkbox" checked={topicIds.includes(t.id)} onChange={() => toggleTopic(t.id)} />
              {t.name}
            </label>
          ))}
        </div>
      )}

      <h3 className="pd-dc-h">来源论文</h3>
      {item.source_paper ? (
        <p style={{ margin: 0, fontSize: 'var(--fs-small)' }}>
          <button type="button" className="pd-dc-more" style={{ color: 'var(--text)', textAlign: 'left', fontSize: 'var(--fs-small)' }} onClick={() => navigate(`/paper/${item.source_paper!.id}`)}>
            {item.source_paper.title}
            {item.source_paper.year ? ` · ${item.source_paper.year}` : ''}
          </button>
        </p>
      ) : (
        <p className="pd-dc-hint">没有关联论文。</p>
      )}

      <h3 className="pd-dc-h">原文证据</h3>
      <EvidenceList evidence={item.evidence} empty="这个条目没有原文证据。" />

      {(item.relations?.length ?? 0) > 0 && (
        <>
          <h3 className="pd-dc-h">关系</h3>
          <ul className="pd-dc-list">
            {item.relations!.map((r) => (
              <li key={r.id}>
                {r.relation_label} <Tag>{r.status === 'confirmed' ? '已确认' : '待确认'}</Tag>
                {r.description && <div className="pd-dc-sub">{r.description}</div>}
              </li>
            ))}
          </ul>
        </>
      )}

      {(item.cards?.length ?? 0) > 0 && (
        <>
          <h3 className="pd-dc-h">关联的学习卡片</h3>
          <ul className="pd-dc-list">
            {item.cards!.map((c) => (
              <li key={c.id}>
                <button
                  type="button"
                  className="pd-dc-more"
                  style={{ color: 'var(--text)', textAlign: 'left', fontSize: 'var(--fs-small)' }}
                  onClick={() => openEvidenceInReader(navigate, c.paper_id, {})}
                >
                  {c.question || c.object_label}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {(item.notes?.length ?? 0) > 0 && (
        <>
          <h3 className="pd-dc-h">关联的笔记</h3>
          <ul className="pd-dc-list">
            {item.notes!.map((n) => (
              <li key={n.id}>
                <div style={{ whiteSpace: 'pre-wrap' }}>{n.text}</div>
                {n.page && <div className="pd-dc-sub">第 {n.page} 页</div>}
              </li>
            ))}
          </ul>
        </>
      )}

      <Dialog
        open={confirmDel}
        onClose={() => setConfirmDel(false)}
        title="删除这个知识条目？"
        footer={
          <>
            <Button variant="ghost" onClick={() => setConfirmDel(false)}>
              取消
            </Button>
            <Button variant="danger" solid onClick={() => void del()}>
              删除
            </Button>
          </>
        }
      >
        <p>只删除知识库里的这个条目，不会影响原论文、笔记或学习卡片。</p>
      </Dialog>
    </div>
  )
}
