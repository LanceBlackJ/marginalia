import { describe, expect, it } from 'vitest'
import { unionRects } from './PageView'

describe('unionRects（聚光灯的洞不能重叠，否则 evenodd 会把重叠处又填上）', () => {
  it('互不相干的两块（左右两栏）保持两块', () => {
    expect(unionRects([[50, 600, 280, 700], [320, 60, 550, 150]])).toHaveLength(2)
  })
  it('重叠或紧贴的块并成一块', () => {
    expect(unionRects([[0, 0, 10, 10], [5, 5, 20, 20]])).toEqual([[0, 0, 20, 20]])
    expect(unionRects([[0, 0, 10, 10], [10.5, 0, 20, 10]])).toEqual([[0, 0, 20, 10]])
  })
  it('一串相接的行最终并成一块，且不改动传入的数组', () => {
    const input: Array<[number, number, number, number]> = [[0, 0, 100, 10], [0, 10, 100, 20], [0, 20, 100, 30]]
    expect(unionRects(input)).toEqual([[0, 0, 100, 30]])
    expect(input[0]).toEqual([0, 0, 100, 10])
  })
})
