import { useEffect, useRef } from 'react'
import { ChevronDown, ChevronUp, Search, X } from 'lucide-react'
import { IconButton, Spinner } from '@/components/ui'
import { useDebounced } from '@/lib/hooks'
import type { SearchHit } from '@/lib/types'
import { useReaderStore } from './readerStore'
import { useSearchStore } from './searchStore'

function Snippet({ hit }: { hit: SearchHit }) {
  const parts: Array<{ text: string; mark: boolean }> = []
  let last = 0
  const ranges = [...hit.highlights].sort((a, b) => a[0] - b[0])
  for (const [s, e] of ranges) {
    if (s < last || e > hit.snippet.length) continue
    if (s > last) parts.push({ text: hit.snippet.slice(last, s), mark: false })
    parts.push({ text: hit.snippet.slice(s, e), mark: true })
    last = e
  }
  if (last < hit.snippet.length) parts.push({ text: hit.snippet.slice(last), mark: false })
  return (
    <span>
      {parts.map((p, i) => (p.mark ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
    </span>
  )
}

export function SearchPanel() {
  const open = useReaderStore((s) => s.searchOpen)
  const paperId = useReaderStore((s) => s.paperId)
  const setOpen = useReaderStore((s) => s.setSearchOpen)
  const { query, hits, active, loading, error, searched, setQuery, run, go, step, clear } = useSearchStore()
  const inputRef = useRef<HTMLInputElement>(null)
  const debounced = useDebounced(query, 300)

  useEffect(() => {
    if (open) {
      inputRef.current?.focus()
      inputRef.current?.select()
    } else {
      clear()
    }
  }, [open, clear])

  useEffect(() => {
    if (open && paperId) void run(paperId, debounced)
  }, [debounced, open, paperId, run])

  // 活动结果滚动到可见
  const listRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>('[data-active="true"]')?.scrollIntoView({ block: 'nearest' })
  }, [active])

  if (!open) return null
  return (
    <div className="pd-search" role="search">
      <div className="pd-search-head">
        <div style={{ flex: 1, minWidth: 0 }} className="pd-input-wrap">
          <span className="pd-input-icon">{loading ? <Spinner size={13} /> : <Search size={14} />}</span>
          <input
            ref={inputRef}
            className="pd-input pd-input--with-icon"
            value={query}
            placeholder="在论文中搜索…"
            aria-label="在论文中搜索"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                if (paperId && query.trim() !== debounced.trim()) void run(paperId, query)
                else step(e.shiftKey ? -1 : 1)
              } else if (e.key === 'Escape') {
                e.stopPropagation()
                setOpen(false)
              }
            }}
          />
        </div>
        <span className="pd-search-count">{hits.length > 0 ? `${active + 1} / ${hits.length}` : searched && !loading ? '无结果' : ''}</span>
        <IconButton icon={<ChevronUp size={16} />} label="上一个" size="sm" disabled={hits.length === 0} onClick={() => step(-1)} />
        <IconButton icon={<ChevronDown size={16} />} label="下一个" size="sm" disabled={hits.length === 0} onClick={() => step(1)} />
        <IconButton icon={<X size={16} />} label="关闭搜索" size="sm" onClick={() => setOpen(false)} />
      </div>
      {error && <div className="pd-field-error" style={{ padding: '0 12px 8px' }}>{error}</div>}
      {hits.length > 0 && (
        <div className="pd-search-list" ref={listRef}>
          {hits.map((h, i) => (
            <button key={`${h.ord}-${i}`} type="button" className="pd-search-item" data-active={i === active} onClick={() => go(i)}>
              <div className="pd-aux pd-weak" style={{ marginBottom: 2 }}>
                第 {h.page} 页{h.section_title ? ` · ${h.section_title}` : ''}
              </div>
              <Snippet hit={h} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
