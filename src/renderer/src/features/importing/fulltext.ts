import { api, errorMessage } from '@/lib/api'
import { openPaperBrowser, pickPdfFiles } from '@/lib/native'
import { toast } from '@/components/ui'
import { useJobsStore } from '@/stores/jobsStore'
import { bestDownloadLink, type LinkSource } from './manualDownload'

/**
 * 只有题录、没有全文的论文：补全文的三条路。
 * 1. 自动获取（arXiv / 开放获取链接）；2. 选一个已经下载好的本地 PDF；3. 在内置浏览器里下载（可登录），下到的 PDF 接回这一篇。
 */

const PENDING_TTL_MS = 30 * 60 * 1000
let pending: { paperId: string; at: number } | null = null

/** 内置浏览器下一个下载下来的 PDF 要接到哪一篇（30 分钟内有效，用一次就清掉）。 */
export function takePendingAttach(now = Date.now()): string | null {
  const p = pending
  pending = null
  return p && now - p.at <= PENDING_TTL_MS ? p.paperId : null
}

export function setPendingAttach(paperId: string, now = Date.now()): void {
  pending = { paperId, at: now }
}

export async function autoFetchFullText(paperId: string): Promise<boolean> {
  try {
    const { job_id } = await api.papers.fetchFulltext(paperId)
    useJobsStore.getState().track(job_id)
    toast.info('正在查找全文', '找到开放获取的版本会自动下载并附加到这篇论文。')
    return true
  } catch (err) {
    toast.error('没能开始获取全文', errorMessage(err))
    return false
  }
}

export async function attachLocalPdf(paperId: string): Promise<boolean> {
  try {
    const paths = await pickPdfFiles()
    if (paths.length === 0) return false
    const { job_id } = await api.papers.attachPdf(paperId, paths[0])
    useJobsStore.getState().track(job_id)
    toast.info('正在附加 PDF', '解析完成后即可阅读。')
    return true
  } catch (err) {
    toast.error('附加 PDF 失败', errorMessage(err))
    return false
  }
}

/** 打开出版商 / DOI 页面。下载到的第一份 PDF 会附加到这一篇，而不是新建一篇。 */
export async function downloadInBrowser(paper: LinkSource & { id: string; arxiv_id?: string | null }): Promise<void> {
  const link = bestDownloadLink(paper) ?? (paper.arxiv_id ? `https://arxiv.org/abs/${paper.arxiv_id}` : null)
  if (!link) {
    toast.warning('没有可用的下载页面', '这篇论文没有 DOI 或链接，可以选择本地 PDF 附加。')
    return
  }
  setPendingAttach(paper.id)
  await openPaperBrowser(link)
  toast.info('在浏览器窗口里下载', '下载到 PDF 后会自动附加到这篇论文。')
}

/** 下载回来的 PDF：有等待附加的论文就接上去，否则当作新论文导入。返回 true 表示已处理。 */
export async function attachDownloadedPdf(path: string, filename: string): Promise<boolean> {
  const paperId = takePendingAttach()
  if (!paperId) return false
  try {
    const d = await api.papers.get(paperId)
    if (d.has_pdf) return false
    const { job_id } = await api.papers.attachPdf(paperId, path)
    useJobsStore.getState().track(job_id)
    toast.info('已下载', `《${filename}》正在附加到《${d.title}》。`)
    return true
  } catch (err) {
    toast.error('附加下载的 PDF 失败', errorMessage(err))
    return true
  }
}
