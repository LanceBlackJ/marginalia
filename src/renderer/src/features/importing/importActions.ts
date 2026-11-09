import { create } from 'zustand'
import { api, errorMessage } from '@/lib/api'
import { pickFolder, pickPdfFiles } from '@/lib/native'
import { toast } from '@/components/ui'
import { textApi } from '@/lib/api.text'
import { attachAsSupplement, canAttachAsSupplement } from '@/features/materials/attach'
import { useJobsStore } from '@/stores/jobsStore'
import type { ImportResult, Job } from '@/lib/types'
import { useManualDownload } from './manualDownload'
import { attachDownloadedPdf } from './fulltext'

/** 全局导入对话框状态（顶栏和文献库都可以触发）。 */
interface ImportUiState {
  doiOpen: boolean
  urlOpen: boolean
  openDoi: () => void
  openUrl: () => void
  close: () => void
}

export const useImportUi = create<ImportUiState>((set) => ({
  doiOpen: false,
  urlOpen: false,
  openDoi: () => set({ doiOpen: true, urlOpen: false }),
  openUrl: () => set({ urlOpen: true, doiOpen: false }),
  close: () => set({ doiOpen: false, urlOpen: false })
}))

function startJob(jobId: string, hint: string): void {
  useJobsStore.getState().track(jobId)
  toast.info('已开始导入', hint)
}

export async function importLocalFiles(): Promise<void> {
  try {
    const paths = await pickPdfFiles()
    if (paths.length === 0) return
    const { job_id } = await api.imports.files(paths)
    startJob(job_id, `共 ${paths.length} 个 PDF，可在任务中心查看进度。`)
  } catch (err) {
    toast.error('导入失败', errorMessage(err))
  }
}

/** 论文下载浏览器里截下来的 PDF：直接导入（来源标题里写明是从哪个网址下载的）。 */
export async function importDownloadedPdf(path: string, filename: string): Promise<void> {
  if (await attachDownloadedPdf(path, filename)) return // 是从某篇只有题录的论文里点进去下载的：接回那一篇
  try {
    const { job_id } = await api.imports.files([path])
    startJob(job_id, `已从内置浏览器下载《${filename}》，正在导入，可在任务中心查看进度。`)
  } catch (err) {
    toast.error('导入下载的论文失败', errorMessage(err))
  }
}

export async function importFolder(): Promise<void> {
  try {
    const path = await pickFolder()
    if (!path) return
    const { job_id } = await api.imports.folder(path, true)
    startJob(job_id, '正在扫描文件夹中的 PDF。')
  } catch (err) {
    toast.error('导入失败', errorMessage(err))
  }
}

export function parseDois(text: string): string[] {
  const out = text
    .split(/[\s,;，；]+/)
    .map((s) => s.trim())
    .filter(Boolean)
  return out.filter((d, i) => out.indexOf(d) === i)
}

/** metadataOnly：只保存题录，不下载开放获取全文（之后可在论文页面“附加 PDF”）。 */
export async function importDois(dois: string[], metadataOnly = false): Promise<void> {
  const { job_id } = await api.imports.doi(dois, metadataOnly)
  startJob(job_id, metadataOnly ? `共 ${dois.length} 个 DOI，仅保存元数据。` : `共 ${dois.length} 个 DOI。`)
}

export async function importUrl(url: string, metadataOnly = false): Promise<void> {
  const { job_id } = await api.imports.url(url.trim(), metadataOnly)
  startJob(job_id, metadataOnly ? '正在解析链接，仅保存元数据。' : '正在解析链接。')
}

