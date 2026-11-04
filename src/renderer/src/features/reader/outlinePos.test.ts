import { describe, expect, it } from 'vitest'
import { activeOutlineIndex, ordAtPdfPoint, sectionStarts, type PosItem } from './outlinePos'

const mk = (ord: number, page: number, y1: number, section: string | null): PosItem => ({ ord, page, section_title: section, bbox: [0, y1 - 20, 100, y1] })
// 第 3 页上依次开始了 3 / 3.1 / 3.2 三个小节
const items: PosItem[] = [
  mk(10, 2, 700, '2 Related Work'),
  mk(20, 3, 120, '3 Method'), mk(21, 3, 300, '3 Method'),
  mk(22, 3, 340, '3.1 Encoder'), mk(23, 3, 520, '3.1 Encoder'),
  mk(24, 3, 560, '3.2 Decoder'), mk(25, 3, 760, '3.2 Decoder'),
  mk(30, 4, 200, '4 Results')
]
const outline = [
  { id: 'a', title: '2 Related Work', level: 1, page: 2 }, { id: 'b', title: '3 Method', level: 1, page: 3 },
  { id: 'c', title: '3.1 Encoder', level: 2, page: 3 }, { id: 'd', title: '3.2 Decoder', level: 2, page: 3 }, { id: 'e', title: '4 Results', level: 1, page: 4 }
]

describe('左边目录的当前章节', () => {
  it('同一页上有好几个小节：看阅读线所在的段落，不是高亮这一页最后一个', () => {
    expect(activeOutlineIndex(outline, items, 21, 3)).toBe(1) // 还在“3 Method”里
    expect(activeOutlineIndex(outline, items, 23, 3)).toBe(2) // 读到 3.1
    expect(activeOutlineIndex(outline, items, 25, 3)).toBe(3)
    expect(activeOutlineIndex(outline, items, 30, 4)).toBe(4)
  })
  it('没有阅读位置或标题对不上时退回按页码', () => {
    expect(activeOutlineIndex(outline, items, null, 3)).toBe(3)
    expect(activeOutlineIndex(outline, [mk(1, 3, 100, '别的标题')], 1, 3)).toBe(3)
    expect(activeOutlineIndex(outline, [], 5, 4)).toBe(4)
  })
  it('第一个章节之前的位置（首部信息）：退回按页码', () => {
    expect(activeOutlineIndex(outline, items, 5, 1)).toBe(-1)
  })
  it('PDF 里阅读线所在的段落', () => {
    expect(ordAtPdfPoint(items, 3, 100)).toBe(20)
    expect(ordAtPdfPoint(items, 3, 330)).toBe(22)
    expect(ordAtPdfPoint(items, 3, 999)).toBe(25) // 都在阅读线上方：取这一页最后一个
    expect(ordAtPdfPoint(items, 9, 100)).toBeNull()
  })
  it('目录项的起点是它标题下的第一个段落', () => {
    expect(sectionStarts(outline, items)).toEqual([10, 20, 22, 24, 30])
  })
})
