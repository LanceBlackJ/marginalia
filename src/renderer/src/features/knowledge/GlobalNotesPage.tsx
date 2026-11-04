import { useEffect, useMemo, useState } from 'react'
import { NoteBody } from '@/features/notes/NoteBody'
import { Clamp } from '@/features/notes/Clamp'
import { useNavigate } from 'react-router-dom'
import { Search } from 'lucide-react'
import { Button, EmptyState, Input, Select, Skeleton } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { notesApi } from '@/lib/api.discovery'
import type { GlobalNote } from '@/lib/types.discovery'
import { openEvidenceInReader } from '@/features/shared/evidenceNav'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
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
  const { items: library } = useLibraryPapers()
  const [q, setQ] = useState('')
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
          <Select
            value={paperId}
            onChange={setPaperId}
            aria-label="按论文筛选"
            options={[{ value: '', label: '全部论文' }, ...library.filter((p) => p.role !== 'supplement').map((p) => ({ value: p.id, label: p.title.slice(0, 70) }))]}
            style={{ maxWidth: 420 }}
          />
          <ExportMenu paperIds={paperId ? [paperId] : undefined} />
        </div>

        {error && <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 'var(--sp-lg)' }}>{error}</p>}
        {loading && notes.length === 0 && (
          <div style={{ marginTop: 'var(--sp-xl)', display: 'flex', flexDirection: 'column', gap: 'var(--sp-md)' }}>
            <Skeleton height={16} width="50%" />
            <Skeleton height={12} width="80%" />
          </div>
        )}
        {!loading && notes.length === 0 && !error && (
          <EmptyState title={q || paperId ? '没有匹配的笔记' : '还没有笔记'} description={q || paperId ? undefined : '在阅读器里选中文字并添加笔记，或在右侧“笔记”标签中记录，这里就会出现。'} />
        )}

        <p className="pd-dc-hint">{total > 0 && `共 ${total} 条笔记，来自 ${groups.length} 篇论文${total > notes.length ? `，已显示 ${notes.length} 条` : ''}`}</p>
        {groups.map((g) => (
          <section key={g.paperId} className="pd-dc-group">
            <div className="pd-dc-group-head">
              <h2>
                <button type="button" className="pd-dc-title-btn" onClick={() => navigate(`/paper/${g.paperId}`)}>
                  {g.title}
                </button>
              </h2>
              <span>{[g.year, `${g.notes.length} 条`].filter(Boolean).join(' · ')}</span>
            </div>
            <ul className="pd-notes-list pd-sticky-grid">
              {g.notes.map((n) => (
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
                    <button type="button" className="pd-dc-more" onClick={() => openEvidenceInReader(navigate, n.paper_id, { page: n.page })}>
                      在原文中查看
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
        ))}
        {total > notes.length && (
          <div style={{ marginTop: 'var(--sp-xl)' }}>
            <Button variant="secondary" onClick={() => setLimit((l) => l + 100)}>
              显示更多
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