/** 导入任务结束后的汇总提示（在 AppShell 中注册）。 */
export function summarizeImportJob(job: Job): void {
  if (!['import', 'import_doi', 'import_url', 'attach_pdf', 'fetch_fulltext'].includes(job.type)) return
  if (job.status === 'cancelled') {
    toast.warning(job.type === 'fetch_fulltext' ? '已取消获取全文' : '导入已取消')
    return
  }
  if (job.status === 'failed') {
    toast.error(job.type === 'fetch_fulltext' ? '没能自动获取全文' : '导入失败', job.error ?? '未知错误')
    return
  }
  const items = (job.result as ImportResult | null)?.items ?? []
  if ((job.type === 'fetch_fulltext' || job.type === 'attach_pdf') && items.length === 1 && items[0].status === 'imported') {
    toast.success('已补上全文', '已附加到这篇论文，可以开始阅读了。公式与表格正在后台处理。')
    return
  }
  const count = (s: string): number => items.filter((i) => i.status === s).length
  const imported = count('imported')
  const dup = count('duplicate')
  const meta = count('metadata_only')
  const failed = items.filter((i) => i.status === 'failed')
  const parts: string[] = []
  if (imported) parts.push(`已导入 ${imported} 篇`)
  if (dup) parts.push(`${dup} 篇已在文献库中`)
  if (meta) parts.push(`${meta} 篇无开放全文，仅保存元数据`)
  if (failed.length) parts.push(`${failed.length} 篇失败`)
  const detail = [
    ...items.filter((i) => i.status === 'duplicate' || i.status === 'metadata_only').map((i) => `${i.input}：${i.message ?? ''}`),
    ...failed.map((i) => `${i.input}：${i.message ?? '失败'}`)
  ]
    .slice(0, 4)
    .join('\n')
  const title = parts.length ? parts.join('，') : '导入完成'
  // 只导入了一个文件、且它与已有论文同 DOI（多半是补充材料）：直接给出“作为补充材料添加”
  const attachable = items.length === 1 && canAttachAsSupplement(items[0]) ? items[0] : null
  const attachAction = attachable ? { label: '作为补充材料添加', onClick: () => void attachAsSupplement(attachable) } : undefined
  if (failed.length && !imported && !meta) toast.error(title, detail)
  else if (failed.length || dup || meta) toast.warning(title, detail, attachAction)
  else toast.success(title, imported ? '公式与表格正在后台处理，文库里会显示处理进度；处理完成前不会显示“已完成”。' : undefined)
  // 试过自动下载但没有下到全文，不是你选了“仅保存元数据”：弹出窗口，去下载再把 PDF 选回来
  const manual = items.filter((i) => i.status === 'metadata_only' && i.needs_manual_download && i.paper_id)
  if (manual.length) {
    useManualDownload.getState().open(manual.map((i) => ({ paperId: i.paper_id as string, input: i.input, reason: i.message ?? '' })))
  }
  void watchProcessing(items.filter((i) => i.status === 'imported' && i.paper_id).map((i) => i.paper_id as string))
  // 扫描版 PDF：已经导入、可以先看原文；文字在后台由本地 OCR 识别（不上传云端）
  const scanned = items.filter((i) => i.ocr_job_id)
  if (scanned.length) {
    const pages = scanned.reduce((n, i) => n + (i.ocr_pages ?? 0), 0)
    toast.info(
      `${scanned.length === 1 ? '这是扫描版' : `${scanned.length} 篇是扫描版`}，正在识别文字`,
      `共 ${pages} 页，在本机用 OCR 识别，约每页 10 秒，不会上传到云端。识别完成前可以先看原文，进度在任务页。识别出的文字可能有错误，请以原文为准。`
    )
  }
}

/** 导入后每 4 秒看一次处理进度（最多 15 分钟）：全部处理好了提示“处理完成”，有失败或没配置的提示“需要注意”。 */
async function watchProcessing(ids: string[]): Promise<void> {
  if (ids.length === 0) return
  const deadline = Date.now() + 15 * 60 * 1000
  const pending = new Set(ids)
  const attention: string[] = []
  while (pending.size > 0 && Date.now() < deadline) {
    await new Promise((r) => window.setTimeout(r, 4000))
    for (const id of [...pending]) {
      try {
        const p = await textApi.processing(id)
        if (p.overall === 'done') pending.delete(id)
        else if (p.overall === 'attention') {
          pending.delete(id)
          attention.push(...p.reasons)
        }
      } catch {
        pending.delete(id) // 论文被删除或后端不可用：不再等
      }
    }
  }
  if (attention.length) toast.warning('处理完成，但需要注意', [...new Set(attention)].slice(0, 3).join(' · '))
  else if (pending.size === 0) toast.success('处理完成', '公式和表格都已处理好。')
}
