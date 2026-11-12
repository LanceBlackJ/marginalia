import { describe, expect, it } from 'vitest'
import type { TextItem } from '@/lib/api.text'
import type { OutlineItem } from '@/lib/types'
import { activeOutlineIndex } from '@/features/reader/outlinePos'
import { neighbourSection } from '@/features/reader/useReaderShortcuts'
import { buildDoc } from './textModel'

// 两个同名小节（CoT 论文的 4 / 5 节下各有一个 “Results”）：目录、正文标题、跳转、高亮都要认章节 id，不能只认标题
const outline: OutlineItem[] = [
  { id: '1', title: '4 Commonsense', level: 1, page: 7 },
  { id: '2', title: 'Results', level: 2, page: 7 },
  { id: '3', title: '5 Symbolic', level: 1, page: 8 },
  { id: '4', title: 'Results', level: 2, page: 8 },
]

function p(ord: number, page: number, title: string, id: string): TextItem {
  return { ord, page, kind: 'paragraph', section_title: title, section_id: id, text: `para ${ord}`, bbox: [0, ord * 10, 100, ord * 10 + 8] }
}

const items = [p(1, 7, '4 Commonsense', '1'), p(2, 7, 'Results', '2'), p(3, 8, '5 Symbolic', '3'), p(4, 8, 'Results', '4')]

describe('同名小节', () => {
  it('正文里两个 Results 标题各带自己的章节键，不合并', () => {
    const heads = buildDoc(items, outline, { showNoise: false }).blocks.filter((b) => b.type === 'heading')
    expect(heads.map((h) => (h.type === 'heading' ? h.secKey : ''))).toEqual(['#1', '#2', '#3', '#4'])
  })

  it('目录高亮按章节键落在第二个 Results', () => {
    expect(activeOutlineIndex(outline, [], null, 8, '#4')).toBe(3)
    expect(activeOutlineIndex(outline, [], null, 7, '#2')).toBe(1)
    // 旧数据只有标题时仍按标题（第一个）
    expect(activeOutlineIndex(outline, [], null, 8, 'Results')).toBe(1)
  })

  it('[ ] 切章节：从第二个 Results 往前是 “5 Symbolic”，不是第一个 Results 的邻居', () => {
    expect(neighbourSection(outline, 8, '#4', -1)?.id).toBe('3')
    expect(neighbourSection(outline, 8, '#2', 1)?.id).toBe('3')
  })

  it('第二个 Results 下面没有正文时，也不会被第一个同名小节“顶替”而漏掉标题', () => {
    const only = [p(1, 7, '4 Commonsense', '1'), p(2, 7, 'Results', '2'), p(3, 8, '5 Symbolic', '3')]
    const heads = buildDoc(only, outline, { showNoise: false }).blocks.filter((b) => b.type === 'heading')
    // 第 4 项（空的第二个 Results）在最后，没有后续章节可触发补标题：至少不能重复出现第一个 Results
    expect(heads.filter((h) => h.type === 'heading' && h.secKey === '#2')).toHaveLength(1)
  })
})
