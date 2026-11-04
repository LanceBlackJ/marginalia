import { describe, expect, it } from 'vitest'
import type { TextItem } from '@/lib/api.text'
import type { OutlineItem } from '@/lib/types'
import {
  applyBold,
  blockText,
  boldRanges,
  mathRanges,
  applyMath,
  buildDoc,
  buildPlain,
  findPassageByBBox,
  firstOrdOnPage,
  headingLevels,
  highlightTerms,
  markAnnotations,
  pageAtLine,
  segmentsFor,
  stepTextSize
} from './textModel'

const item = (ord: number, page: number, kind: TextItem['kind'], section: string | null, text = `t${ord}`, bbox: [number, number, number, number] = [0, ord * 10, 100, ord * 10 + 8]): TextItem => ({
  ord,
  page,
  kind,
  section_title: section,
  text,
  bbox
})

const outline: OutlineItem[] = [
  { id: '1', title: 'Introduction', level: 1, page: 1 },
  { id: '2', title: 'Methods', level: 1, page: 2 },
  { id: '3', title: 'Encoder', level: 2, page: 2 }
]

describe('buildDoc', () => {
  const items = [
    item(0, 1, 'paragraph', 'Introduction'),
    item(1, 1, 'paragraph', 'Introduction'),
    item(2, 2, 'paragraph', 'Methods'),
    item(3, 2, 'figure_text', 'Methods'),
    item(4, 2, 'paragraph', 'Encoder'),
    item(5, 3, 'reference', 'References'),
    item(6, 3, 'paragraph', 'Encoder', 'Six sentence.')
  ]

  const label = (b: ReturnType<typeof buildDoc>['blocks'][number]): string => (b.type === 'heading' ? `H:${b.title}` : `P:${b.parts.map((x) => x.ord).join('+')}`)

  it('builds one continuous block list and inserts a heading when the section changes', () => {
    const doc = buildDoc(items, outline, { showNoise: false })
    expect(doc.blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'P:1', 'H:Methods', 'P:2', 'H:Encoder', 'P:4', 'P:6'])
  })

  it('marks the first block of each page but not the paragraph right after its heading', () => {
    const doc = buildDoc(items, outline, { showNoise: false })
    const starts = doc.blocks.filter((b) => b.type === 'passage' && b.pageStart).map((b) => (b.type === 'passage' ? b.item.ord : -1))
    expect(starts).toEqual([6]) // 第 1、2 页的页码标在各自的章节标题上，只有第 3 页以段落开头
  })

  it('keeps the parser reading order: a figure moved after the paragraph that mentions it stays there', () => {
    // 第 2 页的图注被挪到第 1 页提到它的段落后面：不能按页码把它排回去，也不冒出回头的页码
    const moved = [item(0, 1, 'paragraph', null, 'One sentence.'), item(1, 2, 'caption', null), item(2, 1, 'paragraph', null, 'Two sentence.'), item(3, 2, 'paragraph', null, 'Three sentence.')]
    const doc = buildDoc(moved, outline, { showNoise: false })
    const blocks = doc.blocks.filter((b) => b.type === 'passage')
    expect(blocks.map((b) => (b.type === 'passage' ? b.item.ord : -1))).toEqual([0, 1, 2, 3])
    expect(blocks.filter((b) => b.type === 'passage' && b.pageStart).map((b) => (b.type === 'passage' ? b.item.ord : -1))).toEqual([0, 3])
  })

  it('uses the outline to give nested headings a lower level', () => {
    const lv = headingLevels(outline)
    expect(lv.get('Introduction')).toBe(1)
    expect(lv.get('Encoder')).toBe(2)
  })

  it('moves references to their own list and hides figure text by default', () => {
    const doc = buildDoc(items, outline, { showNoise: false })
    expect(doc.references.map((r) => r.ord)).toEqual([5])
    expect(doc.hidden).toBe(1)
    const shown = buildDoc(items, outline, { showNoise: true })
    expect(shown.hidden).toBe(0)
    expect(shown.blocks.some((b) => b.type === 'passage' && b.item.ord === 3)).toBe(true)
  })

  it('does not render the parser placeholder section as a heading', () => {
    const doc = buildDoc([item(0, 1, 'paragraph', '正文'), item(1, 1, 'paragraph', '正文')], outline, { showNoise: false })
    expect(doc.blocks.map(label)).toEqual(['P:0', 'P:1'])
  })
})

