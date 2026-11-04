import { useEffect, useState } from 'react'
import { Play } from 'lucide-react'
import { Button, EmptyState } from '@/components/ui'
import { reproApi } from '@/lib/api.repro'
import type { SandboxInfo } from '@/lib/types.repro'
import { useCodeStore } from '../codeStore'
import { Disclosure, StepSection, ViewFrame, ViewHead } from '../ViewFrame'
import { AssessView } from './AssessView'
import { PlanView } from './PlanView'
import { ResultsView } from './ResultsView'
import './repro.css'

type Sub = 'assess' | 'plan' | 'results'

const STEPS: Array<{ value: Sub; title: string; summary: string }> = [
  { value: 'assess', title: '可行性评估', summary: '只读静态分析：环境、入口、数据、资源、参数、风险，每项带证据' },
  { value: 'plan', title: '复现计划', summary: '可编辑的步骤清单；每个下载、安装、运行步骤都要你逐项批准，可在 Docker 中运行' },
  { value: 'results', title: '结果对比', summary: '导入你自己跑出的结果，与论文里报告的数值逐项对比' }
]

/** 隔离环境的检测结果：Docker 在运行才能点“在隔离环境中运行”，否则置灰并写明原因。 */
function useSandbox(): SandboxInfo | null {
  const [info, setInfo] = useState<SandboxInfo | null>(null)
  useEffect(() => {
    let alive = true
    reproApi
      .sandbox()
      .then((s) => alive && setInfo(s))
      .catch(() => alive && setInfo(null))
    return () => {
      alive = false
    }
  }, [])
  return info
}

export function ReproView() {
  const paperId = useCodeStore((s) => s.paperId)
  const repoId = useCodeStore((s) => s.repoId)
  const [open, setOpen] = useState<Sub>('assess')
  const info = useSandbox()
  if (!paperId || !repoId) {
    return <EmptyState title="还没有可用的代码仓库" description="先在“代码”里获取并分析代码，才能做复现评估。" />
  }
  const found = info ? [info.docker.found && `Docker ${info.docker.running ? '运行中' : '未运行'}`, info.wsl2.found && `WSL2 ${info.wsl2.distros.slice(0, 2).join('、')}`].filter(Boolean) : []
  const toggle = (s: Sub): void => setOpen((cur) => (cur === s ? cur : s))

  return (
    <ViewFrame>
      <div className="pd-repro">
        <ViewHead
          title="复现"
          desc="按“评估 → 计划 → 结果对比”三步来。评估是静态分析，不运行代码。要运行，只能在 Docker 容器里：默认不联网，代码只读，限制内存和时长，每次都要你确认。也可以导出计划，自己运行后把结果带回来对比。"
          actions={
            <Button icon={<Play size={14} />} disabled={!info?.can_run_here} title={info?.reason ?? '正在检测隔离环境'} onClick={() => setOpen('plan')}>
              在隔离环境中运行
            </Button>
          }
        />
        <div className="pd-repro-env">
          <span className="pd-aux pd-muted">{info ? (found.length ? `已检测到：${found.join('、')}。` : '这台机器没有检测到 Docker 或 WSL2。') : '正在检测隔离环境…'}</span>
          {info && (
            <Disclosure label="隔离环境的说明">
              <p className="pd-aux pd-muted" style={{ margin: 0 }}>
                {info.reason}
              </p>
            </Disclosure>
          )}
        </div>
        <div className="pd-steps">
          {STEPS.map((s, i) => (
            <StepSection key={s.value} index={i + 1} title={s.title} summary={s.summary} open={open === s.value} onToggle={() => toggle(s.value)}>
              {s.value === 'assess' && <AssessView paperId={paperId} repoId={repoId} onNext={() => setOpen('plan')} />}
              {s.value === 'plan' && <PlanView paperId={paperId} repoId={repoId} canRun={!!info?.can_run_here} onAssess={() => setOpen('assess')} onNext={() => setOpen('results')} />}
              {s.value === 'results' && <ResultsView paperId={paperId} repoId={repoId} />}
            </StepSection>
          ))}
        </div>
      </div>
    </ViewFrame>
  )
}
