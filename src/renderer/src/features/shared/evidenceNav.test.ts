import { describe, expect, it, vi } from 'vitest'
import { takePendingJump } from '@/features/materials/crossJump'
import { openEvidenceInReader } from './evidenceNav'

describe('openEvidenceInReader', () => {
  it('登记待办跳转并导航到论文；阅读器加载完成后取走它定位', () => {
    const navigate = vi.fn()
    openEvidenceInReader(navigate, 'paper-1', { page: 7, bbox: [1, 2, 3, 4] })
    expect(navigate).toHaveBeenCalledWith('/reader/paper-1')
    expect(takePendingJump('paper-1')).toEqual({ paperId: 'paper-1', page: 7, bbox: [1, 2, 3, 4] })
    expect(takePendingJump('paper-1')).toBeNull()
  })
  it('没有页码（如摘要证据）时只打开论文，不登记跳转', () => {
    const navigate = vi.fn()
    openEvidenceInReader(navigate, 'paper-2', { page: null })
    expect(navigate).toHaveBeenCalledWith('/reader/paper-2')
    expect(takePendingJump('paper-2')).toBeNull()
  })
  it('bbox 不是四个数时忽略 bbox，仅按页跳转', () => {
    openEvidenceInReader(vi.fn(), 'paper-3', { page: 2, bbox: [1, 2] })
    expect(takePendingJump('paper-3')).toEqual({ paperId: 'paper-3', page: 2, bbox: null })
  })
})
