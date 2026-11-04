/**
 * 跨论文跳转：补充材料是独立的子论文（/reader/<子论文 id>），证据 chip 点击后需要先导航到它的阅读器，
 * 待那篇论文加载完成再定位到页码与区域。readerStore.openPaper 在加载完成时取走这里登记的待办。
 */
import type { NavigateFunction } from 'react-router-dom'
import type { BBox, Evidence } from '@/lib/types'

interface PendingJump {
  paperId: string
  page: number
  bbox?: BBox | null
}

let pending: PendingJump | null = null

export function setPendingJump(j: PendingJump): void {
  pending = j
}

export function takePendingJump(paperId: string): PendingJump | null {
  if (pending && pending.paperId === paperId) {
    const p = pending
    pending = null
    return p
  }
  return null
}

/** 打开补充材料中的证据位置；证据缺少来源信息时返回 false。 */
export function openSupplementEvidence(navigate: NavigateFunction, ev: Pick<Evidence, 'page' | 'bbox' | 'source'>): boolean {
  const paperId = ev.source?.paper_id
  if (!paperId) return false
  setPendingJump({ paperId, page: ev.page, bbox: ev.bbox })
  navigate(`/reader/${paperId}`)
  return true
}