describe('paragraphs cut by a page boundary', () => {
  const mk = (a: string, b: string, kindB: TextItem['kind'] = 'paragraph', secB: string | null = 'Introduction') => [
    item(0, 1, 'paragraph', 'Introduction', a),
    item(1, 2, kindB, secB, b)
  ]
  const label = (b: ReturnType<typeof buildDoc>['blocks'][number]): string => (b.type === 'heading' ? `H:${b.title}` : `P:${b.parts.map((x) => x.ord).join('+')}`)

  it('joins a paragraph that stops without end punctuation with a lower-case continuation on the next page', () => {
    const doc = buildDoc(mk('The model learns a function of', 'the graph structure and more.'), outline, { showNoise: false })
    expect(doc.blocks.map(label)).toEqual(['H:Introduction', 'P:0+1'])
    const b = doc.blocks[1]
    expect(b.type === 'passage' && blockText(b.parts)).toBe('The model learns a function of the graph structure and more.')
  })

  it('does not join after a sentence end, before an upper-case start, or across headings and captions', () => {
    expect(buildDoc(mk('It ends here.', 'and continues'), outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'P:1'])
    expect(buildDoc(mk('It does not end', 'Next sentence starts'), outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'P:1'])
    expect(buildDoc(mk('It does not end', 'new section text', 'paragraph', 'Methods'), outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'H:Methods', 'P:1'])
    expect(buildDoc(mk('It does not end', 'a caption text', 'caption'), outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'P:1'])
  })

  it('keeps the page-end hyphen decision for the joined text', () => {
    const doc = buildDoc(mk('increases the connectiv-', 'ity of the network.'), outline, { showNoise: false })
    const b = doc.blocks[1]
    expect(b.type === 'passage' && blockText(b.parts)).toBe('increases the connectiv- ity of the network.')
  })

  it('also joins a paragraph cut by a column break on the same page, but not short lines', () => {
    const long = 'Depending on the specific application scenario, we need to define the corresponding measure to quantify the network functionality, such as the connectivity'
    const sameLong = [item(0, 1, 'paragraph', 'Introduction', long), item(1, 1, 'paragraph', 'Introduction', 'measure, because it is relevant.')]
    expect(buildDoc(sameLong, outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0+1'])
    const sameShort = [item(0, 1, 'paragraph', 'Introduction', 'Short line'), item(1, 1, 'paragraph', 'Introduction', 'lower start here.')]
    expect(buildDoc(sameShort, outline, { showNoise: false }).blocks.map(label)).toEqual(['H:Introduction', 'P:0', 'P:1'])
  })

  it('never joins in raw mode', () => {
    const doc = buildDoc(mk('The model learns a function of', 'the graph structure.'), outline, { showNoise: false, raw: true })
    expect(doc.blocks.filter((b) => b.type === 'passage')).toHaveLength(2)
  })
})

describe('locating passages', () => {
  const items = [item(0, 1, 'paragraph', null, 'a', [0, 0, 100, 50]), item(1, 1, 'paragraph', null, 'b', [0, 60, 100, 110]), item(2, 3, 'paragraph', null, 'c')]

  it('finds the passage with the largest overlap', () => {
    expect(findPassageByBBox(items, 1, [10, 70, 90, 100])).toBe(1)
    expect(findPassageByBBox(items, 1, [10, 5, 90, 40])).toBe(0)
  })

  it('falls back to the nearest passage when nothing overlaps, and to the next text page', () => {
    expect(findPassageByBBox(items, 1, [200, 62, 300, 70])).toBe(1)
    expect(findPassageByBBox(items, 2, [0, 0, 1, 1])).toBe(2)
    expect(firstOrdOnPage(items, 2)).toBe(2)
    expect(firstOrdOnPage(items, 9)).toBeNull()
  })
})

describe('highlighting', () => {
  it('marks search terms case-insensitively and ignores single letters', () => {
    expect(highlightTerms('Graph neural network', 'neural a')).toEqual([{ text: 'Graph ' }, { text: 'neural', mark: true }, { text: ' network' }])
    expect(highlightTerms('abc', '')).toEqual([{ text: 'abc' }])
  })

  it('marks user highlights only where the text matches verbatim', () => {
    const marks = [
      { id: 'a1', text: 'key players', color: 'yellow' },
      { id: 'a2', text: 'not in this paragraph', color: 'blue' }
    ]
    const segs = markAnnotations('Finding key players in graphs', marks)
    expect(segs).toEqual([{ text: 'Finding ' }, { text: 'key players', annId: 'a1', color: 'yellow' }, { text: ' in graphs' }])
  })

  it('does not search-highlight inside a user highlight', () => {
    const segs = segmentsFor('key players and players', [{ id: 'a', text: 'key players', color: 'pink' }], 'players')
    expect(segs.filter((s) => s.mark).length).toBe(1)
    expect(segs[0].annId).toBe('a')
  })
})

describe('buildPlain', () => {
  const items = [item(0, 1, 'paragraph', null, 'Hello'), item(1, 2, 'paragraph', null, 'World'), item(2, 2, 'reference', null, 'Ref one'), item(3, 2, 'figure_text', null, 'axis')]
  const tr = new Map([[0, '你好']])

  it('exports per view with page separators and references last', () => {
    expect(buildPlain(items, tr, 'text', { showNoise: false })).toBe('—— 第 1 页 ——\n\nHello\n\n—— 第 2 页 ——\n\nWorld\n\n—— 参考文献 ——\n\nRef one')
    expect(buildPlain(items, tr, 'both', { showNoise: false })).toContain('Hello\n你好')
    expect(buildPlain(items, tr, 'target', { showNoise: false })).toContain('你好')
    expect(buildPlain(items, tr, 'target', { showNoise: false })).toContain('World') // 没有译文的段落保留原文
  })
})

describe('page tracking and text size', () => {
  it('picks the last page whose top has passed the reading line', () => {
    const tops = [
      { page: 1, top: -400 },
      { page: 2, top: -20 },
      { page: 3, top: 500 }
    ]
    expect(pageAtLine(tops, 80)).toBe(2)
    expect(pageAtLine(tops, -1000)).toBe(1)
  })

  it('steps through the allowed sizes and stops at the ends', () => {
    expect(stepTextSize(16, 1)).toBe(17)
    expect(stepTextSize(16, -1)).toBe(15)
    expect(stepTextSize(22, 1)).toBe(22)
    expect(stepTextSize(14, -1)).toBe(14)
  })
})

describe('原文加粗', () => {
  it('按顺序定位加粗片段，找不到的跳过', () => {
    const text = 'We call it Graph Rewiring Network and key finding here'
    expect(boldRanges(text, ['Graph Rewiring Network', '不存在', 'key finding'])).toEqual([
      [11, 33],
      [38, 49]
    ])
    expect(boldRanges(text, undefined)).toEqual([])
  })

  it('在搜索命中和批注高亮的分段上叠加加粗，文字内容不变', () => {
    const segs = [{ text: 'We call it ' }, { text: 'Graph', mark: true }, { text: ' Rewiring Network ok' }]
    const out = applyBold(segs, [[11, 33]])
    expect(out.map((s) => s.text).join('')).toBe('We call it Graph Rewiring Network ok')
    expect(out.filter((s) => s.bold).map((s) => s.text).join('')).toBe('Graph Rewiring Network')
    expect(out.find((s) => s.text === 'Graph')).toMatchObject({ mark: true, bold: true })
    expect(applyBold(segs, [])).toBe(segs)
  })
})

describe('行内公式', () => {
  const runs = [
    { src: 'N', tex: 'N' },
    { src: 'vi 2 V', tex: 'v_{i}\in \mathcal{V}' },
    { src: 'σ', tex: '\sigma' }
  ]

  it('按顺序定位；单个字母不会命中词中间', () => {
    const text = 'Network size N is large, and vi 2 V denotes σ here'
    const r = mathRanges(text, runs)
    expect(r.map(([a, b]) => text.slice(a, b))).toEqual(['N', 'vi 2 V', 'σ'])
    expect(r[0][0]).toBe(13) // 不是 Network 里的 N
  })

  it('找不到的公式跳过，不影响其余', () => {
    expect(mathRanges('only σ here', runs).map(([, , t]) => t)).toEqual(['\sigma'])
    expect(mathRanges('nothing', undefined)).toEqual([])
  })

  it('替换成公式分段，文字总长不变；被高亮切开的公式保持原样', () => {
    const text = 'a vi 2 V b'
    const out = applyMath([{ text }], mathRanges(text, runs))
    expect(out.map((s) => s.text).join('')).toBe(text)
    expect(out.find((s) => s.tex)).toMatchObject({ text: 'vi 2 V', tex: 'v_{i}\in \mathcal{V}' })
    const split = applyMath([{ text: 'a vi' }, { text: ' 2 V b', mark: true }], mathRanges(text, runs))
    expect(split.some((s) => s.tex)).toBe(false)
  })
})
