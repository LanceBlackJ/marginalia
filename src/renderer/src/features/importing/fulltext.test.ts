import { describe, expect, it } from 'vitest'
import { setPendingAttach, takePendingAttach } from './fulltext'

describe('等待附加的论文', () => {
  it('只用一次', () => {
    setPendingAttach('p1', 1000)
    expect(takePendingAttach(2000)).toBe('p1')
    expect(takePendingAttach(2000)).toBeNull()
  })
  it('超过 30 分钟就作废，下载来的 PDF 当作新论文', () => {
    setPendingAttach('p1', 0)
    expect(takePendingAttach(31 * 60 * 1000)).toBeNull()
  })
  it('后一次覆盖前一次', () => {
    setPendingAttach('p1', 0)
    setPendingAttach('p2', 10)
    expect(takePendingAttach(20)).toBe('p2')
  })
})
