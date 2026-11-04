import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { AlertCircle, ArrowLeft, ChevronLeft, ChevronRight, Download, FileX, Sparkles } from 'lucide-react'
import { Button, EmptyState, Input, ProgressBar, Select, Skeleton, Spinner } from '@/components/ui'
import { CitePaperContext } from '@/components/CiteChip'
import { Markdown } from '@/components/Markdown'
import { ApiError, api, errorMessage } from '@/lib/api'
import { materialsApi } from '@/lib/api.materials'
import { formatBytes, formatNumber, formatRelativeTime } from '@/lib/format'
import type {
  ArchiveAnalysis,
  AssetAnalysis,
  DatasetColumn,
  DatasetSheet,
  DocumentAnalysis,
  SupplementAsset,
  SupplementAuthorization,
  TableAnalysis,
  TablePage
} from '@/lib/types.materials'
import { useJobsStore } from '@/stores/jobsStore'
import { toast } from '@/components/ui'
import { fmtNum, Histogram, TopBars } from './charts'
import './materials.css'

const PAGE_SIZE = 50
const DTYPE_TEXT: Record<string, string> = {
  integer: '整数',
  float: '小数',
  string: '文本',
  boolean: '布尔',
  datetime: '时间',
  mixed: '混合'
}

type Tab = 'fields' | 'data' | 'explain'

function ColumnOverview({ c }: { c: DatasetColumn }) {
  const st = c.stats
  if (c.histogram && c.histogram.bins.length > 0) {
    return (
      <>
        <Histogram bins={c.histogram.bins} />
        {st && (
          <span className="pd-aux pd-weak">
            中位 {fmtNum(st.median)} · 均值 {fmtNum(st.mean)} · 标准差 {fmtNum(st.std)}
            {c.histogram.sampled ? ' · 分布基于抽样' : ''}
          </span>
        )}
      </>
    )
  }
  if (c.top_values && c.top_values.length > 0) return <TopBars values={c.top_values} total={c.non_null} />
  if (st && st.min != null) {
    return (
      <span className="pd-aux">
        {fmtNum(st.min)} → {fmtNum(st.max)}
      </span>
    )
  }
  return <span className="pd-aux pd-weak">—</span>
}

