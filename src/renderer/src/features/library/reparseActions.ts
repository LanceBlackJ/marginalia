import { toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import type { Job, ReparseStats } from '@/lib/types'
import { describeReparse, isSuspiciousResult } from './reparseModel'

const TERMINAL = new Set(['completed', 'failed', 'cancelled'])
const sleep = (ms: number): Promise<void> => new Promise((r) => window.setTimeout(r, ms))

async function waitJob(jobId: string): Promise<Job> {
  for (;;) {
    const job = await api.jobs.get(jobId)
    if (TERMINAL.has(job.status)) return job
    await sleep(1200)
  }
}

interface JobResult {
  items?: Array<{ paper_id: string; stats?: ReparseStats }>
}

export interface ReparseOptions {
  /** 每篇完成（成功）后调用：刷新列表 / 重新载入阅读器 */
  onDone?: (paperId: string) => void
  force?: boolean
}

/** 撤销最近一次重新解析（旧的解析数据与缓存原样恢复，笔记等用户数据恢复成改写前的值）。 */
export async function undoReparse(paperId: string, onDone?: (paperId: string) => void): Promise<void> {
  try {
    await api.papers.undoReparse(paperId)
    toast.success('已撤销重新解析', '这篇论文回到了重新解析之前的样子。')
    onDone?.(paperId)
  } catch (err) {
    toast.error('撤销失败', errorMessage(err))
  }
}

/**
 * 重新解析一篇或多篇论文（逐篇进行：后台任务，可在任务页取消）。
 * 失败或取消时旧数据完全不变；成功后单篇提供“撤销”，并如实说明笔记重新对应的情况。
 */
export async function reparsePapers(ids: string[], opts: ReparseOptions = {}): Promise<void> {
  if (ids.length === 0) return
  toast.info(ids.length === 1 ? '开始重新解析' : `开始重新解析 ${ids.length} 篇`, '在后台进行，可以继续阅读；进度在任务页。你的笔记、批注和卡片会保留。')
  let ok = 0
  let last: { id: string; stats?: ReparseStats } | null = null
  for (const id of ids) {
    try {
      const { job_id } = await api.papers.reparse(id, opts.force ?? false)
      const job = await waitJob(job_id)
      if (job.status === 'completed') {
        ok += 1
        const item = (job.result as JobResult | null)?.items?.find((i) => i.paper_id === id) ?? (job.result as JobResult | null)?.items?.[0]
        last = { id, stats: item?.stats }
        opts.onDone?.(id)
      } else if (job.status === 'cancelled') {
        toast.warning('重新解析已取消', '这篇论文保持原样。')
      } else if (isSuspiciousResult(job.error)) {
        toast.warning('新的解析结果差别太大，已中止', `${job.error ?? ''}`, {
          label: '仍然替换',
          onClick: () => void reparsePapers([id], { ...opts, force: true })
        })
      } else {
        toast.error('重新解析失败', `${job.error ?? '未知原因'}，原有数据保持不变`)
      }
    } catch (err) {
      toast.error('重新解析失败', `${errorMessage(err)}，原有数据保持不变`)
    }
  }
  if (ok === 0) return
  if (ids.length === 1 && last) {
    const id = last.id
    toast.success('已重新解析，可撤销', describeReparse(last.stats), { label: '撤销', onClick: () => void undoReparse(id, opts.onDone) })
  } else {
    toast.success(`已重新解析 ${ok} 篇`, '每篇都可以在行菜单或阅读器的“更多”里撤销，保留 7 天。')
  }
}
