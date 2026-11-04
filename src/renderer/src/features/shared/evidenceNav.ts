import type { NavigateFunction } from 'react-router-dom'
import { setPendingJump } from '@/features/materials/crossJump'
import type { BBox } from '@/lib/types'

/**
 * 从“发现/比较/知识库”等页面跳到阅读器里的某条原文证据：
 * 登记待办跳转（阅读器加载完成后自行取走并定位/高亮），再导航到论文。
 */
export function openEvidenceInReader(
  navigate: NavigateFunction,
  paperId: string,
  ev: { page?: number | null; bbox?: number[] | null }
): void {
  if (ev.page) setPendingJump({ paperId, page: ev.page, bbox: ev.bbox && ev.bbox.length === 4 ? (ev.bbox as BBox) : null })
  navigate(`/reader/${paperId}`)
}
