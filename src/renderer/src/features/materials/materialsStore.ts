import { create } from 'zustand'
import { toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { materialsApi } from '@/lib/api.materials'
import type {
  DiscoverCandidate,
  NeedsSupplementPermission,
  Recommendation,
  SupplementAsset,
  SupplementAuthorization
} from '@/lib/types.materials'
import { useJobsStore } from '@/stores/jobsStore'
import { hasActiveAssets } from './helpers'

export { hasActiveAssets }

interface MaterialsState {
  paperId: string | null
  loaded: boolean
  loading: boolean
  error: string | null
  items: SupplementAsset[]
  authorization: SupplementAuthorization | null
  recommendation: Recommendation | null
  recommending: boolean
  /** 回答时发现可能需要补充材料但尚未授权：按卡片 id 记录（SSE needs_supplement_permission） */
  needs: Record<string, NeedsSupplementPermission>

  /** 打开论文时调用：重置授权（“每次打开询问一次”），再读取补充材料列表。 */
  load: (paperId: string) => Promise<void>
  refresh: () => Promise<void>
  reset: () => void
  setAuthorization: (allow: boolean) => Promise<void>
  addFiles: (paths: string[]) => Promise<void>
  addUrl: (url: string) => Promise<boolean>
  linkCandidates: (candidates: DiscoverCandidate[]) => Promise<void>
  approve: (id: string) => Promise<void>
  reject: (id: string) => Promise<void>
  remove: (id: string) => Promise<void>
  setCategory: (id: string, category: string) => Promise<void>
  confirmMatch: (id: string) => Promise<void>
  retry: (id: string) => Promise<void>
  analyze: (id: string) => Promise<void>
  recommend: (question: string) => Promise<void>
  noteNeeds: (n: NeedsSupplementPermission) => void
}

let timer: number | undefined

export const useMaterialsStore = create<MaterialsState>((set, get) => {
  const schedule = (): void => {
    window.clearTimeout(timer)
    const { items, paperId } = get()
    if (paperId && hasActiveAssets(items)) {
      timer = window.setTimeout(() => void get().refresh(), 1500)
    }
  }

  const track = (jobId: string): void => useJobsStore.getState().track(jobId)

  return {
    paperId: null,
    loaded: false,
    loading: false,
    error: null,
    items: [],
    authorization: null,
    recommendation: null,
    recommending: false,
    needs: {},

    load: async (paperId) => {
      window.clearTimeout(timer)
      set({ paperId, loaded: false, loading: true, error: null, items: [], authorization: null, recommendation: null, needs: {} })
      try {
        await materialsApi.authorization.set(paperId, null) // 重新打开 → 重新询问
        const list = await materialsApi.list(paperId)
        if (get().paperId !== paperId) return
        set({ items: list.items, authorization: list.authorization, loaded: true, loading: false })
        schedule()
      } catch (err) {
        if (get().paperId === paperId) set({ loading: false, loaded: true, error: errorMessage(err) })
      }
    },

    refresh: async () => {
      const paperId = get().paperId
      if (!paperId) return
      try {
        const list = await materialsApi.list(paperId)
        if (get().paperId !== paperId) return
        set({ items: list.items, authorization: list.authorization, error: null })
      } catch (err) {
        if (get().paperId === paperId) set({ error: errorMessage(err) })
      }
      schedule()
    },

    reset: () => {
      window.clearTimeout(timer)
      set({ paperId: null, loaded: false, loading: false, error: null, items: [], authorization: null, recommendation: null, needs: {} })
    },

    setAuthorization: async (allow) => {
      const paperId = get().paperId
      if (!paperId) return
      try {
        const authorization = await materialsApi.authorization.set(paperId, allow)
        if (get().paperId === paperId) set({ authorization })
      } catch (err) {
        toast.error('设置失败', errorMessage(err))
      }
    },

    addFiles: async (paths) => {
      const paperId = get().paperId
      if (!paperId || paths.length === 0) return
      try {
        const { job_id } = await materialsApi.addFiles(paperId, paths)
        track(job_id)
        toast.info('正在添加补充材料', `共 ${paths.length} 个文件，解析完成后会出现在列表里。`)
        await get().refresh()
      } catch (err) {
        toast.error('添加失败', errorMessage(err))
      }
    },

    addUrl: async (url) => {
      const paperId = get().paperId
      if (!paperId) return false
      try {
        const res = await materialsApi.addUrl(paperId, url)
        res.items.forEach((a) => a.job_id && track(a.job_id))
        if (res.skipped_existing) toast.info('这个链接已经关联过了')
        await get().refresh()
        return true
      } catch (err) {
        toast.error('添加失败', errorMessage(err))
        return false
      }
    },

    linkCandidates: async (candidates) => {
      const paperId = get().paperId
      if (!paperId || candidates.length === 0) return
      try {
        const res = await materialsApi.linkRemote(paperId, candidates)
        res.items.forEach((a) => a.job_id && track(a.job_id))
        const waiting = res.items.filter((a) => a.download_status === 'waiting_approval').length
        toast.success(`已关联 ${res.items.length} 个补充材料`, waiting ? `其中 ${waiting} 个文件较大，需要你批准后才会下载。` : undefined)
        await get().refresh()
      } catch (err) {
        toast.error('关联失败', errorMessage(err))
      }
    },

    approve: async (id) => {
      try {
        const a = await materialsApi.approve(id)
        if (a.job_id) track(a.job_id)
        await get().refresh()
      } catch (err) {
        toast.error('批准失败', errorMessage(err))
      }
    },

    reject: async (id) => {
      try {
        await materialsApi.reject(id)
        await get().refresh()
      } catch (err) {
        toast.error('操作失败', errorMessage(err))
      }
    },

    remove: async (id) => {
      const prev = get().items
      set({ items: prev.filter((a) => a.id !== id) })
      try {
        await materialsApi.remove(id)
      } catch (err) {
        set({ items: prev })
        toast.error('删除失败', errorMessage(err))
      }
    },

    setCategory: async (id, category) => {
      try {
        const a = await materialsApi.patch(id, { category })
        set((s) => ({ items: s.items.map((x) => (x.id === id ? a : x)) }))
      } catch (err) {
        toast.error('修改失败', errorMessage(err))
      }
    },

    confirmMatch: async (id) => {
      try {
        const a = await materialsApi.patch(id, { match_status: '用户确认' })
        set((s) => ({ items: s.items.map((x) => (x.id === id ? a : x)) }))
      } catch (err) {
        toast.error('操作失败', errorMessage(err))
      }
    },

    retry: async (id) => {
      try {
        const a = await materialsApi.retry(id)
        if (a.job_id) track(a.job_id)
        set((s) => ({ items: s.items.map((x) => (x.id === id ? a : x)) }))
        schedule()
      } catch (err) {
        toast.error('重试失败', errorMessage(err))
      }
    },

    analyze: async (id) => {
      try {
        const { job_id } = await materialsApi.analyze(id)
        track(job_id)
        toast.info('AI 正在分析这个补充材料')
        const waitDone = async (): Promise<void> => {
          for (let i = 0; i < 120; i++) {
            await new Promise((r) => window.setTimeout(r, 1500))
            const jobs = useJobsStore.getState().jobs
            const j = jobs.find((x) => x.id === job_id)
            if (j && !['pending', 'running'].includes(j.status)) break
          }
          await get().refresh()
        }
        void waitDone()
      } catch (err) {
        toast.error('分析失败', errorMessage(err))
      }
    },

    recommend: async (question) => {
      const paperId = get().paperId
      if (!paperId) return
      set({ recommending: true })
      try {
        const recommendation = await materialsApi.recommend(paperId, question)
        if (get().paperId === paperId) set({ recommendation })
      } catch (err) {
        toast.error('推荐失败', errorMessage(err))
      } finally {
        set({ recommending: false })
      }
    },

    noteNeeds: (n) => set((s) => ({ needs: { ...s.needs, [n.card_id]: n } }))
  }
})
