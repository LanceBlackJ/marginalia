import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'
import { AlertTriangle, ChevronDown, ChevronRight, Image as ImageIcon, RefreshCw, Trash2 } from 'lucide-react'
import { Button, EmptyState, Input, ProgressBar, Segmented, Skeleton, Tag, toast } from '@/components/ui'
import { Markdown } from '@/components/Markdown'
import { ZoomImage } from '@/components/ZoomImage'
import { api, errorMessage } from '@/lib/api'
import { visionApi } from '@/lib/api.vision'
import { textApi } from '@/lib/api.text'
import { truncate } from '@/lib/format'
import type {
  ChartData,
  ChartPanel,
  FigureAnalysis,
  FigureAnswer,
  FigureGroup,
  FigureKind,
  FrameworkData,
  TableData
} from '@/lib/types.vision'
import { useReaderStore } from '@/features/reader/readerStore'
import { FrameworkFlowSection } from './FrameworkFlowChart'
import { useBlobUrl } from './useBlobUrl'
import './figures.css'

const active = (s?: string): boolean => s === 'pending' || s === 'running'
const KIND_LABEL: Record<FigureKind, string> = { framework: '框架图', chart: '实验图', table: '表格' }

/**
 * 阅读器右侧的“图表”标签：把论文里的框架图、实验结果图和表格交给视觉模型理解。
 * 读图结果由模型生成，后端逐项与图的文字层/图注核对：读不准的数值只标“估计”，图里没有的内容回答“图中看不出”。
 */
