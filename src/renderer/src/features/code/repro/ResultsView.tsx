import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Download, Plus, Trash2 } from 'lucide-react'
import { Button, EmptyState, IconButton, Input, Select, Skeleton, TextArea, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { reproApi } from '@/lib/api.repro'
import type { CompareResult, ReproRun, ReproTarget, TargetCandidate } from '@/lib/types.repro'
import { openPaperAt } from '../codeStore'
import { COMPARE_LABEL, downloadText, envToText, formatNum, sourceLabel, formatRel, parseEnvText, relToTolPercent, tolPercentToRel } from './reproUtils'

// ---------------------------------------------------------------- 论文里的目标数值
function TargetRow({ t, paperId, onChanged }: { t: ReproTarget; paperId: string; onChanged: () => void }) {
  const navigate = useNavigate()
  const [name, setName] = useState(t.name)
  const [tol, setTol] = useState(relToTolPercent(t.tol_rel))
  useEffect(() => {
    setName(t.name)
    setTol(relToTolPercent(t.tol_rel))
  }, [t])
  const patch = async (body: Parameters<typeof reproApi.patchTarget>[2]): Promise<void> => {
    try {
      await reproApi.patchTarget(paperId, t.id, body)
      onChanged()
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    }
  }
  return (
    <tr>
      <td>
        <Input value={name} onChange={setName} onBlur={() => name.trim() && name !== t.name && void patch({ name: name.trim() })} aria-label="指标名称" />
      </td>
      <td className="pd-repro-num">
        {formatNum(t.value)}
        {t.std !== null && ` ± ${formatNum(t.std)}`}
        {t.unit}
      </td>
      <td>
        {t.page ? (
          <button type="button" className="pd-link" onClick={() => void openPaperAt(navigate, paperId, { page: t.page ?? undefined, ord: t.ord ?? undefined })}>
            论文第 {t.page} 页
          </button>
        ) : (
          <span className="pd-muted">手动填写</span>
        )}
        {t.quote_verified === true && <span className="pd-aux pd-muted"> · 出处原句已核对</span>}
        {t.quote_verified === false && <span className="pd-aux"> · 出处原句在该段落里找不到</span>}
      </td>
      <td className="pd-repro-tol">
        <Input
          value={tol}
          onChange={setTol}
          onBlur={() => {
            const rel = tolPercentToRel(tol)
            if (rel === undefined) return toast.error('容差必须是非负数字，单位百分比')
            if (rel !== t.tol_rel) void patch({ tol_rel: rel })
          }}
          placeholder="如 5"
          aria-label="相对容差 %"
        />
        <span className="pd-aux pd-muted">%</span>
      </td>
      <td>
        <label className="pd-repro-check" title="指标定义和你的结果不同时取消勾选，对比会标为“无法比较”">
          <input type="checkbox" checked={t.comparable} onChange={(e) => void patch({ comparable: e.target.checked })} />
        </label>
      </td>
      <td>
        <IconButton
          icon={<Trash2 size={14} />}
          label="删除这个目标数值"
          size="sm"
          onClick={() => void reproApi.deleteTarget(paperId, t.id).then(onChanged, (err) => toast.error('删除失败', errorMessage(err)))}
        />
      </td>
    </tr>
  )
}

function Targets({ paperId, targets, reload }: { paperId: string; targets: ReproTarget[]; reload: () => void }) {
  const [cands, setCands] = useState<TargetCandidate[] | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [form, setForm] = useState({ name: '', value: '', std: '', unit: '' })

  const findCandidates = async (): Promise<void> => {
    try {
      setCands((await reproApi.candidates(paperId)).items)
      setPicked(new Set())
    } catch (err) {
      toast.error('读取候选失败', errorMessage(err))
    }
  }
  const addPicked = async (): Promise<void> => {
    try {
      for (const i of picked) {
        const c = cands?.[i]
        if (c) await reproApi.createTarget(paperId, { name: c.label, value: c.value, std: c.std, unit: c.unit, page: c.page, ord: c.ord, quote: c.quote, source: c.kind === 'paragraph' ? 'text' : 'table' })
      }
      setPicked(new Set())
      reload()
      toast.success('已添加', '请核对名称和含义，必要时修改')
    } catch (err) {
      toast.error('添加失败', errorMessage(err))
    }
  }
  const addManual = async (): Promise<void> => {
    const value = Number(form.value)
    if (!form.name.trim() || form.value.trim() === '' || !Number.isFinite(value)) return toast.error('请填写名称和数值')
    try {
      await reproApi.createTarget(paperId, { name: form.name.trim(), value, std: form.std.trim() ? Number(form.std) : null, unit: form.unit.trim(), source: 'user' })
      setForm({ name: '', value: '', std: '', unit: '' })
      reload()
    } catch (err) {
      toast.error('添加失败', errorMessage(err))
    }
  }

  return (
    <section className="pd-repro-section">
      <h3 className="pd-repro-h3">论文里报告的目标数值</h3>
      <p className="pd-aux pd-muted">选出你要复现的数值。可以从论文文字里的候选挑选，也可以手动填写；候选只是按规则找出的数字，含义需要你自己确认。</p>
      {targets.length === 0 ? (
        <p className="pd-muted">还没有目标数值。</p>
      ) : (
        <table className="pd-repro-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>论文值</th>
              <th>出处</th>
              <th>相对容差</th>
              <th>可比较</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {targets.map((t) => (
              <TargetRow key={t.id} t={t} paperId={paperId} onChanged={reload} />
            ))}
          </tbody>
        </table>
      )}
      <div className="pd-repro-form">
        <Input value={form.name} onChange={(v) => setForm({ ...form, name: v })} placeholder="指标名，如 ANC" aria-label="手动添加：指标名" />
        <Input value={form.value} onChange={(v) => setForm({ ...form, value: v })} placeholder="数值" aria-label="手动添加：数值" />
        <Input value={form.std} onChange={(v) => setForm({ ...form, std: v })} placeholder="标准差，可选" aria-label="手动添加：标准差" />
        <Input value={form.unit} onChange={(v) => setForm({ ...form, unit: v })} placeholder="单位，如 %" aria-label="手动添加：单位" />
        <Button size="sm" icon={<Plus size={14} />} onClick={() => void addManual()}>
          手动添加
        </Button>
        <Button size="sm" onClick={() => void findCandidates()}>
          从论文文字里找候选
        </Button>
      </div>
      {cands && (
        <div className="pd-repro-cands">
          {cands.length === 0 ? (
            <p className="pd-muted">没有找到候选数字，请手动填写。</p>
          ) : (
            <>
              <ul>
                {cands.map((c, i) => (
                  <li key={`${c.ord}-${i}`}>
                    <label>
                      <input
                        type="checkbox"
                        checked={picked.has(i)}
                        onChange={(e) => {
                          const next = new Set(picked)
                          if (e.target.checked) next.add(i)
                          else next.delete(i)
                          setPicked(next)
                        }}
                      />{' '}
                      <strong>{c.label}</strong> = {formatNum(c.value)}
                      {c.std !== null && ` ± ${formatNum(c.std)}`}
                      {c.unit}
                      <span className="pd-aux pd-muted"> · 第 {c.page} 页 · {c.quote.length > 90 ? `${c.quote.slice(0, 90)}…` : c.quote}</span>
                    </label>
                  </li>
                ))}
              </ul>
              <Button size="sm" variant="primary" disabled={picked.size === 0} onClick={() => void addPicked()}>
                添加所选 {picked.size}
              </Button>
            </>
          )}
        </div>
      )}
    </section>
  )
}

// ---------------------------------------------------------------- 对比表
function CompareTable({ run, cmp, reload }: { run: ReproRun; cmp: CompareResult; reload: () => void }) {
  const names = (run.metrics ?? []).map((m) => m.name)
  const setMap = async (targetId: string, metric: string): Promise<void> => {
    try {
      await reproApi.setMapping(run.id, targetId, metric || null)
      reload()
    } catch (err) {
      toast.error('保存对应关系失败', errorMessage(err))
    }
  }
  return (
    <>
      <table className="pd-repro-table">
        <thead>
          <tr>
            <th>指标</th>
            <th>论文值</th>
            <th>我的结果</th>
            <th>差值</th>
            <th>相对误差</th>
            <th>状态</th>
          </tr>
        </thead>
        <tbody>
          {cmp.items.map((it) => (
            <tr key={it.target.id} data-status={it.status}>
              <td>{it.target.name}</td>
              <td className="pd-repro-num">
                {formatNum(it.target.value)}
                {it.target.std !== null && ` ± ${formatNum(it.target.std)}`}
                {it.target.unit}
              </td>
              <td>
                <Select
                  aria-label={`为“${it.target.name}”选择对应的结果指标`}
                  size="sm"
                  value={run.mappings?.[it.target.id] ?? ''}
                  onChange={(v) => void setMap(it.target.id, v)}
                  options={[{ value: '', label: it.mapping === 'auto' && it.metric ? `自动：${it.metric.name}` : '自动对应' }, ...names.map((n) => ({ value: n, label: n }))]}
                />
                {it.metric && (
                  <span className="pd-repro-num pd-aux">
                    {formatNum(it.metric.value)}
                    {it.metric.std !== null && ` ± ${formatNum(it.metric.std)}`}
                  </span>
                )}
                {it.metric?.source && <div className="pd-aux pd-muted">{sourceLabel(it.metric.source)}，运行 {it.metric.source.run_id.slice(0, 8)}</div>}
              </td>
              <td className="pd-repro-num">{formatNum(it.diff)}</td>
              <td className="pd-repro-num">{formatRel(it.rel_error)}</td>
              <td>
                <span className="pd-repro-badge" data-compare={it.status}>
                  {COMPARE_LABEL[it.status]}
                </span>
                {it.notes.map((n) => (
                  <div key={n} className="pd-aux pd-muted">
                    {n}
                  </div>
                ))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="pd-aux pd-muted">{cmp.note}</p>
      {cmp.reasons.length > 0 && (
        <div className="pd-repro-reasons">
          <h4 className="pd-repro-h4">可能相关的线索</h4>
          <ul>
            {cmp.reasons.map((r) => (
              <li key={r.text}>
                <span className="pd-repro-badge" data-kind={r.kind}>
                  {r.kind === 'fact' ? '事实' : '推测'}
                </span>{' '}
                {r.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  )
}

// ---------------------------------------------------------------- 我的结果
function RunPanel({ runId, onDeleted }: { runId: string; onDeleted: () => void }) {
  const [run, setRun] = useState<ReproRun | null>(null)
  const [cmp, setCmp] = useState<CompareResult | null>(null)
  const [envText, setEnvText] = useState('')
  const [conclusion, setConclusion] = useState('')
  const [notes, setNotes] = useState('')

  const load = useCallback(async (): Promise<void> => {
    try {
      const [r, c] = await Promise.all([reproApi.run(runId), reproApi.compare(runId)])
      setRun(r)
      setCmp(c)
      setEnvText(envToText(r.env))
      setConclusion(r.conclusion)
      setNotes(r.notes)
    } catch (err) {
      toast.error('读取结果失败', errorMessage(err))
    }
  }, [runId])
  useEffect(() => {
    setRun(null)
    setCmp(null)
    void load()
  }, [load])

  const saveMeta = async (): Promise<void> => {
    try {
      await reproApi.patchRun(runId, { env: parseEnvText(envText), notes, conclusion })
      toast.success('已保存')
      void load()
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    }
  }
  const exportReport = async (): Promise<void> => {
    try {
      await reproApi.patchRun(runId, { env: parseEnvText(envText), notes, conclusion })
      downloadText(await reproApi.report(runId))
    } catch (err) {
      toast.error('导出失败', errorMessage(err))
    }
  }
  if (!run || !cmp) return <Skeleton height={16} />
  return (
    <div className="pd-repro-run">
      <CompareTable run={run} cmp={cmp} reload={() => void load()} />
      <div className="pd-repro-meta">
        <label className="pd-field-label" htmlFor="repro-env">
          运行环境，每行 键=值，建议记录 Python/框架/CUDA 版本、随机种子、commit、命令
        </label>
        <TextArea id="repro-env" value={envText} onChange={setEnvText} rows={4} placeholder={'python=3.8\ntorch=1.9.0\nseed=1\ncommit=abc1234'} style={{ fontFamily: 'var(--font-mono)' }} />
        <label className="pd-field-label" htmlFor="repro-notes">
          备注
        </label>
        <TextArea id="repro-notes" value={notes} onChange={setNotes} rows={2} />
        <label className="pd-field-label" htmlFor="repro-concl">
          你的结论，系统不会替你判断“复现成功”或“失败”
        </label>
        <TextArea id="repro-concl" value={conclusion} onChange={setConclusion} rows={3} placeholder="例如：ANC 在容差内，F1 差 5 个点，怀疑数据划分不同…" />
        <div className="pd-row" style={{ gap: 8, justifyContent: 'flex-end' }}>
          <Button size="sm" variant="ghost" onClick={() => void reproApi.deleteRun(runId).then(onDeleted, (err) => toast.error('删除失败', errorMessage(err)))}>
            删除这份结果
          </Button>
          <Button size="sm" onClick={() => void saveMeta()}>
            保存
          </Button>
          <Button size="sm" icon={<Download size={14} />} onClick={() => void exportReport()}>
            导出复现报告 (.md)
          </Button>
        </div>
      </div>
    </div>
  )
}

function ImportRun({ paperId, repoId, onCreated }: { paperId: string; repoId: string; onCreated: (id: string) => void }) {
  const [name, setName] = useState('')
  const [content, setContent] = useState('')
  const [path, setPath] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async (): Promise<void> => {
    setBusy(true)
    try {
      const run = await reproApi.createRun(paperId, { name, content: content.trim() || undefined, path: path.trim() || undefined, repo_id: repoId })
      for (const w of run.warnings ?? []) toast.info('导入提示', w)
      setContent('')
      setPath('')
      setName('')
      onCreated(run.id)
    } catch (err) {
      toast.error('导入失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="pd-repro-import">
      <Input value={name} onChange={setName} placeholder="给这次结果起个名字，可选" aria-label="结果名称" />
      <TextArea value={content} onChange={setContent} rows={5} placeholder={'粘贴你的结果：CSV / TSV / Markdown 表格 / JSON\n例如：\nmetric,value\nANC,0.52\nF1,85.0'} style={{ fontFamily: 'var(--font-mono)' }} aria-label="粘贴结果内容" />
      <Input value={path} onChange={setPath} placeholder="或者填本地文件路径，支持 .csv .tsv .json .txt .log .md，不超过 2MB" aria-label="结果文件路径" />
      <div className="pd-row" style={{ justifyContent: 'flex-end' }}>
        <Button variant="primary" loading={busy} disabled={!content.trim() && !path.trim()} onClick={() => void submit()}>
          导入并对比
        </Button>
      </div>
    </div>
  )
}

/** C：导入用户自己跑出的结果，与论文报告的数值逐项对比。系统只陈述差值与线索，不替用户断言“复现成功”。 */
export function ResultsView({ paperId, repoId }: { paperId: string; repoId: string }) {
  const [targets, setTargets] = useState<ReproTarget[] | null>(null)
  const [runs, setRuns] = useState<ReproRun[] | null>(null)
  const [runId, setRunId] = useState<string | null>(null)

  const loadTargets = useCallback(async (): Promise<void> => {
    try {
      setTargets((await reproApi.targets(paperId)).items)
    } catch (err) {
      toast.error('读取目标数值失败', errorMessage(err))
    }
  }, [paperId])
  const loadRuns = useCallback(
    async (select?: string): Promise<void> => {
      try {
        const items = (await reproApi.runs(paperId)).items
        setRuns(items)
        setRunId((cur) => select ?? (cur && items.some((r) => r.id === cur) ? cur : (items[0]?.id ?? null)))
      } catch (err) {
        toast.error('读取结果失败', errorMessage(err))
      }
    },
    [paperId]
  )
  useEffect(() => {
    void loadTargets()
    void loadRuns()
  }, [loadTargets, loadRuns])

  if (!targets || !runs) return <Skeleton height={16} />
  return (
    <div className="pd-repro-panel">
      <p className="pd-aux pd-muted">在你自己的环境里跑完后，把结果带回来。Marginalia 只做对比：给出差值、相对误差和是否在容差内，是否算复现成功由你自己判断。</p>
      <Targets paperId={paperId} targets={targets} reload={() => void loadTargets()} />
      <section className="pd-repro-section">
        <h3 className="pd-repro-h3">我的结果</h3>
        {runs.length > 0 && (
          <div className="pd-repro-runbar">
            <Select
              aria-label="选择一份结果"
              size="sm"
              value={runId ?? ''}
              onChange={setRunId}
              options={runs.map((r) => ({ value: r.id, label: `${r.name} · ${r.metric_count ?? 0} 个指标 · ${r.created_at.slice(0, 16).replace('T', ' ')}` }))}
            />
          </div>
        )}
        <ImportRun paperId={paperId} repoId={repoId} onCreated={(id) => void loadRuns(id)} />
        {runId ? (
          targets.length === 0 ? (
            <EmptyState title="先添加论文里的目标数值" description="对比需要知道论文报告了什么数值。" />
          ) : (
            <RunPanel key={runId} runId={runId} onDeleted={() => void loadRuns()} />
          )
        ) : (
          <p className="pd-muted">还没有导入结果。</p>
        )}
      </section>
    </div>
  )
}
