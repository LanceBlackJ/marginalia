import { useEffect, useState, type ReactElement } from 'react'
import { Bookmark, X } from 'lucide-react'
import { Button, Input, Segmented, Select } from '@/components/ui'
import type { PaperFacets, PaperFilters, ParseState, ReadState } from '@/lib/types.library'
import { FLAG_FILTERS, filterChips, normalizeFilters, PARSE_OPTIONS, READ_OPTIONS, TEXT_FILTERS, type ChipLookup } from './filterModel'

/** 文字类输入框：打字时延迟 350ms 才真正触发筛选，避免每敲一个字就查一次。 */
function DebouncedInput({ value, onCommit, ...rest }: { value: string; onCommit: (v: string) => void; placeholder?: string; 'aria-label'?: string }) {
  const [text, setText] = useState(value)
  useEffect(() => setText(value), [value])
  useEffect(() => {
    if (text === value) return
    const t = window.setTimeout(() => onCommit(text), 350)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text])
  return <Input {...rest} value={text} onChange={setText} onEnter={() => onCommit(text)} clearable />
}

interface FilterPanelProps {
  filters: PaperFilters
  facets: PaperFacets | null
  onChange: (f: PaperFilters) => void
  onSaveView: () => void
}

const label = (children: string): ReactElement => <div className="pd-fp-label">{children}</div>