export function FiguresPanel() {
  const paperId = useReaderStore((s) => s.paperId)
  const [items, setItems] = useState<FigureGroup[] | null>(null)
  const [openId, setOpenId] = useState<number | null>(null)
  // PDF 上点了图的角标：展开对应的图
  const figureFocus = useReaderStore((s) => s.figureFocus)
  useEffect(() => {
    if (figureFocus) setOpenId(figureFocus.id)
  }, [figureFocus])

  const load = useCallback(async (id: string): Promise<void> => {
    try {
      setItems((await visionApi.figures(id)).items)
    } catch (err) {
      toast.error('无法读取图表列表', errorMessage(err))
      setItems([])
    }
  }, [])

  useEffect(() => {
    setItems(null)
    setOpenId(null)
    if (paperId) void load(paperId)
  }, [paperId, load])

  if (!paperId) return null
  if (items === null) {
    return (
      <div className="pd-fig-panel">
        <Skeleton height={44} />
        <Skeleton height={44} style={{ marginTop: 8 }} />
      </div>
    )
  }
  if (items.length === 0) {
    return (
      <div className="pd-fig-panel">
        <EmptyState icon={<ImageIcon size={20} />} title="没有识别到图表" description="这篇论文的解析结果里没有图或表，扫描版 PDF 暂不支持。" />
      </div>
    )
  }
  return (
    <div className="pd-fig-panel">
      <p className="pd-fig-note">
        点开一张图，让视觉模型读图。读图会把这张图的截图发送给云端模型，并记入隐私日志；结果仅作辅助，请对照原图。
      </p>
      <ul className="pd-fig-list">
        {items.map((f) => (
          <li key={f.id} className="pd-fig-item" data-open={openId === f.id}>
            <button type="button" className="pd-fig-head" aria-expanded={openId === f.id} onClick={() => setOpenId(openId === f.id ? null : f.id)}>
              {openId === f.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="pd-fig-label">{f.label ?? (f.kind === 'table' ? '表格' : '图')}</span>
              <span className="pd-fig-cap">{truncate(f.caption.replace(/^(?:Fig(?:ure)?s?\.?|Table)\s*\d+\s*[|:.]?\s*/, '') || '没有图注', 48)}</span>
              <span className="pd-fig-page">p.{f.page}</span>
              {f.analysis_status === 'ready' && <Tag tone="outline">已读图</Tag>}
              {active(f.analysis_status) && <Tag tone="info">读图中</Tag>}
              {f.analysis_status === 'failed' && <Tag tone="warning">失败</Tag>}
            </button>
            {openId === f.id && <FigureDetail paperId={paperId} fig={f} onChanged={() => void load(paperId)} />}
          </li>
        ))}
      </ul>
    </div>
  )
}

function FigureDetail({ paperId, fig, onChanged }: { paperId: string; fig: FigureGroup; onChanged: () => void }) {
  const jumpToPage = useReaderStore((s) => s.jumpToPage)
  const [data, setData] = useState<FigureAnalysis | null>(null)
  const [progress, setProgress] = useState<{ progress: number; message: string } | null>(null)
  const [kind, setKind] = useState<FigureKind | 'auto'>('auto')
  const [starting, setStarting] = useState(false)
  const [showOrig, setShowOrig] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const orig = useBlobUrl(showOrig ? () => textApi.regionVector(paperId, fig.page, fig.bbox, 110) : null, [showOrig, paperId, fig.id])

  const poll = useCallback(async (): Promise<void> => {
    try {
      const a = await visionApi.analysis(paperId, fig.id)
      setData(a)
      if (active(a.status)) {
        if (a.job_id) {
          try {
            const j = await api.jobs.get(a.job_id)
            setProgress({ progress: j.progress, message: j.message })
          } catch {
            /* 下一轮再取 */
          }
        }
        timer.current = window.setTimeout(() => void poll(), 1800)
      } else {
        setProgress(null)
        onChanged()
      }
    } catch (err) {
      toast.error('无法读取读图结果', errorMessage(err))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paperId, fig.id])

  useEffect(() => {
    setData(null)
    void poll()
    return () => window.clearTimeout(timer.current)
  }, [poll])

  const start = async (force: boolean): Promise<void> => {
    setStarting(true)
    try {
      await visionApi.analyze(paperId, fig.id, { force, kind })
      window.clearTimeout(timer.current)
      await poll()
    } catch (err) {
      toast.error('无法开始读图', errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const remove = async (): Promise<void> => {
    try {
      await visionApi.removeAnalysis(paperId, fig.id)
      window.clearTimeout(timer.current)
      await poll()
      onChanged()
    } catch (err) {
      toast.error('无法删除', errorMessage(err))
    }
  }

  const status = data?.status
  return (
    <div className="pd-fig-body">
      <div className="pd-fig-actions">
        <Button size="sm" variant="ghost" onClick={() => jumpToPage(fig.page)}>
          跳到第 {fig.page} 页
        </Button>
        <Button size="sm" variant="ghost" icon={<ImageIcon size={12} />} aria-pressed={showOrig} onClick={() => setShowOrig((v) => !v)}>
          {showOrig ? '隐藏原图' : '对照原图'}
        </Button>
      </div>
      {showOrig && (orig.url ? <ZoomImage className="pd-fig-orig" src={orig.url} alt={`${fig.label ?? '图'} 原图`} /> : <p className="pd-fig-weak">{orig.error ? '原图加载失败' : '正在加载原图…'}</p>)}

      {(status === 'none' || status === 'failed' || !status) && (
        <div className="pd-fig-start">
          {status === 'failed' && (
            <div className="pd-card-hint" role="note">
              <AlertTriangle size={13} />
              <span>{data?.error || '读图失败。'}</span>
            </div>
          )}
          <Segmented<FigureKind | 'auto'>
            aria-label="图类型"
            value={kind}
            onChange={setKind}
            options={[
              { value: 'auto', label: '自动判断' },
              { value: 'framework', label: '框架图' },
              { value: 'chart', label: '实验图' },
              { value: 'table', label: '表格' }
            ]}
          />
          <Button size="sm" variant="primary" loading={starting} onClick={() => void start(status === 'failed')}>
            {status === 'failed' ? '重试读图' : '让视觉模型读这张图'}
          </Button>
        </div>
      )}

      {active(status) && (
        <div className="pd-fig-progress">
          <ProgressBar value={progress ? Math.max(0.03, progress.progress) : undefined} />
          <span className="pd-fig-weak">{progress?.message || '正在读图…'}</span>
        </div>
      )}

      {status === 'ready' && data && (
        <>
          <AnalysisView a={data} paperId={paperId} fig={fig} />
          <AskBox paperId={paperId} figureId={fig.id} />
          <div className="pd-fig-actions">
            <Button size="sm" variant="ghost" icon={<RefreshCw size={12} />} loading={starting} onClick={() => void start(true)}>
              重新读图
            </Button>
            <Button size="sm" variant="ghost" icon={<Trash2 size={12} />} onClick={() => void remove()}>
              删除结果
            </Button>
          </div>
        </>
      )}
    </div>
  )
}

function Notes({ a }: { a: FigureAnalysis }) {
  const notes = [...(a.warnings ?? []), ...(a.verification?.notes ?? [])]
  if (notes.length === 0) return null
  return (
    <div className="pd-card-hint" role="note">
      <AlertTriangle size={13} />
      <span>{[...new Set(notes)].join('；')}</span>
    </div>
  )
}

function AnalysisView({ a, paperId, fig }: { a: FigureAnalysis; paperId: string; fig: FigureGroup }) {
  const v = a.verification
  const text = a.analysis
  const md = (s: string): ReactElement => <Markdown text={s} evidence={a.evidence} invalid={v?.invalid_citations} invalidRefs={v?.invalid_refs} />
  const verifiable = (a.data as { verifiable?: boolean } | null)?.verifiable
  return (
    <div className="pd-fig-result">
      <div className="pd-fig-meta">
        {a.kind && <Tag tone="outline">{KIND_LABEL[a.kind]}</Tag>}
        <Tag tone="neutral" title="读图结果由视觉模型生成">
          视觉模型读图
        </Tag>
        {verifiable === true && <Tag tone="outline">已与图的文字层核对</Tag>}
        {verifiable === false && <Tag tone="warning" title="这张图是位图，文字无法逐项核对">未能核对文字层</Tag>}
        {v && v.status !== 'verified' && <Tag tone="warning">引用部分未核实</Tag>}
      </div>
      <Notes a={a} />
      {a.kind === 'framework' && a.data && <FrameworkView data={a.data as FrameworkData} a={a} md={md} paperId={paperId} fig={fig} />}
      {a.kind === 'chart' && a.data && <ChartView data={a.data as ChartData} />}
      {a.kind === 'table' && a.data && <TableView data={a.data as TableData} />}
      {text && (
        <>
          {text.overview && (
            <section>
              <h4 className="pd-card-sec">图的整体作用</h4>
              {md(text.overview)}
            </section>
          )}
          {text.flow_explanation && (
            <section>
              <h4 className="pd-card-sec">流程说明</h4>
              {md(text.flow_explanation)}
            </section>
          )}
          {text.direct && (
            <section>
              <h4 className="pd-card-sec">图在说什么</h4>
              {md(text.direct)}
            </section>
          )}
          {text.points && text.points.length > 0 && (
            <section>
              <h4 className="pd-card-sec">要点</h4>
              <ul className="pd-fig-ul">
                {text.points.map((p, i) => (
                  <li key={i}>{md(p.text)}</li>
                ))}
              </ul>
            </section>
          )}
          {text.paper_claims && text.paper_claims.length > 0 && (
            <section>
              <h4 className="pd-card-sec">论文的说法与这张图是否相符</h4>
              <ul className="pd-fig-ul">
                {text.paper_claims.map((c, i) => (
                  <li key={i}>
                    <Tag tone={c.supported === 'yes' ? 'outline' : 'warning'}>
                      {{ yes: '图支持', partly: '部分支持', no: '图不支持', unclear: '无法判断' }[c.supported]}
                    </Tag>{' '}
                    {c.text}
                    {c.note && <span className="pd-fig-weak"> —— {c.note}</span>}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {text.inferences.length > 0 && (
            <section>
              <h4 className="pd-card-sec" title="不是作者原话">AI 推断</h4>
              <ul className="pd-fig-ul">
                {text.inferences.map((x, i) => (
                  <li key={i}>{md(x.text)}</li>
                ))}
              </ul>
            </section>
          )}
          {text.limitations.length > 0 && (
            <section>
              <h4 className="pd-card-sec">读图的局限</h4>
              <ul className="pd-fig-ul">
                {text.limitations.map((x, i) => (
                  <li key={i}>{x}</li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
      {v?.ungrounded_numbers && v.ungrounded_numbers.length > 0 && (
        <p className="pd-fig-weak">
          这些数字在图和所引原文里都找不到，请不要当作事实使用：{v.ungrounded_numbers.join('、')}
        </p>
      )}
    </div>
  )
}

function FrameworkView({ data, a, md, paperId, fig }: { data: FrameworkData; a: FigureAnalysis; md: (s: string) => ReactElement; paperId: string; fig: FigureGroup }) {
  const byId = new Map(data.modules.map((m) => [m.id, m]))
  const grouped = new Set(data.groups.flatMap((g) => g.members))
  const ungrouped = data.modules.filter((m) => !grouped.has(m.id))
  const mapping = a.mapping?.modules ?? {}
  const moduleRow = (id: string): ReactElement | null => {
    const m = byId.get(id)
    if (!m) return null
    const map = mapping[id]
    return (
      <li key={id} className="pd-fig-mod">
        <div className="pd-fig-mod-head">
          <strong>{m.label}</strong>
          <Tag tone="outline">{{ input: '输入', process: '处理', model: '模型', output: '输出', data: '数据', other: '其他' }[m.kind]}</Tag>
          {m.in_text === false && (
            <Tag tone="warning" title="这个名字没有出现在图的文字层里，可能是模型读错或自己起的名字">
              图中未找到该文字
            </Tag>
          )}
        </div>
        {m.role && <div className="pd-fig-weak">{m.role}</div>}
        {map?.found && map.description ? md(map.description) : <div className="pd-fig-weak">论文正文里没有找到对它的描述。</div>}
      </li>
    )
  }
  return (
    <>
      {data.summary && (
        <section>
          <h4 className="pd-card-sec">概述</h4>
          <p className="pd-fig-p">{data.summary}</p>
        </section>
      )}
      <FrameworkFlowSection data={data} a={a} md={md} paperId={paperId} fig={fig} />
      <section>
        <h4 className="pd-card-sec">模块 {data.modules.length}</h4>
        {data.groups.map((g) => (
          <div key={g.id} className="pd-fig-group">
            <div className="pd-fig-group-title">{g.label}</div>
            <ul className="pd-fig-mods">{g.members.map(moduleRow)}</ul>
          </div>
        ))}
        {ungrouped.length > 0 && <ul className="pd-fig-mods">{ungrouped.map((m) => moduleRow(m.id))}</ul>}
      </section>
      {data.flows.length > 0 && (
        <section>
          <h4 className="pd-card-sec">连线 {data.flows.length}</h4>
          <ul className="pd-fig-flows">
            {data.flows.map((f, i) => (
              <li key={i}>
                {byId.get(f.from)?.label ?? f.from} <span className="pd-fig-arrow">→</span> {byId.get(f.to)?.label ?? f.to}
                {f.label && <span className="pd-fig-weak"> · {f.label}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}
      {data.uncertain.length > 0 && (
        <section>
          <h4 className="pd-card-sec">模型自己不确定的地方</h4>
          <ul className="pd-fig-ul">
            {data.uncertain.map((u, i) => (
              <li key={i}>{u}</li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}

function valueTag(v: ChartPanel['values'][number]): ReactElement {
  if (v.precision === 'exact' && v.verified) return <Tag tone="outline" title="数值在图的文字层里原样出现">图上印出</Tag>
  if (v.precision === 'exact') return <Tag tone="warning" title="没有在图的文字层里找到，不能当作精确值">未核对</Tag>
  return <Tag tone="neutral" title="从图形位置估读，不是精确值">估计</Tag>
}

function ChartView({ data }: { data: ChartData }) {
  return (
    <>
      {data.panels.map((p, i) => (
        <section key={i} className="pd-fig-panelbox">
          <h4 className="pd-card-sec">
            子图 {p.label || i + 1}
            {p.chart_type && <span className="pd-fig-weak"> · {p.chart_type}</span>}
            {p.title && <span> · {p.title}</span>}
          </h4>
          <p className="pd-fig-weak">
            横轴：{p.x_axis.label || '—'}
            {p.x_axis.unit && `（${p.x_axis.unit}）`}；纵轴：{p.y_axis.label || '—'}
            {p.y_axis.unit && `（${p.y_axis.unit}）`}
          </p>
          {p.series.length > 0 && (
            <div className="pd-fig-chips">
              {p.series.map((s) => (
                <Tag key={s} tone="outline">
                  {s}
                </Tag>
              ))}
            </div>
          )}
          {p.observations.length > 0 && (
            <ul className="pd-fig-ul">
              {p.observations.map((o, k) => (
                <li key={k}>
                  {o.text}
                  {o.has_unverified_numbers && <Tag tone="warning">含未核对数字</Tag>}
                </li>
              ))}
            </ul>
          )}
          {p.values.length > 0 && (
            <table className="pd-fig-table">
              <thead>
                <tr>
                  <th scope="col">读到的数值</th>
                  <th scope="col">值</th>
                  <th scope="col">可信度</th>
                </tr>
              </thead>
              <tbody>
                {p.values.map((v, k) => (
                  <tr key={k}>
                    <td>{v.text}</td>
                    <td>{v.value}</td>
                    <td>{valueTag(v)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      ))}
      {data.unreadable.length > 0 && <p className="pd-fig-weak">读不清的部分：{data.unreadable.join('；')}</p>}
    </>
  )
}

function TableView({ data }: { data: TableData }) {
  const bad = new Set(data.unverified_cells.map(([r, c]) => `${r}:${c}`))
  return (
    <section>
      {data.title && <h4 className="pd-card-sec">{data.title}</h4>}
      <div className="pd-fig-tablewrap">
        <table className="pd-fig-table">
          <thead>
            <tr>
              {data.columns.map((c, i) => (
                <th key={i} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} data-unverified={bad.has(`${r}:${c}`) || undefined} title={bad.has(`${r}:${c}`) ? '这个单元格没有在表格文字层里找到，可能读错' : undefined}>
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {data.verified_ratio != null && <p className="pd-fig-weak">与文字层一致的单元格：{Math.round(data.verified_ratio * 100)}%；带虚线下划线的单元格未能核对。</p>}
      {data.notes && <p className="pd-fig-weak">{data.notes}</p>}
    </section>
  )
}

function AskBox({ paperId, figureId }: { paperId: string; figureId: number }) {
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [ans, setAns] = useState<FigureAnswer | null>(null)
  const ask = async (): Promise<void> => {
    if (!q.trim()) return
    setBusy(true)
    try {
      setAns(await visionApi.askFigure(paperId, figureId, q.trim()))
    } catch (err) {
      toast.error('提问失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="pd-fig-ask">
      <h4 className="pd-card-sec">追问这张图</h4>
      <div className="pd-fig-askrow">
        <Input value={q} placeholder="例如：哪个方法在最大规模上最好？" onChange={setQ} onEnter={() => void ask()} aria-label="追问这张图" />
        <Button size="sm" loading={busy} disabled={!q.trim()} onClick={() => void ask()}>
          提问
        </Button>
      </div>
      <p className="pd-fig-weak">提问会再次发送这张图的截图。图里看不出的内容会直接回答“图中看不出”。</p>
      {ans && (
        <div className="pd-fig-answer">
          {!ans.answerable && <Tag tone="warning">图中看不出</Tag>}
          <p className="pd-fig-p">{ans.answer}</p>
          {ans.numbers.length > 0 && (
            <div className="pd-fig-chips">
              {ans.numbers.map((n, i) => (
                <Tag key={i} tone={n.how === 'printed' ? 'outline' : 'neutral'} title={n.how === 'printed' ? '在图的文字层里找到' : '读图估计，不是精确值'}>
                  {n.value} · {n.how === 'printed' ? '图上印出' : '估计'}
                </Tag>
              ))}
            </div>
          )}
          {ans.warnings.map((w, i) => (
            <p key={i} className="pd-fig-weak">
              {w}
            </p>
          ))}
        </div>
      )}
    </div>
  )
}
