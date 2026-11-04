/** 复现（第 4 阶段）的 API 客户端。所有接口都不执行用户代码。 */
import { apiRequest } from './api'
import type {
  AssessmentState,
  CompareResult,
  ExecMetrics,
  ExecOptions,
  ExecRun,
  ExecLimits,
  ExportFile,
  PlanState,
  ReproRun,
  ReproTarget,
  SandboxInfo,
  TargetCandidate
} from './types.repro'

type Query = Record<string, string | number | boolean | null | undefined>

const get = <T>(path: string, query?: Query): Promise<T> => apiRequest<T>('GET', path, { query })
const post = <T>(path: string, body?: unknown): Promise<T> => apiRequest<T>('POST', path, { body: body ?? {} })
const patch = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PATCH', path, { body })
const put = <T>(path: string, body: unknown): Promise<T> => apiRequest<T>('PUT', path, { body })
const del = <T>(path: string): Promise<T> => apiRequest<T>('DELETE', path)

export interface TargetInput {
  name?: string
  value?: number
  std?: number | null
  unit?: string
  tol_rel?: number | null
  tol_abs?: number | null
  comparable?: boolean
  page?: number | null
  ord?: number | null
  quote?: string
  source?: 'table' | 'text' | 'user'
}

export interface StepPatch {
  title?: string
  detail?: string
  command?: string
  cwd?: string
  note?: string
  done?: boolean
}

export const reproApi = {
  sandbox: () => get<SandboxInfo>('/api/system/sandbox'),

  assessment: (paperId: string, repoId: string) => get<AssessmentState>(`/api/papers/${paperId}/repro/assessment`, { repo_id: repoId }),
  startAssessment: (paperId: string, repoId: string) => post<{ job_id: string; repo_id: string }>(`/api/papers/${paperId}/repro/assessment`, { repo_id: repoId }),

  plan: (paperId: string, repoId: string) => get<PlanState>(`/api/papers/${paperId}/repro/plan`, { repo_id: repoId }),
  generatePlan: (paperId: string, repoId: string, force = false) => post<PlanState>(`/api/papers/${paperId}/repro/plan`, { repo_id: repoId, force }),
  patchStep: (stepId: string, body: StepPatch) => patch<PlanState>(`/api/repro/steps/${stepId}`, body),
  decide: (stepId: string, decision: 'approve' | 'reject' | 'reset', note = '') => post<PlanState>(`/api/repro/steps/${stepId}/approve`, { decision, note }),
  addStep: (planId: string, body: { title: string; command?: string; detail?: string; cwd?: string; phase?: string }) => post<PlanState>(`/api/repro/plans/${planId}/steps`, body),
  deleteStep: (stepId: string) => del<PlanState>(`/api/repro/steps/${stepId}`),
  exportPlan: (planId: string, format: 'md' | 'sh') => get<ExportFile>(`/api/repro/plans/${planId}/export`, { format }),

  candidates: (paperId: string) => get<{ items: TargetCandidate[] }>(`/api/papers/${paperId}/repro/target-candidates`),
  targets: (paperId: string) => get<{ items: ReproTarget[] }>(`/api/papers/${paperId}/repro/targets`),
  createTarget: (paperId: string, body: TargetInput) => post<ReproTarget>(`/api/papers/${paperId}/repro/targets`, body),
  patchTarget: (paperId: string, id: string, body: TargetInput) => patch<ReproTarget>(`/api/papers/${paperId}/repro/targets/${id}`, body),
  deleteTarget: (paperId: string, id: string) => del<{ ok: boolean }>(`/api/papers/${paperId}/repro/targets/${id}`),

  runs: (paperId: string) => get<{ items: ReproRun[] }>(`/api/papers/${paperId}/repro/runs`),
  createRun: (paperId: string, body: { name?: string; content?: string; path?: string; repo_id?: string | null; env?: Record<string, string> }) =>
    post<ReproRun>(`/api/papers/${paperId}/repro/runs`, body),
  run: (id: string) => get<ReproRun>(`/api/repro/runs/${id}`),
  patchRun: (id: string, body: { name?: string; env?: Record<string, string>; notes?: string; conclusion?: string }) => patch<ReproRun>(`/api/repro/runs/${id}`, body),
  deleteRun: (id: string) => del<{ ok: boolean }>(`/api/repro/runs/${id}`),
  setMapping: (runId: string, targetId: string, metric: string | null) => put<ReproRun>(`/api/repro/runs/${runId}/mapping`, { target_id: targetId, metric }),
  compare: (runId: string) => get<CompareResult>(`/api/repro/runs/${runId}/compare`),
  report: (runId: string) => get<ExportFile>(`/api/repro/runs/${runId}/report`),

  execOptions: (image?: string) => get<ExecOptions>('/api/repro/exec/options', { image }),
  pullImage: (image: string) => post<{ job_id: string; image: string }>('/api/repro/images/pull', { image, confirm: true }),
  execute: (stepId: string, body: { command: string; cwd: string; image: string; network: boolean; limits: ExecLimits }) =>
    post<ExecRun>(`/api/repro/steps/${stepId}/execute`, { ...body, confirm: true }),
  execs: (paperId: string, repoId: string) => get<{ items: ExecRun[] }>(`/api/papers/${paperId}/repro/exec`, { repo_id: repoId }),
  exec: (id: string) => get<ExecRun>(`/api/repro/exec/${id}`),
  cancelExec: (id: string) => post<ExecRun>(`/api/repro/exec/${id}/cancel`),
  deleteExec: (id: string) => del<{ ok: boolean }>(`/api/repro/exec/${id}`),
  execMetrics: (id: string) => get<ExecMetrics>(`/api/repro/exec/${id}/metrics`),
  importExec: (id: string, body: { name?: string; metrics?: string[] }) => post<ReproRun>(`/api/repro/exec/${id}/import`, body)
}
