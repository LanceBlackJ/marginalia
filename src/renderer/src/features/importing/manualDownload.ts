import { create } from 'zustand'
import type { AccessLink } from '@/lib/types'

/** 试过自动下载但没有下到全文的论文：弹窗让你去下载，再把下载好的 PDF 接回来。 */
export interface ManualDownloadItem {
  paperId: string
  /** 你输入的 DOI 或链接 */
  input: string
  /** 为什么没下到，后端给的原因 */
  reason: string
}

interface ManualDownloadState {
  items: ManualDownloadItem[]
  open: (items: ManualDownloadItem[]) => void
  close: () => void
}

export const useManualDownload = create<ManualDownloadState>((set) => ({
  items: [],
  open: (items) => set({ items }),
  close: () => set({ items: [] })
}))

export interface LinkSource {
  doi: string | null
  source_url?: string | null
  access_info?: AccessLink[] | null
}

const isWeb = (u: string): boolean => /^https?:\/\//i.test(u)

/** 去哪里下载：先用后端记录的可用来源，也就是出版商页面和开放获取页；没有就用 DOI 链接，再没有用你给的链接。 */
export function bestDownloadLink(p: LinkSource): string | null {
  const first = (p.access_info ?? []).find((l) => isWeb(l.url))
  if (first) return first.url
  if (p.doi) return `https://doi.org/${p.doi}`
  return p.source_url && isWeb(p.source_url) ? p.source_url : null
}

/** 备选链接：去掉最佳链接和重复项。 */
export function otherDownloadLinks(p: LinkSource): AccessLink[] {
  const best = bestDownloadLink(p)
  const seen = new Set<string>(best ? [best] : [])
  const out: AccessLink[] = []
  const add = (l: AccessLink): void => {
    if (isWeb(l.url) && !seen.has(l.url)) {
      seen.add(l.url)
      out.push(l)
    }
  }
  for (const l of p.access_info ?? []) add(l)
  if (p.doi) add({ label: 'DOI 链接', url: `https://doi.org/${p.doi}` } as AccessLink)
  if (p.source_url) add({ label: '你输入的链接', url: p.source_url } as AccessLink)
  return out
}
