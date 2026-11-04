import { useCallback, useEffect, useRef, useState } from 'react'
import { Trash2 } from 'lucide-react'
import { Button, Dialog, IconButton, Input, Switch, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { reproApi } from '@/lib/api.repro'
import type { ExecLimits, ExecMetrics, ExecOptions, ExecRun, PlanStep } from '@/lib/types.repro'
import { waitJob } from './waitJob'
import { EXEC_STATUS_LABEL, LIMIT_LABEL, execActive, formatNum, limitsText, parseLimit, sourceLabel } from './reproUtils'

const LIMIT_KEYS: Array<keyof ExecLimits> = ['memory_mb', 'cpus', 'pids', 'timeout_s']

/** 运行前的确认框：写清命令、镜像、是否联网、资源上限。点“确认运行”之前什么都不会启动。 */
export function RunDialog({ step, onClose, onStarted }: { step: PlanStep | null; onClose: () => void; onStarted: (run: ExecRun) => void }) {
  const [opts, setOpts] = useState<ExecOptions | null>(null)
  const [image, setImage] = useState('')
  const [network, setNetwork] = useState(false)
  const [limits, setLimits] = useState<Record<keyof ExecLimits, string> | null>(null)
  const [pulling, setPulling] = useState(false)
  const [busy, setBusy] = useState(false)

  const loadOpts = useCallback(async (img?: string): Promise<void> => {
    try {
      const o = await reproApi.execOptions(img)
      setOpts(o)
      setImage(o.image)
      setLimits((cur) => cur ?? (Object.fromEntries(LIMIT_KEYS.map((k) => [k, String(o.defaults[k])])) as Record<keyof ExecLimits, string>))
    } catch (err) {
      toast.error('读取运行选项失败', errorMessage(err))
    }
  }, [])

  useEffect(() => {
    if (!step) return
    setOpts(null)
    setLimits(null)
    setNetwork(false)
    void loadOpts()
  }, [step, loadOpts])

  if (!step) return null
  const parsed = opts && limits ? LIMIT_KEYS.map((k) => parseLimit(limits[k], opts.minima[k], opts.maxima[k])) : []
  const limitsOk = parsed.length === 4 && parsed.every((v) => v !== null)
  const canRun = !!opts && opts.available && opts.image_present && limitsOk && !busy && !pulling

  const pull = async (): Promise<void> => {
    if (!opts?.pull) return
    setPulling(true)
    try {
      const { job_id } = await reproApi.pullImage(opts.pull.image)
      const job = await waitJob(job_id)
      if (job?.status !== 'completed') toast.error('拉取镜像失败', job?.error ?? '详情见任务日志')
      await loadOpts(opts.pull.image)
    } catch (err) {
      toast.error('拉取镜像失败', errorMessage(err))
    } finally {
      setPulling(false)
    }
  }

  const start = async (): Promise<void> => {
    if (!opts || !limits || !limitsOk) return
    setBusy(true)
    try {
      const l = Object.fromEntries(LIMIT_KEYS.map((k, i) => [k, parsed[i] as number])) as unknown as ExecLimits
      onStarted(await reproApi.execute(step.id, { command: step.command, cwd: step.cwd, image: opts.image, network, limits: l }))
      onClose()
    } catch (err) {
      toast.error('无法运行', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="在隔离环境中运行"
      width={560}
      footer={
        <>
          <Button onClick={onClose}>取消</Button>
          <Button variant="primary" disabled={!canRun} onClick={() => void start()}>
            确认运行
          </Button>
        </>
      }
    >
      <div className="pd-repro-run-dialog">
        <div>
          <div className="pd-repro-run-label">命令{step.cwd ? `，在仓库目录 ${step.cwd} 下` : ''}</div>
          <pre className="pd-repro-cmd">{step.command}</pre>
        </div>
        {!opts || !limits ? (
          <p className="pd-aux pd-muted">正在读取…</p>
        ) : (
          <>
            <div>
              <div className="pd-repro-run-label">镜像</div>
              <div className="pd-row" style={{ gap: 8 }}>
                <Input value={image} onChange={setImage} aria-label="镜像" style={{ fontFamily: 'var(--font-mono)' }} onEnter={() => void loadOpts(image)} />
                <Button size="sm" onClick={() => void loadOpts(image)}>
                  检查
                </Button>
              </div>
              {opts.image_present ? (
                <p className="pd-aux pd-muted">本机已有这个镜像，不需要下载。</p>
              ) : opts.pull ? (
                <div className="pd-repro-pull">
                  <p style={{ margin: 0 }}>
                    本机没有镜像 {opts.pull.image}，需要拉取，{opts.pull.size_hint}。
                  </p>
                  <Button size="sm" disabled={pulling} onClick={() => void pull()}>
                    {pulling ? '正在拉取…' : '同意并拉取镜像'}
                  </Button>
                </div>
              ) : null}
            </div>
            <div>
              <Switch checked={network} onChange={setNetwork} label="允许这一步联网" />
              <p className="pd-aux pd-muted">{network ? '容器可以访问网络，这次会记入日志。只在需要安装依赖时才勾选。' : '默认不联网。'}</p>
            </div>
            <div>
              <div className="pd-repro-run-label">资源上限，只能调小</div>
              <div className="pd-repro-limits">
                {LIMIT_KEYS.map((k, i) => (
                  <label key={k} className="pd-repro-limit">
                    <span className="pd-aux pd-muted">{LIMIT_LABEL[k]}</span>
                    <Input
                      value={limits[k]}
                      onChange={(v) => setLimits({ ...limits, [k]: v })}
                      invalid={parsed[i] === null}
                      inputMode="decimal"
                      aria-label={LIMIT_LABEL[k]}
                      placeholder={`${opts.minima[k]} 到 ${opts.maxima[k]}`}
                    />
                  </label>
                ))}
              </div>
            </div>
            <ul className="pd-aux pd-muted pd-repro-mounts">
              {opts.mounts.map((m) => (
                <li key={m}>{m}</li>
              ))}
              <li>容器里没有 API Key 和任何环境变量里的密钥，以非 root 身份运行，运行结束后容器会被删除。</li>
            </ul>
          </>
        )}
      </div>
    </Dialog>
  )
}

function MetricsPreview({ run, onImported }: { run: ExecRun; onImported: (r: ExecRun) => void }) {
  const [data, setData] = useState<ExecMetrics | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    reproApi
      .execMetrics(run.id)
      .then(setData)
      .catch((err) => toast.error('解析数值失败', errorMessage(err)))
  }, [run.id])
  if (!data) return null
  const doImport = async (): Promise<void> => {
    setBusy(true)
    try {
      await reproApi.importExec(run.id, {})
      toast.success('已导入为一条结果', '到“结果对比”里选择它，和论文数值对比')
      onImported(run)
    } catch (err) {
      toast.error('导入失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="pd-repro-metrics">
      {data.metrics.length > 0 && (
        <table className="pd-repro-table">
          <thead>
            <tr>
              <th>指标</th>
              <th>数值</th>
              <th>来源</th>
            </tr>
          </thead>
          <tbody>
            {data.metrics.map((m) => (
              <tr key={m.name}>
                <td>{m.name}</td>
                <td className="pd-repro-num">{formatNum(m.value)}</td>
                <td className="pd-aux pd-muted">{sourceLabel(m.source)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {data.warnings.map((w) => (
        <p key={w} className="pd-aux pd-muted" style={{ margin: 0 }}>
          {w}
        </p>
      ))}
      {data.metrics.length > 0 &&
        (run.imported_run_id ? (
          <p className="pd-aux pd-muted" style={{ margin: 0 }}>
            已导入过一次。
          </p>
        ) : (
          <div>
            <Button size="sm" variant="primary" disabled={busy} onClick={() => void doImport()}>
              导入为结果
            </Button>
          </div>
        ))}
    </div>
  )
}

function ExecItem({ run, onChange, onRemoved, onImported }: { run: ExecRun; onChange: (r: ExecRun) => void; onRemoved: (id: string) => void; onImported: () => void }) {
  const [open, setOpen] = useState(execActive(run))
  const [full, setFull] = useState<ExecRun>(run)
  const logRef = useRef<HTMLPreElement>(null)

  useEffect(() => {
    if (!open) return
    let alive = true
    const tick = async (): Promise<void> => {
      try {
        const r = await reproApi.exec(run.id)
        if (!alive) return
        setFull(r)
        onChange(r)
        if (execActive(r)) timer = window.setTimeout(() => void tick(), 1000)
      } catch {
        /* 下一次刷新再试 */
      }
    }
    let timer = window.setTimeout(() => void tick(), 0)
    return () => {
      alive = false
      window.clearTimeout(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, run.id])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [full.log])

  const cancel = async (): Promise<void> => {
    try {
      onChange(await reproApi.cancelExec(run.id))
    } catch (err) {
      toast.error('取消失败', errorMessage(err))
    }
  }
  const remove = async (): Promise<void> => {
    try {
      await reproApi.deleteExec(run.id)
      onRemoved(run.id)
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }
  const active = execActive(run)
  return (
    <li className="pd-repro-exec" data-status={run.status}>
      <div className="pd-repro-step-head">
        <button type="button" className="pd-link" onClick={() => setOpen(!open)}>
          {run.title || '运行'}
        </button>
        <span className="pd-repro-badge" data-exec={run.status}>
          {EXEC_STATUS_LABEL[run.status]}
        </span>
        <span className="pd-aux pd-muted">
          {run.created_at.replace('T', ' ').slice(0, 16)} · {run.image} · {run.network ? '联网' : '不联网'}
        </span>
        <span className="pd-spacer" />
        {active ? (
          <Button size="sm" onClick={() => void cancel()}>
            取消运行
          </Button>
        ) : (
          <IconButton icon={<Trash2 size={14} />} label="删除这条运行记录" size="sm" onClick={() => void remove()} />
        )}
      </div>
      {run.reason && run.status !== 'succeeded' && <p className="pd-repro-exec-reason">{run.reason}</p>}
      {open && (
        <>
          <p className="pd-aux pd-muted" style={{ margin: 0 }}>
            {limitsText(run.limits)}
          </p>
          <pre className="pd-repro-log-box" ref={logRef} aria-label="运行日志">
            {(full.log ?? []).join('\n') || (active ? '等待输出…' : '没有输出')}
          </pre>
          {run.files.length > 0 && (
            <p className="pd-aux pd-muted" style={{ margin: 0 }}>
              输出文件：{run.files.slice(0, 8).map((f) => f.path).join('、')}
              {run.files.length > 8 ? ` 等 ${run.files.length} 个` : ''}
            </p>
          )}
          {run.status === 'succeeded' && <MetricsPreview run={full} onImported={onImported} />}
        </>
      )}
    </li>
  )
}

/** 隔离运行记录：实时日志、取消、输出文件，成功的运行可以把真正解析出的数值导入为结果。 */
export function ExecRuns({ paperId, repoId, reloadKey, onImported }: { paperId: string; repoId: string; reloadKey: number; onImported: () => void }) {
  const [items, setItems] = useState<ExecRun[] | null>(null)
  useEffect(() => {
    reproApi
      .execs(paperId, repoId)
      .then((r) => setItems(r.items))
      .catch((err) => toast.error('读取运行记录失败', errorMessage(err)))
  }, [paperId, repoId, reloadKey])
  if (!items || items.length === 0) return null
  return (
    <div className="pd-repro-panel">
      <h4 className="pd-repro-sub">隔离运行记录</h4>
      <ul className="pd-repro-steps">
        {items.map((r) => (
          <ExecItem
            key={r.id}
            run={r}
            onChange={(n) => setItems((cur) => cur?.map((x) => (x.id === n.id ? { ...x, ...n, log: undefined } : x)) ?? cur)}
            onRemoved={(id) => setItems((cur) => cur?.filter((x) => x.id !== id) ?? cur)}
            onImported={onImported}
          />
        ))}
      </ul>
    </div>
  )
}