/** 筛选面板：所有条件彼此 AND。条件生效后以芯片的形式显示在列表上方，可单独移除。 */
export function FilterPanel({ filters, facets, onChange, onSaveView }: FilterPanelProps) {
  const set = (patch: Partial<PaperFilters>): void => onChange(normalizeFilters({ ...filters, ...patch }))
  const years = facets?.years ?? []
  const minYear = years.length ? Math.min(...years.map((y) => y.year)) : undefined
  const maxYear = years.length ? Math.max(...years.map((y) => y.year)) : undefined
  const tagIds = new Set(filters.tags ?? [])
  const toggleTag = (id: string): void => {
    const next = new Set(tagIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    set({ tags: [...next] })
  }
  const num = (v: string): number | undefined => (v.trim() && Number.isFinite(Number(v)) ? Math.round(Number(v)) : undefined)

  return (
    <section className="pd-fp" aria-label="筛选论文">
      <div className="pd-fp-grid">
        <div className="pd-fp-col pd-fp-col--wide">
          {label('按内容找')}
          {TEXT_FILTERS.map((t) => (
            <div key={t.key} className="pd-fp-row">
              <span className="pd-fp-rowlabel" title={t.hint}>
                {t.label}
              </span>
              <DebouncedInput value={filters[t.key] ?? ''} placeholder={t.placeholder} aria-label={t.label} onCommit={(v) => set({ [t.key]: v })} />
            </div>
          ))}
          <p className="pd-fp-note">多个词用空格隔开，要求全部出现。搜到笔记、批注或正文时，列表会在论文下面显示命中的片段。AI 卡片是 AI 生成的内容，不是论文原文。</p>
        </div>

        <div className="pd-fp-col">
          {label('论文信息')}
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">年份</span>
            <div className="pd-fp-pair">
              <Input type="number" inputMode="numeric" value={filters.year_from?.toString() ?? ''} placeholder={minYear ? String(minYear) : '起'} aria-label="起始年份" onChange={(v) => set({ year_from: num(v) })} />
              <span>–</span>
              <Input type="number" inputMode="numeric" value={filters.year_to?.toString() ?? ''} placeholder={maxYear ? String(maxYear) : '止'} aria-label="结束年份" onChange={(v) => set({ year_to: num(v) })} />
            </div>
          </div>
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">作者</span>
            <>
              <Input list="pd-fp-authors" value={filters.author ?? ''} placeholder="姓名的一部分" aria-label="作者" onChange={(v) => set({ author: v })} clearable />
              <datalist id="pd-fp-authors">
                {(facets?.authors ?? []).map((a) => (
                  <option key={a.author} value={a.author}>
                    {a.count} 篇
                  </option>
                ))}
              </datalist>
            </>
          </div>
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">期刊/会议</span>
            <Select size="sm" aria-label="期刊或会议" value={filters.venue ?? ''} onChange={(v) => set({ venue: v })} options={[{ value: '', label: '不限' }, ...(facets?.venues ?? []).map((v) => ({ value: v.venue, label: `${v.venue} · ${v.count}` }))]} />
          </div>
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">语言</span>
            <Select size="sm" aria-label="语言" value={filters.language ?? ''} onChange={(v) => set({ language: v })} options={[{ value: '', label: '不限' }, ...(facets?.languages ?? []).map((l) => ({ value: l.language, label: `${l.language === 'zh' ? '中文' : l.language === 'en' ? '英文' : l.language} · ${l.count}` }))]} />
          </div>
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">导入时间</span>
            <div className="pd-fp-pair">
              <Input type="date" value={filters.imported_from ?? ''} aria-label="导入起始日期" onChange={(v) => set({ imported_from: v })} />
              <span>–</span>
              <Input type="date" value={filters.imported_to ?? ''} aria-label="导入结束日期" onChange={(v) => set({ imported_to: v })} />
            </div>
          </div>
        </div>

        <div className="pd-fp-col">
          {label('状态')}
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">阅读</span>
            <Select size="sm" aria-label="阅读状态" value={filters.read ?? ''} onChange={(v) => set({ read: (v || undefined) as ReadState | undefined })} options={[{ value: '', label: '不限' }, ...READ_OPTIONS.map((o) => ({ value: o.value, label: facets ? `${o.label} · ${facets.read[o.value]}` : o.label }))]} />
          </div>
          <div className="pd-fp-row">
            <span className="pd-fp-rowlabel">解析</span>
            <Select size="sm" aria-label="解析状态" value={filters.parse ?? ''} onChange={(v) => set({ parse: (v || undefined) as ParseState | undefined })} options={[{ value: '', label: '不限' }, ...PARSE_OPTIONS.map((o) => ({ value: o.value, label: facets && facets.parse[o.value] ? `${o.label} · ${facets.parse[o.value]}` : o.label }))]} />
          </div>
          <div className="pd-fp-flags">
            {FLAG_FILTERS.map((fl) => (
              <label key={fl.key} className="pd-fp-check">
                <input type="checkbox" checked={Boolean(filters[fl.key])} onChange={(e) => set({ [fl.key]: e.target.checked })} />
                <span>{fl.label}</span>
                {facets && <span className="pd-fp-count">{facets.flags[fl.key]}</span>}
              </label>
            ))}
          </div>
        </div>

        <div className="pd-fp-col pd-fp-col--wide">
          {label('标签')}
          {(facets?.tags ?? []).length === 0 ? (
            <p className="pd-fp-note">还没有标签。在论文行的“更多”菜单里选“标签…”可以添加。</p>
          ) : (
            <>
              <div className="pd-fp-tags">
                {(facets?.tags ?? []).map((t) => (
                  <button key={t.id} type="button" className={`pd-ptag pd-fp-tag${tagIds.has(t.id) ? ' is-on' : ''}`} aria-pressed={tagIds.has(t.id)} onClick={() => toggleTag(t.id)}>
                    {t.name} <span className="pd-fp-count">{t.count}</span>
                  </button>
                ))}
              </div>
              {tagIds.size > 1 && (
                <Segmented<'any' | 'all'> variant="text" aria-label="标签匹配方式" value={filters.tag_mode ?? 'any'} onChange={(v) => set({ tag_mode: v })} options={[{ value: 'any', label: '含任一标签' }, { value: 'all', label: '同时含有全部' }]} />
              )}
            </>
          )}
        </div>
      </div>
      <div className="pd-fp-foot">
        <Button size="sm" icon={<Bookmark size={14} />} onClick={onSaveView} disabled={Object.keys(normalizeFilters(filters)).length === 0}>
          保存为智能视图
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onChange({})} disabled={Object.keys(normalizeFilters(filters)).length === 0}>
          清除全部条件
        </Button>
      </div>
    </section>
  )
}

/** 已生效条件的芯片行：每个条件一枚，点 × 移除。 */
export function FilterChips({ filters, lookup, onChange }: { filters: PaperFilters; lookup: ChipLookup; onChange: (f: PaperFilters) => void }) {
  const chips = filterChips(filters, lookup)
  if (chips.length === 0) return null
  return (
    <div className="pd-chips" aria-label="已生效的筛选条件">
      {chips.map((c) => (
        <span key={c.key} className="pd-chip">
          {c.label}
          <button type="button" className="pd-chip-x" aria-label={`移除条件：${c.label}`} onClick={() => onChange(c.without)}>
            <X size={12} />
          </button>
        </span>
      ))}
      {chips.length > 1 && (
        <button type="button" className="pd-chip-clear" onClick={() => onChange({})}>
          清除全部
        </button>
      )}
    </div>
  )
}
