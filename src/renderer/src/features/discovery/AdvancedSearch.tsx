import { useState } from 'react'
import { Download, Plus, Search, X } from 'lucide-react'
import { Button, Dialog, Input, Menu, Select, Switch, toast, type MenuItem } from '@/components/ui'
import { discoveryApi } from '@/lib/api.discovery'
import { errorMessage } from '@/lib/api'
import { downloadText } from '@/features/shared/download'
import type { AdvancedField, AdvancedResponse, AdvancedRow, Work } from '@/lib/types.discovery'
import { KindFilter, filterByKind, useKindFilter } from './KindFilter'
import { Pager, usePaging } from './Pager'
import { WorkItem } from './WorkItem'
import { WorkDrawer } from './WorkDrawer'
import { DcLoading } from './shared'
import { REF_STYLES, toBibtex, toCsv, toReferenceList, toRis, type RefStyle } from './exportWorks'

const FIELD_OPTIONS: Array<{ value: AdvancedField; label: string }> = [
  { value: 'title', label: '标题' },
  { value: 'tak', label: '标题/摘要' },
  { value: 'abstract', label: '摘要' },
  { value: 'author', label: '作者' },
  { value: 'venue', label: '期刊/会议' },
  { value: 'doi', label: 'DOI' },
  { value: 'all', label: '全部字段' }
]
const OP_OPTIONS = [
  { value: 'and', label: '且 AND' },
  { value: 'or', label: '或 OR' },
  { value: 'not', label: '非 NOT' }
]
const SORTS = [
  { value: 'relevance', label: '相关度' },
  { value: 'cited', label: '被引次数' },
  { value: 'date', label: '最新发表' }
]

const blankRow = (field: AdvancedField = 'title'): AdvancedRow => ({ field, term: '', op: 'and', exact: false })

/** 导出里“当前范围”的说明；文件名带日期。 */
const stamp = (): string => new Date().toISOString().slice(0, 10)

/**
 * Scopus 式的高级检索：逐行写 字段 + 检索词，行与行之间用 且 / 或 / 非 连接；标题可以“精确匹配”直接找一篇已知标题的论文。
 * 结果可以导出 BibTeX / RIS / CSV，或按 GB/T 7714、APA、IEEE 生成参考文献文本。不使用 AI，只查公开元数据。
 */
