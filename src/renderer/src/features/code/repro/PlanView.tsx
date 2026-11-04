import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Download, Pencil, Play, Plus, Trash2 } from 'lucide-react'
import { Button, Dialog, EmptyState, IconButton, Input, Skeleton, TextArea, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { reproApi } from '@/lib/api.repro'
import type { Evidence, PlanState, PlanStep } from '@/lib/types.repro'
import { openPaperAt, useCodeStore } from '../codeStore'
import { ExecRuns, RunDialog } from './ExecPanel'
import { APPROVAL_LABEL, downloadText, evidenceLabel, pendingCount } from './reproUtils'

function StepEvidence({ evidence }: { evidence: Evidence[] }) {
  const navigate = useNavigate()
  const paperId = useCodeStore((s) => s.paperId)
  const goToLines = useCodeStore((s) => s.goToLines)
  const setTab = useCodeStore((s) => s.setTab)
  if (evidence.length === 0) return null
  return (
    <ul className="pd-repro-evidence">
      {evidence.map((e, i) => (
        <li key={`${evidenceLabel(e)}-${i}`}>
          <button
            type="button"
            className="pd-link"
            onClick={() => {
              if (e.type === 'code') {
                setTab('code')
                void goToLines(e.path, Math.max(1, e.line_start), Math.max(1, e.line_end))
              } else if (e.type === 'paper' && paperId) void openPaperAt(navigate, paperId, { page: e.page ?? undefined, ord: e.ord ?? undefined })
            }}
          >
            {evidenceLabel(e)}
          </button>
        </li>
      ))}
    </ul>
  )
}

function StepCard({ step, canRun, onRun, onChange, onError }: { step: PlanStep; canRun: boolean; onRun: (s: PlanStep) => void; onChange: (s: PlanState) => void; onError: (e: unknown, what: string) => void }) {
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(step.title)
  const [detail, setDetail] = useState(step.detail)
  const [command, setCommand] = useState(step.command)
  const [cwd, setCwd] = useState(step.cwd)
  const [showLog, setShowLog] = useState(false)

  useEffect(() => {
    setTitle(step.title)
    setDetail(step.detail)
    setCommand(step.command)
    setCwd(step.cwd)
  }, [step])

  const run = async (what: string, fn: () => Promise<PlanState>): Promise<void> => {
    try {
      onChange(await fn())
    } catch (err) {
      onError(err, what)
    }
  }
  const save = async (): Promise<void> => {
    await run('保存步骤', () => reproApi.patchStep(step.id, { title, detail, command, cwd }))
    setEditing(false)
    if (command !== step.command || cwd !== step.cwd) {
      if (step.approval === 'approved' || step.approval === 'rejected') toast.info('命令已修改', '之前的批准/拒绝已作废，需要重新决定')
    }
  }

  return (
    <li className="pd-repro-step" data-approval={step.approval} data-done={step.done}>
      <div className="pd-repro-step-head">
        <label className="pd-repro-check" title="标记这一步你已经在自己的环境里完成了">
          <input type="checkbox" checked={step.done} onChange={(e) => void run('更新步骤', () => reproApi.patchStep(step.id, { done: e.target.checked }))} />
        </label>
        <span className="pd-repro-step-ord">{step.ord}</span>
        <span className="pd-repro-step-title">{step.title}</span>
        <span className="pd-aux pd-muted">{step.phase_label}</span>
        <span className="pd-spacer" />
        <span className="pd-repro-badge" data-approval={step.approval}>
          {APPROVAL_LABEL[step.approval]}
        </span>
        <IconButton icon={<Pencil size={14} />} label="编辑这一步" size="sm" onClick={() => setEditing(!editing)} />
        <IconButton icon={<Trash2 size={14} />} label="删除这一步" size="sm" onClick={() => void run('删除步骤', () => reproApi.deleteStep(step.id))} />
      </div>
      {editing ? (
        <div className="pd-repro-edit">
          <Input value={title} onChange={setTitle} aria-label="步骤标题" />
          <TextArea value={detail} onChange={setDetail} rows={3} aria-label="说明" />
          <Input value={cwd} onChange={setCwd} placeholder="工作目录，相对仓库根" aria-label="工作目录" />
          <TextArea value={command} onChange={setCommand} rows={3} placeholder="命令，留空表示手动完成" aria-label="命令" style={{ fontFamily: 'var(--font-mono)' }} />
          <p className="pd-aux pd-muted">改动命令或目录后，这一步之前的批准会作废，需要重新批准。</p>
          <div className="pd-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
            <Button size="sm" onClick={() => setEditing(false)}>
              取消
            </Button>
            <Button size="sm" variant="primary" onClick={() => void save()}>
              保存
            </Button>
          </div>
        </div>
      ) : (
        <>
          {step.detail && <p className="pd-repro-step-detail">{step.detail}</p>}
          {step.command && (
            <pre className="pd-repro-cmd">
              {step.cwd ? `# 在目录 ${step.cwd} 下\n` : ''}
              {step.command}
            </pre>
          )}
          {step.note && <p className="pd-aux pd-muted">备注：{step.note}</p>}
          {(step.side_effects.length > 0 || step.risk) && (
            <p className="pd-aux pd-muted">
              {step.side_effects.length > 0 && `会产生的副作用：${step.side_effects.join('、')}。`}
              {step.risk && ` 风险：${step.risk}`}
            </p>
          )}
          <StepEvidence evidence={step.evidence} />
        </>
      )}
      {step.command.trim() && step.approval !== 'rejected' && (
        <div className="pd-repro-approve">
          <Button size="sm" icon={<Play size={14} />} disabled={!canRun} title={canRun ? undefined : '需要 Docker 在运行'} onClick={() => onRun(step)}>
            在隔离环境中运行
          </Button>
        </div>
      )}
      {step.needs_approval && (
        <div className="pd-repro-approve">
          {step.approval !== 'approved' && (
            <Button size="sm" variant="primary" onClick={() => void run('批准', () => reproApi.decide(step.id, 'approve'))}>
              批准这一步
            </Button>
          )}
          {step.approval !== 'rejected' && (
            <Button size="sm" onClick={() => void run('拒绝', () => reproApi.decide(step.id, 'reject'))}>
              拒绝
            </Button>
          )}
          {(step.approval === 'approved' || step.approval === 'rejected') && (
            <Button size="sm" variant="ghost" onClick={() => void run('撤销决定', () => reproApi.decide(step.id, 'reset'))}>
              撤销决定
            </Button>
          )}
          <span className="pd-aux pd-muted">批准只是记录你的决定，不会自动运行。</span>
          {step.approval_log.length > 0 && (
            <button type="button" className="pd-link" onClick={() => setShowLog(!showLog)}>
              {showLog ? '收起记录' : `批准记录 ${step.approval_log.length}`}
            </button>
          )}
        </div>
      )}
      {showLog && (
        <ul className="pd-repro-log pd-aux">
          {step.approval_log.map((a, i) => (
            <li key={`${a.at}-${i}`}>
              {a.at.replace('T', ' ').slice(0, 19)} · 用户{a.decision === 'approved' ? '批准' : a.decision === 'rejected' ? '拒绝' : '撤销决定'}
              {a.note ? ` · ${a.note}` : ''}
              {a.command ? ` · 当时的命令：${a.command.split('\n')[0].slice(0, 80)}` : ''}
            </li>
          ))}
        </ul>
      )}
    </li>
  )
}

/** B：复现计划与批准闸门。计划只是文档：命令类步骤要逐项批准，导出后由用户在自己的隔离环境里手动运行。 */
export function PlanView({ paperId, repoId, canRun, onAssess, onNext }: { paperId: string; repoId: string; canRun: boolean; onAssess: () => void; onNext: () => void }) {
  const [state, setState] = useState<PlanState | null>(null)
  const [confirmRegen, setConfirmRegen] = useState(false)
  const [adding, setAdding] = useState(false)
  const [newTitle, setNewTitle] = useState('')
  const [newCmd, setNewCmd] = useState('')
  const [runStep, setRunStep] = useState<PlanStep | null>(null)
  const [execKey, setExecKey] = useState(0)

  const fail = useCallback((err: unknown, what: string): void => toast.error(`${what}失败`, errorMessage(err)), [])
  const load = useCallback(async (): Promise<void> => {
    try {
      setState(await reproApi.plan(paperId, repoId))
    } catch (err) {
      fail(err, '读取计划')
    }
  }, [paperId, repoId, fail])
  useEffect(() => {
    setState(null)
    void load()
  }, [load])

  const generate = async (force: boolean): Promise<void> => {
    try {
      setState(await reproApi.generatePlan(paperId, repoId, force))
      setConfirmRegen(false)
    } catch (err) {
      setConfirmRegen(false)
      fail(err, '生成计划')
    }
  }

  const exportAs = async (format: 'md' | 'sh'): Promise<void> => {
    if (!state?.plan) return
    try {
      downloadText(await reproApi.exportPlan(state.plan.id, format))
    } catch (err) {
      fail(err, '导出')
    }
  }

  const addStep = async (): Promise<void> => {
    if (!state?.plan || !newTitle.trim()) return
    try {
      setState(await reproApi.addStep(state.plan.id, { title: newTitle.trim(), command: newCmd }))
      setNewTitle('')
      setNewCmd('')
      setAdding(false)
    } catch (err) {
      fail(err, '添加步骤')
    }
  }

  if (!state) return <Skeleton height={16} />
  const plan = state.plan
  if (!plan) {
    return (
      <div className="pd-repro-panel">
        <EmptyState
          title="还没有复现计划"
          description="计划是根据“可行性评估”里的证据生成的步骤清单：准备环境、获取数据、编译、最小验证、完整运行、对比结果。需要执行的步骤都要你逐项批准。可以导出成说明自己运行，也可以在 Docker 隔离环境中运行，每次运行前都会让你确认。"
          action={
            <div className="pd-row" style={{ gap: 8 }}>
              <Button variant="primary" onClick={() => void generate(false)}>
                生成复现计划
              </Button>
              <Button onClick={onAssess}>先去看评估</Button>
            </div>
          }
        />
      </div>
    )
  }
  const pending = pendingCount(plan.steps)
  return (
    <div className="pd-repro-panel">
      <div className="pd-repro-actions">
        <span className="pd-repro-count">
          {plan.steps.length} 步 · 待批准 {pending} · 已批准 {plan.summary.approved} · 已拒绝 {plan.summary.rejected} · 已完成 {plan.summary.done}
        </span>
        <span className="pd-spacer" />
        <Button size="sm" icon={<Download size={14} />} onClick={() => void exportAs('md')}>
          导出说明 (.md)
        </Button>
        <Button size="sm" icon={<Download size={14} />} onClick={() => void exportAs('sh')} title="只启用已批准步骤的命令，其它以注释保留">
          导出脚本 (.sh)
        </Button>
      </div>
      <p className="pd-aux pd-muted">{plan.note} 导出的脚本里，未批准的命令只以注释保留。</p>
      <ol className="pd-repro-steps">
        {plan.steps.map((s) => (
          <StepCard key={s.id} step={s} canRun={canRun} onRun={setRunStep} onChange={setState} onError={fail} />
        ))}
      </ol>
      {adding ? (
        <div className="pd-repro-edit">
          <Input value={newTitle} onChange={setNewTitle} placeholder="步骤标题" aria-label="新步骤标题" />
          <TextArea value={newCmd} onChange={setNewCmd} rows={2} placeholder="命令" aria-label="新步骤命令" style={{ fontFamily: 'var(--font-mono)' }} />
          <div className="pd-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
            <Button size="sm" onClick={() => setAdding(false)}>
              取消
            </Button>
            <Button size="sm" variant="primary" disabled={!newTitle.trim()} onClick={() => void addStep()}>
              添加
            </Button>
          </div>
        </div>
      ) : (
        <div className="pd-repro-actions">
          <Button size="sm" icon={<Plus size={14} />} onClick={() => setAdding(true)}>
            添加我自己的步骤
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirmRegen(true)}>
            重新生成计划…
          </Button>
          <span className="pd-spacer" />
          <Button onClick={onNext}>下一步：导入结果并对比</Button>
        </div>
      )}
      <ExecRuns paperId={paperId} repoId={repoId} reloadKey={execKey} onImported={onNext} />
      <RunDialog step={runStep} onClose={() => setRunStep(null)} onStarted={() => setExecKey((k) => k + 1)} />
      <Dialog
        open={confirmRegen}
        onClose={() => setConfirmRegen(false)}
        title="重新生成计划"
        width={440}
        footer={
          <>
            <Button onClick={() => setConfirmRegen(false)}>取消</Button>
            <Button variant="danger" solid onClick={() => void generate(true)}>
              覆盖并重新生成
            </Button>
          </>
        }
      >
        <p>重新生成会覆盖你对步骤的所有编辑、新增的步骤和全部批准记录，无法恢复。确定吗？</p>
      </Dialog>
    </div>
  )
}
