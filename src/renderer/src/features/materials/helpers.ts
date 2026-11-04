/** 补充材料相关的纯函数（不依赖 React / 浏览器环境，便于单元测试）。 */
import { formatBytes, formatNumber } from '@/lib/format'
import type { ImportResultItem } from '@/lib/types'
import type { SupplementAsset } from '@/lib/types.materials'

const ACTIVE_DOWNLOAD = new Set(['pending', 'downloading', 'processing'])

/** 列表里是否还有在后台处理的材料（需要轮询刷新）。 */
export function hasActiveAssets(items: Array<Pick<SupplementAsset, 'download_status'>>): boolean {
  return items.some((a) => ACTIVE_DOWNLOAD.has(a.download_status))
}

/** 一句话说明这个材料里有什么（已解析完成才有）：大小、页数、行列数等。 */
export function assetFacts(a: SupplementAsset): string[] {
  const out: string[] = []
  if (a.size != null) out.push(formatBytes(a.size))
  if (a.child?.page_count) out.push(`${a.child.page_count} 页`)
  const an = a.analysis
  if (an?.kind === 'table') {
    const sheets = an.sheets && an.sheets > 1 ? `${an.sheets} 个工作表 · ` : ''
    out.push(`${sheets}${formatNumber(an.rows ?? 0)} 行 × ${an.columns ?? 0} 列${an.partial ? ' · 仅扫描了前一部分' : ''}`)
  } else if (an?.kind === 'archive') {
    out.push(`${formatNumber(an.total_files ?? 0)} 个文件`)
  } else if (an?.kind === 'document') {
    out.push(`${formatNumber(an.paragraphs ?? 0)} 段`)
  }
  return out
}

export const LOCAL_PATH = /^(?:[a-zA-Z]:[\\/]|\\\\|\/)/
export const HTTP_URL = /^https?:\/\/\S+$/i

/**
 * 导入结果里“与已有论文同 DOI 的重复文件”可以改为作为该论文的补充材料添加。
 * 只有输入是本地文件路径或文件链接时才能这样做（DOI / 网页链接下载的临时文件已被丢弃）。
 */
export function canAttachAsSupplement(it: Pick<ImportResultItem, 'input' | 'can_attach_as_supplement' | 'target_paper_id'>): boolean {
  return !!it.can_attach_as_supplement && !!it.target_paper_id && (LOCAL_PATH.test(it.input) || HTTP_URL.test(it.input))
}
