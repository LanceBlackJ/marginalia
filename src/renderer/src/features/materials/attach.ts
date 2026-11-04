import { toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { materialsApi } from '@/lib/api.materials'
import type { ImportResultItem } from '@/lib/types'
import { useJobsStore } from '@/stores/jobsStore'
import { canAttachAsSupplement, LOCAL_PATH } from './helpers'

export { canAttachAsSupplement }

export async function attachAsSupplement(it: ImportResultItem): Promise<boolean> {
  const target = it.target_paper_id
  if (!target) return false
  try {
    if (LOCAL_PATH.test(it.input)) {
      const { job_id } = await materialsApi.addFiles(target, [it.input])
      useJobsStore.getState().track(job_id)
    } else {
      const res = await materialsApi.addUrl(target, it.input)
      res.items.forEach((a) => a.job_id && useJobsStore.getState().track(a.job_id))
    }
    toast.success('已作为补充材料添加', '解析完成后，可在这篇论文的“材料”标签里查看。', {
      label: '打开论文',
      onClick: () => {
        window.location.hash = `/reader/${target}`
      }
    })
    return true
  } catch (err) {
    toast.error('添加补充材料失败', errorMessage(err))
    return false
  }
}