export function AdvancedSearch() {
  const [rows, setRows] = useState<AdvancedRow[]>([blankRow()])
  const [yearFrom, setYearFrom] = useState('')
  const [yearTo, setYearTo] = useState('')
  const [sort, setSort] = useState<'relevance' | 'cited' | 'date'>('relevance')
  const [status, setStatus] = useState<'idle' | 'loading' | 'done' | 'error'>('idle')
  const [error, setError] = useState('')
  const [data, setData] = useState<AdvancedResponse | null>(null)
  const [selected, setSelected] = useState<Work | null>(null)
  const [refStyle, setRefStyle] = useState<RefStyle>('gbt7714')
  const [refOpen, setRefOpen] = useState(false)

  const all = data?.results ?? []
  const kindState = useKindFilter(all)
  const results = filterByKind(all, kindState.kind)
  const paging = usePaging(results, `${all.length}|${kindState.kind ?? ''}|${status}`)

  const setRow = (i: number, patch: Partial<AdvancedRow>): void => setRows((rs) => rs.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  const canSearch = rows.some((r) => r.term.trim())

  const run = async (): Promise<void> => {
    if (!canSearch) return
    setStatus('loading')
    setError('')
    try {
      const res = await discoveryApi.advanced({
        rows: rows.filter((r) => r.term.trim()),
        year_from: yearFrom ? Number(yearFrom) : null,
        year_to: yearTo ? Number(yearTo) : null,
        sort,
        limit: 100
      })
      setData(res)
      setStatus('done')
    } catch (err) {
      setError(errorMessage(err))
      setStatus('error')
    }
  }

  const exportItems: MenuItem[] = [
    { key: 'bib', label: `BibTeX（.bib）· ${results.length} 条`, onSelect: () => save('bib', toBibtex(results), 'application/x-bibtex') },
    { key: 'ris', label: `RIS（.ris，导入 Zotero / EndNote）`, onSelect: () => save('ris', toRis(results), 'application/x-research-info-systems') },
    { key: 'csv', label: 'CSV（.csv，Excel 直接打开）', onSelect: () => save('csv', toCsv(results), 'text/csv;charset=utf-8') },
    { key: 'sep', separator: true },
    { key: 'ref', label: '生成参考文献文本…', onSelect: () => setRefOpen(true) }
  ]
  const save = (ext: string, text: string, mime: string): void => {
    if (results.length === 0) return toast.info('没有可导出的结果')
    downloadText(`检索结果-${stamp()}.${ext}`, text, mime)
  }
  const refText = toReferenceList(results, refStyle)

  return (
    <div className="pd-adv">
      <div className="pd-adv-rows">
        {rows.map((r, i) => (
          <div key={i} className="pd-adv-row">
            {i === 0 ? (
              <span className="pd-adv-op0">检索</span>
            ) : (
              <Select value={r.op} onChange={(v) => setRow(i, { op: v as AdvancedRow['op'] })} options={OP_OPTIONS} aria-label={`第 ${i + 1} 行与上一行的关系`} />
            )}
            <Select value={r.field} onChange={(v) => setRow(i, { field: v as AdvancedField })} options={FIELD_OPTIONS} aria-label="检索字段" />
            <Input
              value={r.term}
              onChange={(v) => setRow(i, { term: v })}
              onEnter={() => void run()}
              placeholder={r.field === 'author' ? '作者姓名，例如 Yoshua Bengio' : r.field === 'doi' ? '10.xxxx/xxxxx' : r.field === 'title' ? '论文标题（或其中的词）' : '检索词'}
              aria-label={`第 ${i + 1} 行检索词`}
            />
            {r.field === 'title' && <Switch checked={r.exact} onChange={(v) => setRow(i, { exact: v })} label="精确匹配" />}
            {rows.length > 1 && (
              <button type="button" className="pd-adv-x" aria-label="删除这一行" onClick={() => setRows((rs) => rs.filter((_, k) => k !== i))}>
                <X size={14} />
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="pd-adv-ctl">
        <Button size="sm" variant="ghost" icon={<Plus size={14} />} disabled={rows.length >= 8} onClick={() => setRows((rs) => [...rs, blankRow('tak')])}>
          添加一行
        </Button>
        <span className="pd-dc-years">
          年份
          <Input value={yearFrom} onChange={(v) => setYearFrom(v.replace(/\D/g, '').slice(0, 4))} placeholder="起" inputMode="numeric" aria-label="起始年份" />
          –
          <Input value={yearTo} onChange={(v) => setYearTo(v.replace(/\D/g, '').slice(0, 4))} placeholder="止" inputMode="numeric" aria-label="截止年份" />
        </span>
        <span className="pd-adv-sort">
          排序
          <Select value={sort} onChange={(v) => setSort(v as typeof sort)} options={SORTS} aria-label="排序方式" />
        </span>
        <div className="pd-spacer" />
        <Button variant="primary" icon={<Search size={15} />} disabled={!canSearch} loading={status === 'loading'} onClick={() => void run()}>
          检索
        </Button>
      </div>
      <p className="pd-dc-hint">从上到下读：“或”把检索式分成几个分支，分支内“且 / 非”依次收窄。标题里勾“精确匹配”，就是按整句标题找一篇已知的论文。只查公开元数据，不用 AI。</p>

      {status === 'error' && <p className="pd-status-text pd-status-text--danger">{error}</p>}
      {status === 'loading' && <DcLoading rows={3} text="正在检索……" />}
      {status === 'done' && data && (
        <>
          <div className="pd-adv-sum">
            <span>
              共 {all.length} 条{data.exact_matches ? ` · 标题完全一致 ${data.exact_matches} 条` : ''}
              {data.branches > 1 ? ` · ${data.branches} 个检索分支` : ''}
            </span>
            <Menu align="end" trigger={({ toggle, open }) => <Button size="sm" icon={<Download size={14} />} onClick={toggle} aria-haspopup="menu" aria-expanded={open} disabled={results.length === 0}>导出</Button>} items={exportItems} />
          </div>
          {data.warnings.map((w) => (
            <p key={w} className="pd-dc-hint">
              {w}
            </p>
          ))}
          <KindFilter works={all} value={kindState.kind} onChange={kindState.setKind} />
          <div className="pd-dc-results">
            {results.length === 0 && <p className="pd-dc-hint pd-dc-empty-line">没有找到符合条件的论文。可以放宽年份、去掉“精确匹配”或少写几行条件。</p>}
            {paging.shown.map((w) => (
              <WorkItem key={w.key} work={w} onOpen={setSelected} />
            ))}
          </div>
          <Pager page={paging.page} pages={paging.pages} total={results.length} onPage={paging.setPage} />
        </>
      )}
      <WorkDrawer work={selected ? (all.find((w) => w.key === selected.key) ?? selected) : null} onClose={() => setSelected(null)} onOpenOther={setSelected} />

      <Dialog
        open={refOpen}
        onClose={() => setRefOpen(false)}
        title="参考文献文本"
        width={720}
        footer={
          <>
            <Button size="sm" onClick={() => void navigator.clipboard.writeText(refText).then(() => toast.success('已复制参考文献'))}>
              复制
            </Button>
            <Button size="sm" onClick={() => save('txt', refText, 'text/plain;charset=utf-8')}>
              保存为 .txt
            </Button>
            <Button size="sm" variant="primary" onClick={() => setRefOpen(false)}>
              关闭
            </Button>
          </>
        }
      >
        <div className="pd-adv-refstyle">
          格式
          <Select value={refStyle} onChange={(v) => setRefStyle(v as RefStyle)} options={REF_STYLES} aria-label="参考文献格式" />
          <span className="pd-aux pd-muted">共 {results.length} 条；信息缺失的项直接省略，不会编造。</span>
        </div>
        <textarea className="pd-adv-reftext" readOnly value={refText} rows={14} aria-label="参考文献文本" />
      </Dialog>
    </div>
  )
}
