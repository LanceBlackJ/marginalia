import { useEffect, useMemo, useState } from 'react'
import { NoteBody } from '@/features/notes/NoteBody'
import { Clamp } from '@/features/notes/Clamp'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, BookOpen, Search } from 'lucide-react'
import { Button, EmptyState, Input, Skeleton } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { notesApi } from '@/lib/api.discovery'
import type { GlobalNote } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { ExportMenu } from './ExportMenu'
import '@/features/discovery/discovery.css'
import '@/features/compare/compare.css'
import './knowledge.css'

function fmt(iso: string): string {
  const d = new Date(iso.endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString('zh-CN', { year: 'numeric', month: 'numeric', day: 'numeric' })
}

/** 全局阅读笔记：汇总所有论文里的笔记，可搜索、按论文筛选、跳回原文，并导出 Markdown。 */
export function GlobalNotesPage() {
  const navigate = useNavigate()
  const [q, setQ] = useState('')
  /** 空值显示“有笔记的文章”；选中后只显示这篇文章的笔记。 */
  const [paperId, setPaperId] = useState('')
  const [notes, setNotes] = useState<GlobalNote[]>([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(100)

  useEffect(() => {
    let alive = true
    setLoading(true)
    const t = window.setTimeout(() => {
      notesApi
        .all({ q: q.trim() || undefined, paper_id: paperId || undefined, limit })
        .then((r) => {
          if (!alive) return
          setNotes(r.items)
          setTotal(r.total)
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
  }, [q, paperId, limit])

  const groups = useMemo(() => {
    const map = new Map<string, { paperId: string; title: string; year: number | null; notes: GlobalNote[] }>()
    for (const n of notes) {
      const g = map.get(n.paper_id) ?? { paperId: n.paper_id, title: n.paper_title, year: n.paper_year, notes: [] }
      g.notes.push(n)
      map.set(n.paper_id, g)
    }
    return [...map.values()]
  }, [notes])

  return (
    <div className="pd-page">
      <div className="pd-page-inner">
        <h1 className="pd-page-title">阅读笔记</h1>
        <p className="pd-page-subtitle">汇总所有论文里的笔记。笔记本身仍在阅读器右侧的“笔记”标签中编辑；这里用于检索、回到原文和导出。</p>

        <div className="pd-dc-options" style={{ marginTop: 'var(--sp-xl)' }}>
          <div style={{ width: 360, maxWidth: '100%' }}>
            <Input value={q} onChange={setQ} clearable icon={<Search size={15} />} placeholder="搜索笔记与引用的原文…" aria-label="搜索笔记" />
          </div>
          <ExportMenu paperIds={paperId ? [paperId] : undefined} />
        </div>

        {error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-lg)' }}>{error}</p>}
        {loading && notes.length === 0 && (
          <div style={{ marginTop: 'var(--sp-xl)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-md)' }}>
            <Skeleton height={16} width="50%" />
            <Skeleton height={12} width="80%" />
          </div>
        )}
        {!loading && notes.length === 0 && !error && !paperId && (
          <EmptyState title={q || paperId ? '没有匹配的笔记' : '还没有笔记'} description={q || paperId ? undefined : '在阅读器里选中文字并添加笔记，或在右侧“笔记”标签中记录，这里就会出现。'} />
        )}

        {paperId ? (
          <PaperNotesView
            group={groups[0] ?? null}
            notes={notes}
            total={total}
            loading={loading}
            onBack={() => {
              setPaperId('')
              setLimit(100)
            }}
            onOpenPaper={() => navigate(`/paper/${paperId}`)}
            onOpenEvidence={(n) => openEvidenceInReader(navigate, n.paper_id, { page: n.page })}
          />
        ) : (
          <ArticleNotesList groups={groups} total={total} loaded={notes.length} onSelect={setPaperId} />
        )}
        {total > notes.length && (
          <div style={{ marginTop: 'var(--sp-xl)' }}>
            <Button variant="secondary" onClick={() => setLimit((l) => l + 100)}>
              {paperId ? '显示更多笔记' : '显示更多文章'}
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

type NoteGroup = { paperId: string; title: string; year: number | null; notes: GlobalNote[] }

function ArticleNotesList({ groups, total, loaded, onSelect }: { groups: NoteGroup[]; total: number; loaded: number; onSelect: (paperId: string) => void }) {
  if (groups.length === 0) return null
  return (
    <section className="pd-notes-article-browser" aria-label="按文章浏览阅读笔记">
      <div className="pd-notes-browser-head">
        <div>
          <h2>按文章浏览</h2>
          <p>{total > 0 ? `共 ${total} 条笔记，来自 ${groups.length} 篇文章${total > loaded ? `，已载入 ${loaded} 条` : ''}` : '选择一篇文章查看它的笔记'}</p>
        </div>
      </div>
      <div className="pd-notes-article-list">
        {groups.map((g) => (
          <button key={g.paperId} type="button" className="pd-notes-article" onClick={() => onSelect(g.paperId)}>
            <span className="pd-notes-article-main">
              <BookOpen size={16} />
              <span className="pd-notes-article-title" title={g.title}>{g.title}</span>
            </span>
            <span className="pd-notes-article-meta">{[g.year, `${g.notes.length} 条笔记`].filter(Boolean).join(' · ')}</span>
          </button>
        ))}
      </div>
    </section>
  )
}

function PaperNotesView({
  group,
  notes,
  total,
  loading,
  onBack,
  onOpenPaper,
  onOpenEvidence
}: {
  group: NoteGroup | null
  notes: GlobalNote[]
  total: number
  loading: boolean
  onBack: () => void
  onOpenPaper: () => void
  onOpenEvidence: (note: GlobalNote) => void
}) {
  const title = group?.title ?? '这篇文章'
  return (
    <section className="pd-notes-paper-view" aria-label={`${title}的阅读笔记`}>
      <button type="button" className="pd-notes-back" onClick={onBack}>
        <ArrowLeft size={14} /> 全部文章
      </button>
      <div className="pd-notes-paper-head">
        <div>
          <h2 title={title}>{title}</h2>
          <p>{[group?.year, `${total} 条笔记`].filter(Boolean).join(' · ')}</p>
        </div>
        <Button size="sm" variant="secondary" icon={<BookOpen size={14} />} onClick={onOpenPaper}>
          打开文章
        </Button>
      </div>
      {loading && notes.length === 0 ? (
        <div className="pd-notes-paper-loading"><Skeleton height={18} width="50%" /><Skeleton height={12} width="80%" /></div>
      ) : notes.length === 0 ? (
        <EmptyState title="这篇文章没有匹配的笔记" description="可以清除搜索条件，或返回全部文章选择另一篇。" />
      ) : (
        <ul className="pd-notes-list pd-sticky-grid">
          {notes.map((n) => (
            <li key={n.id} className="pd-sticky">
              {n.quote && (
                <Clamp lines={3}>
                  <blockquote className="pd-cmp-quote">{n.quote}</blockquote>
                </Clamp>
              )}
              {n.text && (
                <Clamp lines={8}>
                  <NoteBody text={n.text} />
                </Clamp>
              )}
              <div className="pd-dc-sub">
                {n.page && <span>第 {n.page} 页</span>}
                <span>{fmt(n.updated_at)}</span>
                <button type="button" className="pd-dc-more" onClick={() => onOpenEvidence(n)}>
                  在原文中查看
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
