import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { Button, Select, Tag, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import type { KbItem, KbRelation, KbTopic } from '@/lib/types.discovery'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
import { EvidenceList } from './EvidenceList'
import { useKb } from './knowledgeStore'

/** 主题下的论文：手动加入/移出；一篇论文可以属于一个主题。 */
export function TopicPapers({ topic }: { topic: KbTopic }) {
  const navigate = useNavigate()
  const { items: library } = useLibraryPapers()
  const { loadTopics, version } = useKb()
  const [papers, setPapers] = useState<Array<{ id: string; title: string; year: number | null }>>([])
  const [pick, setPick] = useState('')

  const load = useCallback(async (): Promise<void> => {
    try {
      setPapers((await kbApi.topicPapers(topic.id)).items)
    } catch {
      setPapers([])
    }
  }, [topic.id])
  useEffect(() => {
    void load()
  }, [load, version])

  const add = async (): Promise<void> => {
    if (!pick) return
    try {
      await kbApi.addTopicPaper(topic.id, pick)
      setPick('')
      await Promise.all([load(), loadTopics()])
    } catch (err) {
      toast.error('无法加入论文', errorMessage(err))
    }
  }
  const remove = async (id: string): Promise<void> => {
    try {
      await kbApi.removeTopicPaper(topic.id, id)
      await Promise.all([load(), loadTopics()])
    } catch (err) {
      toast.error('无法移出论文', errorMessage(err))
    }
  }

  const candidates = library.filter((p) => p.role !== 'supplement' && !papers.some((x) => x.id === p.id))
  return (
    <details className="pd-kb-extra">
      <summary>主题下的论文 {papers.length}</summary>
      <ul className="pd-dc-list" style={{ marginTop: 'var(--sp-sm)' }}>
        {papers.map((p) => (
          <li key={p.id} className="pd-kb-paper-row">
            <button type="button" className="pd-dc-more" style={{ color: 'var(--text)', textAlign: 'left', fontSize: 'var(--fs-small)' }} onClick={() => navigate(`/paper/${p.id}`)}>
              {p.title}
              {p.year ? ` · ${p.year}` : ''}
            </button>
            <button type="button" className="pd-cmp-del" style={{ padding: '2px 4px' }} aria-label="移出主题" onClick={() => void remove(p.id)}>
              <X size={13} />
            </button>
          </li>
        ))}
        {papers.length === 0 && <li className="pd-dc-hint">还没有论文归到这个主题。</li>}
      </ul>
      <div className="pd-kb-addrow">
        <Select
          size="sm"
          value={pick}
          onChange={setPick}
          aria-label="选择要加入的论文"
          options={[{ value: '', label: '从文献库选择论文…' }, ...candidates.map((p) => ({ value: p.id, label: p.title.slice(0, 60) }))]}
        />
        <Button size="sm" variant="secondary" disabled={!pick} onClick={() => void add()}>
          加入
        </Button>
      </div>
    </details>
  )
}

const REL_FALLBACK = '相关'

/** 主题内的关系：AI 提出的是“待确认”，必须附原文证据；你确认后才成为正式关系。 */
export function TopicRelations({ topic, items }: { topic: KbTopic; items: KbItem[] }) {
  const { version, bump } = useKb()
  const { byId } = useLibraryPapers()
  const [rels, setRels] = useState<KbRelation[]>([])
  const [suggesting, setSuggesting] = useState(false)
  const [open, setOpen] = useState<string | null>(null)

  const load = useCallback(async (): Promise<void> => {
    try {
      setRels((await kbApi.relations({ topic_id: topic.id })).items)
    } catch {
      setRels([])
    }
  }, [topic.id])
  useEffect(() => {
    void load()
  }, [load, version])

  const nameOf = (kind: 'item' | 'paper', id: string): string =>
    kind === 'item' ? (items.find((i) => i.id === id)?.title ?? '已删除的条目') : (byId.get(id)?.title ?? '论文')

  const suggest = async (): Promise<void> => {
    setSuggesting(true)
    try {
      const r = await kbApi.suggestRelations(topic.id)
      await load()
      if (r.suggested.length === 0) toast.info('没有新的关系建议', '条目太少，或模型没有找到有原文证据支撑的关系。')
    } catch (err) {
      toast.error('AI 建议关系失败', errorMessage(err))
    } finally {
      setSuggesting(false)
    }
  }
  const confirm = async (id: string): Promise<void> => {
    try {
      await kbApi.confirmRelation(id)
      await load()
      bump()
    } catch (err) {
      toast.error('无法确认', errorMessage(err))
    }
  }
  const remove = async (id: string): Promise<void> => {
    try {
      await kbApi.deleteRelation(id)
      await load()
      bump()
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }

  return (
    <details className="pd-kb-extra">
      <summary>条目之间的关系 {rels.length}</summary>
      <div className="pd-dc-actions" style={{ marginTop: 'var(--sp-sm)' }}>
        <Button size="sm" variant="secondary" loading={suggesting} disabled={items.length < 2} disabledReason="至少需要 2 个条目" onClick={() => void suggest()}>
          让 AI 建议关系
        </Button>
        <span className="pd-dc-hint" style={{ margin: 0 }}>AI 只提出候选，每条都附原文证据，确认后才生效。</span>
      </div>
      <ul className="pd-dc-list" style={{ marginTop: 'var(--sp-md)' }}>
        {rels.map((r) => (
          <li key={r.id}>
            <div>
              {nameOf(r.from_kind, r.from_id)} <strong>→ {r.relation_label || REL_FALLBACK} →</strong> {nameOf(r.to_kind, r.to_id)}{' '}
              {r.status === 'suggested' ? <Tag>待确认{r.ai_inferred ? ' · AI 推断' : ''}</Tag> : <Tag tone="success">已确认</Tag>}
            </div>
            {r.description && <div className="pd-dc-sub">{r.description}</div>}
            <div className="pd-dc-actions" style={{ marginTop: 4 }}>
              {r.status === 'suggested' && (
                <Button size="sm" variant="secondary" onClick={() => void confirm(r.id)}>
                  确认
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => void remove(r.id)}>
                {r.status === 'suggested' ? '忽略' : '删除'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setOpen(open === r.id ? null : r.id)}>
                {open === r.id ? '收起证据' : `查看证据 ${r.evidence.length}`}
              </Button>
            </div>
            {open === r.id && <EvidenceList evidence={r.evidence} empty="这条关系没有证据。" />}
          </li>
        ))}
        {rels.length === 0 && <li className="pd-dc-hint">还没有关系。</li>}
      </ul>
    </details>
  )
}
