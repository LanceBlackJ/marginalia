import { api } from '@/lib/api'
import type { JobDetail } from '@/lib/types'

/** 每秒查一次后台任务，直到结束（完成/失败/取消）。token.cancelled 变为 true 时提前返回 null（比如组件已卸载）。 */
export async function waitJob(id: string, onTick?: (job: JobDetail) => void, token: { cancelled: boolean } = { cancelled: false }): Promise<JobDetail | null> {
  for (;;) {
    if (token.cancelled) return null
    const job = await api.jobs.get(id)
    onTick?.(job)
    if (job.status === 'completed' || job.status === 'failed' || job.status === 'cancelled') return job
    await new Promise((r) => window.setTimeout(r, 1000))
  }
}