function FieldsTable({ sheet }: { sheet: DatasetSheet }) {
  return (
    <table className="pd-cols">
      <thead>
        <tr>
          <th>字段</th>
          <th>缺失</th>
          <th>不同取值</th>
          <th>取值范围 / 分布</th>
        </tr>
      </thead>
      <tbody>
        {sheet.columns.map((c) => (
          <tr key={c.name}>
            <td>
              {c.name}
              <span className="pd-aux pd-weak">{DTYPE_TEXT[c.dtype] ?? c.dtype}</span>
            </td>
            <td>
              <span className="pd-misbar" aria-hidden>
                <i style={{ width: `${Math.min(100, c.missing_rate * 100)}%` }} />
              </span>
              {c.missing === 0 ? '无' : `${(c.missing_rate * 100).toFixed(1)}%`}
              {c.missing > 0 && <span className="pd-aux pd-weak"> · {formatNumber(c.missing)} 处</span>}
            </td>
            <td>
              {formatNumber(c.unique)}
              {!c.unique_exact && <span className="pd-aux pd-weak"> 以上</span>}
            </td>
            <td>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                <ColumnOverview c={c} />
                {c.stats && c.histogram && (
                  <span className="pd-aux pd-weak">
                    最小 {fmtNum(c.stats.min)} · 最大 {fmtNum(c.stats.max)}
                  </span>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function DataTable({ assetId, sheet }: { assetId: string; sheet: DatasetSheet }) {
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<TablePage | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [jump, setJump] = useState('')
  const total = sheet.rows
  const approx = sheet.rows_approx || sheet.partial

  useEffect(() => {
    setOffset(0)
    setPage(null)
    setError(null)
  }, [assetId, sheet.name])

  useEffect(() => {
    if (offset === 0) {
      setPage(null)
      return
    }
    let alive = true
    setLoading(true)
    setError(null)
    materialsApi
      .table(assetId, { sheet: sheet.name, offset, limit: PAGE_SIZE })
      .then((p) => alive && setPage(p))
      .catch((err) => alive && setError(errorMessage(err)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [assetId, sheet.name, offset])

  const columns = page?.columns ?? sheet.preview.columns
  const rows = page?.rows ?? (offset === 0 ? sheet.preview.rows : [])
  const hasNext = rows.length === PAGE_SIZE && (total == null || offset + PAGE_SIZE < total)
  const go = (): void => {
    const n = Number.parseInt(jump, 10)
    if (Number.isFinite(n) && n >= 1) setOffset(Math.floor((n - 1) / PAGE_SIZE) * PAGE_SIZE)
  }

  return (
    <div>
      <div className="pd-data-wrap">
        <table className="pd-data">
          <thead>
            <tr>
              <th style={{ color: 'var(--text-3)' }}>#</th>
              {columns.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={offset + i}>
                <td className="pd-null">{offset + i + 1}</td>
                {r.map((v, j) => (
                  <td key={j} className={v === null ? 'pd-null' : undefined} title={v === null ? '' : String(v)}>
                    {v === null ? '—' : String(v)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && !loading && <div className="pd-aux pd-weak" style={{ padding: 16 }}>{error ?? '没有更多数据'}</div>}
      </div>
      <div className="pd-data-pager">
        <Button size="sm" variant="ghost" icon={<ChevronLeft size={14} />} disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
          上一页
        </Button>
        <span style={{ fontVariantNumeric: 'tabular-nums' }}>
          第 {formatNumber(offset + 1)}–{formatNumber(offset + rows.length)} 行{total != null ? ` / ${approx ? '约 ' : ''}${formatNumber(total)}` : ''}
        </span>
        <Button size="sm" variant="ghost" disabled={!hasNext || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>
          下一页
          <ChevronRight size={14} />
        </Button>
        {loading && <Spinner size={14} />}
        <span style={{ flex: 1 }} />
        <span>跳到第</span>
        <div style={{ width: 84 }}>
          <Input value={jump} onChange={(v) => setJump(v.replace(/[^\d]/g, ''))} onEnter={go} aria-label="跳到行号" />
        </div>
        <span>行</span>
      </div>
      <p className="pd-aux pd-weak" style={{ marginTop: 8 }}>
        每次最多读取 {PAGE_SIZE} 行，不会把整个文件载入内存。
      </p>
    </div>
  )
}

function ExplainPane({
  asset,
  analysis,
  auth,
  setAuth,
  onChanged
}: {
  asset: SupplementAsset
  analysis: TableAnalysis
  auth: SupplementAuthorization | null
  setAuth: (a: SupplementAuthorization) => void
  onChanged: () => void
}) {
  const [running, setRunning] = useState(false)
  const [needsConfig, setNeedsConfig] = useState(false)
  const expl = analysis.explanation
  const navigate = useNavigate()

  const run = async (): Promise<void> => {
    setRunning(true)
    setNeedsConfig(false)
    try {
      if (!auth?.granted) setAuth(await materialsApi.authorization.set(asset.paper_id, true))
      const { job_id } = await materialsApi.explain(asset.id)
      useJobsStore.getState().track(job_id)
      for (let i = 0; i < 160; i++) {
        await new Promise((r) => window.setTimeout(r, 1500))
        const j = await api.jobs.get(job_id)
        if (j.status === 'completed') break
        if (j.status === 'failed' || j.status === 'cancelled') throw new Error(j.error ?? '解读没有成功')
      }
      onChanged()
    } catch (err) {
      if (err instanceof ApiError && (err.code === 'not_configured' || err.code === 'no_api_key')) setNeedsConfig(true)
      else toast.error('无法解读数据集', errorMessage(err))
    } finally {
      setRunning(false)
    }
  }

  const evidence = expl?.evidence
  return (
    <div className="pd-explain">
      {expl && (
        <>
          <div className="pd-explain-meta">
            {expl.model} · {formatRelativeTime(expl.at)}生成
            {expl.verification.status !== 'verified' && ' · 部分引用无法在论文中核实，请对照原文确认'}
          </div>
          <CitePaperContext.Provider value={asset.paper_id}>
            <Markdown text={expl.markdown} evidence={evidence} invalid={expl.verification.invalid_citations} invalidRefs={expl.verification.invalid_refs} />
          </CitePaperContext.Provider>
        </>
      )}
      {!expl && !running && (
        <EmptyState
          icon={<Sparkles size={20} />}
          title="让 AI 结合论文解读这份数据"
          description="会把字段统计和前 3 行样本，连同与它相关的论文片段发送给你配置的模型，并按论文原文给出带引用的说明。需要你允许 AI 使用这篇论文的补充材料。"
          action={
            <Button variant="primary" onClick={() => void run()}>
              {auth?.granted ? '解读数据集' : '允许并解读'}
            </Button>
          }
        />
      )}
      {running && (
        <div style={{ padding: '16px 0', maxWidth: 360 }}>
          <p className="pd-aux" style={{ margin: '0 0 12px' }}>
            正在结合论文解读数据集…
          </p>
          <ProgressBar />
        </div>
      )}
      {expl && !running && (
        <Button size="sm" variant="ghost" icon={<Sparkles size={13} />} onClick={() => void run()} style={{ marginTop: 16 }}>
          重新解读
        </Button>
      )}
      {needsConfig && (
        <div className="pd-card-hint pd-card-hint--warn" role="note" style={{ marginTop: 16 }}>
          <AlertCircle size={14} />
          <span style={{ flex: 1 }}>还没有配置 AI 模型。</span>
          <Button size="sm" onClick={() => navigate('/settings/ai')}>
            去设置
          </Button>
        </div>
      )}
    </div>
  )
}

function TableView({
  asset,
  analysis,
  auth,
  setAuth,
  onChanged
}: {
  asset: SupplementAsset
  analysis: TableAnalysis
  auth: SupplementAuthorization | null
  setAuth: (a: SupplementAuthorization) => void
  onChanged: () => void
}) {
  const [tab, setTab] = useState<Tab>('fields')
  const [sheetName, setSheetName] = useState(analysis.sheets[0]?.name ?? '')
  const sheet = analysis.sheets.find((s) => s.name === sheetName) ?? analysis.sheets[0]
  if (!sheet) return <EmptyState icon={<FileX size={20} />} title="这个文件里没有读到表格数据" />

  return (
    <>
      {analysis.sheets.length > 1 && (
        <div className="pd-row" style={{ marginTop: 'var(--sp-lg)' }}>
          <span className="pd-aux pd-weak">工作表</span>
          <Select
            size="sm"
            aria-label="选择工作表"
            value={sheet.name}
            onChange={setSheetName}
            options={analysis.sheets.map((s) => ({ value: s.name, label: `${s.name} · ${formatNumber(s.rows)} 行` }))}
          />
        </div>
      )}
      <div className="pd-asset-stats">
        <div className="pd-asset-stat">
          <b>
            {sheet.rows_approx ? '≈ ' : ''}
            {formatNumber(sheet.rows)}
          </b>
          <span>行</span>
        </div>
        <div className="pd-asset-stat">
          <b>{formatNumber(sheet.columns.length)}</b>
          <span>列</span>
        </div>
        <div className="pd-asset-stat">
          <b>{formatNumber(sheet.columns.reduce((n, c) => n + c.missing, 0))}</b>
          <span>缺失值</span>
        </div>
        {analysis.encoding && (
          <div className="pd-asset-stat">
            <b>{analysis.encoding}</b>
            <span>编码{analysis.delimiter ? ` · 分隔符 ${analysis.delimiter === '\t' ? 'Tab' : analysis.delimiter}` : ''}</span>
          </div>
        )}
      </div>
      {sheet.partial && (
        <p className="pd-asset-notice">
          文件较大，只扫描了前 {formatNumber(sheet.rows_scanned)} 行来计算统计，总行数{sheet.rows_approx ? '为估计值' : '已统计'}。逐页浏览仍可读取之后的数据。
        </p>
      )}
      <div className="pd-asset-tabs" role="tablist">
        {(
          [
            ['fields', '字段与统计'],
            ['data', '数据预览'],
            ['explain', 'AI 解读']
          ] as Array<[Tab, string]>
        ).map(([k, label]) => (
          <button key={k} type="button" role="tab" className="pd-asset-tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </div>
      <div style={{ marginTop: 'var(--sp-xl)' }}>
        {tab === 'fields' && <FieldsTable sheet={sheet} />}
        {tab === 'data' && <DataTable assetId={asset.id} sheet={sheet} />}
        {tab === 'explain' && <ExplainPane asset={asset} analysis={analysis} auth={auth} setAuth={setAuth} onChanged={onChanged} />}
      </div>
    </>
  )
}

function ArchiveView({ asset, a, onChanged }: { asset: SupplementAsset; a: ArchiveAnalysis; onChanged: () => void }) {
  const [busy, setBusy] = useState(false)
  const isZip = asset.filename.toLowerCase().endsWith('.zip')
  const extract = async (): Promise<void> => {
    setBusy(true)
    try {
      const { job_id } = await materialsApi.extract(asset.id)
      useJobsStore.getState().track(job_id)
      for (let i = 0; i < 120; i++) {
        await new Promise((r) => window.setTimeout(r, 1200))
        const j = await api.jobs.get(job_id)
        if (j.status === 'completed') break
        if (j.status === 'failed' || j.status === 'cancelled') throw new Error(j.error ?? '解压没有成功')
      }
      onChanged()
    } catch (err) {
      toast.error('解压失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  const exts = Object.entries(a.extensions).sort((x, y) => y[1] - x[1]).slice(0, 8)
  return (
    <>
      <div className="pd-asset-stats">
        <div className="pd-asset-stat">
          <b>{formatNumber(a.total_files)}</b>
          <span>个文件</span>
        </div>
        <div className="pd-asset-stat">
          <b>{formatBytes(a.total_uncompressed)}</b>
          <span>解压后大小</span>
        </div>
        {exts.length > 0 && (
          <div className="pd-asset-stat">
            <b style={{ fontSize: 'var(--fs-small)' }}>{exts.map(([e, n]) => `${e || '无扩展名'} ${n}`).join(' · ')}</b>
            <span>文件类型</span>
          </div>
        )}
      </div>
      {a.suspicious.length > 0 && (
        <ul className="pd-warn-list">
          {a.suspicious.slice(0, 6).map((s, i) => (
            <li key={i}>{s}</li>
          ))}
        </ul>
      )}
      <p className="pd-asset-notice">
        压缩包里的内容只会被列出，不会被运行。{a.truncated ? '文件很多，只列出前一部分。' : ''}
      </p>
      <ul className="pd-entries">
        {a.entries.map((e, i) => (
          <li key={i}>
            <span style={{ wordBreak: 'break-all' }}>{e.name}</span>
            <span>{e.is_dir ? '目录' : formatBytes(e.size)}</span>
          </li>
        ))}
      </ul>
      {isZip && (
        <div style={{ marginTop: 'var(--sp-lg)' }}>
          {a.extracted ? (
            <p className="pd-aux" style={{ margin: 0 }}>
              已安全解压 {formatNumber(a.extracted.files)} 个文件到应用数据目录 {a.extracted.dir}。
            </p>
          ) : (
            <Button loading={busy} onClick={() => void extract()}>
              安全解压到应用数据目录
            </Button>
          )}
        </div>
      )}
    </>
  )
}

function DocumentView({ d }: { d: DocumentAnalysis }) {
  return (
    <>
      <div className="pd-asset-stats">
        <div className="pd-asset-stat">
          <b>{formatNumber(d.paragraphs)}</b>
          <span>段落</span>
        </div>
        <div className="pd-asset-stat">
          <b>{formatNumber(d.tables)}</b>
          <span>表格</span>
        </div>
        <div className="pd-asset-stat">
          <b>{formatNumber(d.chars)}</b>
          <span>字符</span>
        </div>
      </div>
      {d.headings.length > 0 && (
        <>
          <h3 className="pd-section-title" style={{ margin: '0 0 8px' }}>
            标题
          </h3>
          <ul className="pd-entries" style={{ marginBottom: 'var(--sp-xl)', maxHeight: '24vh' }}>
            {d.headings.map((h, i) => (
              <li key={i}>
                <span>{h}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      <h3 className="pd-section-title" style={{ margin: '0 0 8px' }}>
        开头片段
      </h3>
      <div className="pd-textsample">{d.text_sample || '没有读到文字'}</div>
    </>
  )
}

export function AssetPage() {
  const { assetId } = useParams()
  const navigate = useNavigate()
  const [data, setData] = useState<{ asset: SupplementAsset; analysis: AssetAnalysis } | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [auth, setAuth] = useState<SupplementAuthorization | null>(null)
  const [downloading, setDownloading] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  const load = useCallback(async (): Promise<void> => {
    if (!assetId) return
    try {
      const d = await materialsApi.analysis(assetId)
      setData(d)
      setError(null)
      window.clearTimeout(timer.current)
      if (['pending', 'downloading', 'processing'].includes(d.asset.download_status)) {
        timer.current = window.setTimeout(() => void load(), 1500)
      }
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [assetId])

  useEffect(() => {
    setData(null)
    setError(null)
    void load()
    return () => window.clearTimeout(timer.current)
  }, [load])

  const paperId = data?.asset.paper_id
  useEffect(() => {
    if (!paperId) return
    materialsApi.authorization
      .get(paperId)
      .then(setAuth)
      .catch(() => undefined)
  }, [paperId])

  const download = async (): Promise<void> => {
    if (!data) return
    setDownloading(true)
    try {
      const blob = await materialsApi.fileBlob(data.asset.id)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = data.asset.filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 10_000)
    } catch (err) {
      toast.error('无法取回原文件', errorMessage(err))
    } finally {
      setDownloading(false)
    }
  }

  const body = useMemo(() => {
    if (!data) return null
    const { asset, analysis } = data
    if (asset.download_status !== 'done') {
      return (
        <EmptyState
          icon={asset.download_status === 'failed' ? <AlertCircle size={22} /> : <Spinner size={20} />}
          tone={asset.download_status === 'failed' ? 'danger' : 'default'}
          title={asset.download_status === 'failed' ? '处理失败' : asset.download_status === 'waiting_approval' ? '等待你批准下载' : '正在处理…'}
          description={asset.download_error ?? (asset.download_status === 'waiting_approval' ? '请回到论文的“材料”标签中批准或拒绝。' : '稍候片刻，完成后会自动显示。')}
        />
      )
    }
    if (!analysis) {
      return <EmptyState icon={<FileX size={22} />} title="这个文件没有可显示的内容" description="暂时只能解析表格、Word 文档和压缩包；可以把原文件取回后用别的软件打开。" />
    }
    if (analysis.kind === 'error') {
      return <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="解析时遇到问题" description={`${analysis.error}。原文件已保留，可以取回后用别的软件打开。`} />
    }
    if (analysis.kind === 'table') {
      return <TableView asset={asset} analysis={analysis} auth={auth} setAuth={setAuth} onChanged={() => void load()} />
    }
    if (analysis.kind === 'archive') return <ArchiveView asset={asset} a={analysis} onChanged={() => void load()} />
    if (analysis.kind === 'document') return <DocumentView d={analysis} />
    return null
  }, [data, auth, load])

  if (error && !data) {
    return (
      <div className="pd-page">
        <div className="pd-page-inner pd-asset">
          <EmptyState tone="danger" icon={<AlertCircle size={22} />} title="无法打开这份材料" description={error} action={<Button onClick={() => navigate(-1)}>返回</Button>} />
        </div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className="pd-page">
        <div className="pd-page-inner pd-asset">
          <Skeleton height={28} width={320} />
          <Skeleton height={14} width={200} style={{ marginTop: 12 }} />
        </div>
      </div>
    )
  }

  const { asset } = data
  return (
    <div className="pd-page">
      <div className="pd-page-inner pd-asset">
        <button type="button" className="pd-asset-back" onClick={() => navigate(`/reader/${asset.paper_id}`)}>
          <ArrowLeft size={14} />
          返回论文
        </button>
        <div className="pd-row" style={{ alignItems: 'flex-start' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <h1>{asset.filename}</h1>
            <div className="pd-asset-sub">
              {asset.category}
              {asset.size != null ? ` · ${formatBytes(asset.size)}` : ''}
              {asset.source_url ? ` · 来自 ${asset.source_url}` : ''}
            </div>
            {asset.summary && <p className="pd-asset-sub" style={{ marginTop: 8, maxWidth: '78ch' }}>{asset.summary}</p>}
          </div>
          {asset.download_status === 'done' && (
            <Button size="sm" icon={<Download size={13} />} loading={downloading} onClick={() => void download()}>
              取回原文件
            </Button>
          )}
        </div>
        {body}
      </div>
    </div>
  )
}
