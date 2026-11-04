import { describe, expect, it } from 'vitest'
import type { CompareEvidence } from '@/lib/types.discovery'
import { buildEvidenceIndex, citesToLinks, evidenceKey } from './evidenceIndex'

const ev = (label: string, ord: number | null): CompareEvidence => ({
  label, paper_id: 'p', paper_title: 't', ord, page: ord ? 3 : null, section_title: null, quote: 'q', bbox: null, kind: ord === null ? 'abstract' : 'paper_text'
})

describe('evidence index', () => {
  it('编号格式与后端一致：论文字母:P段落号 / ABS', () => {
    expect(evidenceKey('A', 12)).toBe('A:P12')
    expect(evidenceKey('B', null)).toBe('B:ABS')
  })
  it('按编号索引证据，未索引的编号查不到（界面据此标为无法核实）', () => {
    const idx = buildEvidenceIndex([ev('A', 12), ev('B', null)])
    expect(idx.get('A:P12')?.page).toBe(3)
    expect(idx.get('B:ABS')?.kind).toBe('abstract')
    expect(idx.get('A:P99')).toBeUndefined()
  })
  it('把正文里的 [A:P12] 转成可替换为 chip 的链接，其余方括号不动', () => {
    expect(citesToLinks('结论成立[A:P12][B:ABS]。参见[1]与[abc]')).toBe('结论成立[A·P12](cite:A:P12)[B·ABS](cite:B:ABS)。参见[1]与[abc]')
  })
})
