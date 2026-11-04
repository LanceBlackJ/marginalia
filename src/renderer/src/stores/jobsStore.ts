import { create } from 'zustand'
import { api } from '@/lib/api'
import { isActiveJob } from '@/lib/format'
import type { Job } from '@/lib/types'

type FinishListener = (job: Job) => void

interface JobsState {
  jobs: Job[]
  loaded: boolean
  /** 每当有任务从进行中变为终态时递增，页面据此刷新数据。 */
  finishedTick: number
  refresh: () => Promise<void>
  /** 登记本会话主动创建的任务：完成时触发监听器（如汇总提示）。 */
  track: (jobId: string) => void
  onFinish: (listener: FinishListener) => () => void
}

const tracked = new Set<string>()
const listeners = new Set<FinishListener>()

export const useJobsStore = create<JobsState>((set, get) => ({
  jobs: [],
  loaded: false,
  finishedTick: 0,

  refresh: async () => {
    const { items } = await api.jobs.list({ limit: 200 })
    const prev = new Map(get().jobs.map((j) => [j.id, j.status]))
    let finished = false
    for (const job of items) {
      const was = prev.get(job.id)
      const nowActive = isActiveJob(job.status)
      const wasActive = was === undefined ? tracked.has(job.id) : isActiveJob(was)
      if (!nowActive && wasActive) {
        finished = true
        if (tracked.has(job.id)) {
          tracked.delete(job.id)
          listeners.forEach((l) => l(job))
        }
      }
    }
    set((s) => ({ jobs: items, loaded: true, finishedTick: finished ? s.finishedTick + 1 : s.finishedTick }))
  },

  track: (jobId) => {
    tracked.add(jobId)
    void get()
      .refresh()
      .catch(() => undefined)
  },

  onFinish: (listener) => {
    listeners.add(listener)
    return () => {
      listeners.delete(listener)
    }
  }
}))

export function useActiveJobs(): Job[] {
  return useJobsStore((s) => s.jobs).filter((j) => isActiveJob(j.status))
}
