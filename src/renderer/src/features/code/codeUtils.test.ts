import { describe, expect, it } from 'vitest'
import type { CodeFileEntry, FlowEdge, FlowNode, PaperCodeLink } from '@/lib/types.code'
import {
  ancestorDirs,
  buildFileTree,
  citationsToLinks,
  deriveTarget,
  dimRawCitations,
  filterFiles,
  fmtBytes,
  groupLinksByModule,
  layoutFlow,
  lineRange,
  parseLoc,
  roleBadge,
  splitCitations,
  targetLabel
} from './codeUtils'
import { highlightLines, languageForPath, splitHtmlLines } from './highlight'

const f = (path: string, role: CodeFileEntry['role'] = 'util'): CodeFileEntry => ({ path, language: null, size: 10, lines: 3, role, skipped: null })

describe('buildFileTree', () => {
  it('puts directories first, sorts naturally and compacts single-child directory chains', () => {
    const tree = buildFileTree([f('README.md'), f('code/FINDER_CN/train.py'), f('code/FINDER_CN/utils/a.py'), f('code/FINDER_CN/utils/b.py'), f('data/x2.csv'), f('data/x10.csv'), f('data/x1.csv')])
    expect(tree.map((n) => n.name)).toEqual(['code/FINDER_CN', 'data', 'README.md'])
    const code = tree[0]
    expect(code.path).toBe('code/FINDER_CN')
    expect(code.children.map((c) => c.name)).toEqual(['utils', 'train.py'])
    expect(tree[1].children.map((c) => c.name)).toEqual(['x1.csv', 'x2.csv', 'x10.csv'])
  })

  it('computes ancestor directories for auto-expansion', () => {
    expect(ancestorDirs('a/b/c.py')).toEqual(['a', 'a/b'])
    expect(ancestorDirs('c.py')).toEqual([])
  })

  it('filters files by every search term', () => {
    const files = [f('code/FINDER_CN/train.py'), f('code/FINDER_ND/train.py'), f('code/FINDER_CN/utils.py')]
    expect(filterFiles(files, 'cn train').map((x) => x.path)).toEqual(['code/FINDER_CN/train.py'])
    expect(filterFiles(files, '  ')).toEqual([])
  })
})

describe('labels and formatting', () => {
  it('only badges meaningful roles', () => {
    expect(roleBadge('train')).toBe('训练')
    expect(roleBadge('util')).toBeNull()
    expect(roleBadge('other')).toBeNull()
  })
  it('formats sizes, line ranges and locations', () => {
    expect(fmtBytes(512)).toBe('512 B')
    expect(fmtBytes(2048)).toBe('2.0 KB')
    expect(fmtBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(fmtBytes(null)).toBe('—')
    expect(lineRange(3, 3)).toBe('L3')
    expect(lineRange(3, 9)).toBe('L3–9')
    expect(parseLoc('code/a.pyx:43')).toEqual({ path: 'code/a.pyx', a: 43, b: 43 })
    expect(parseLoc('code/a.pyx:10-20')).toEqual({ path: 'code/a.pyx', a: 10, b: 20 })
    expect(parseLoc('nope')).toBeNull()
  })
})

describe('explain targets', () => {
  const sym = { id: 7, path: 'a/b.py', kind: 'method' as const, qualname: 'Agent.learn' }
  it('prefers selected lines, then the selected symbol, then the file', () => {
    expect(deriveTarget(null, null, null)).toBeNull()
    expect(deriveTarget('a/b.py', { path: 'a/b.py', a: 3, b: 9 }, sym)).toEqual({ kind: 'line_range', path: 'a/b.py', line_start: 3, line_end: 9 })
    expect(deriveTarget('a/b.py', { path: 'other.py', a: 3, b: 9 }, sym)).toEqual({ kind: 'function', path: 'a/b.py', symbol_id: 7, symbol: 'Agent.learn' })
    expect(deriveTarget('a/b.py', null, { ...sym, kind: 'class' })?.kind).toBe('class')
    expect(deriveTarget('a/b.py', null, { ...sym, path: 'x.py' })).toEqual({ kind: 'file', path: 'a/b.py' })
  })
  it('labels targets for display', () => {
    expect(targetLabel({ kind: 'line_range', path: 'a/b.py', line_start: 3, line_end: 9 })).toBe('b.py L3–9')
    expect(targetLabel({ kind: 'variable', variable: 'lr', symbol: 'train' })).toBe('变量 lr · 在 train 中')
    expect(targetLabel({ kind: 'file', path: 'a/b.py' })).toBe('文件 b.py')
  })
})

describe('citations', () => {
  it('splits [L#] markers out of text', () => {
    const segs = splitCitations('先初始化 [L1]，再训练 [L2,L3]。')
    expect(segs.map((s) => (s.type === 'cite' ? s.refs : s.text))).toEqual(['先初始化 ', [1], '，再训练 ', [2, 3], '。'])
    expect(splitCitations('没有引用')).toEqual([{ type: 'text', text: '没有引用' }])
  })
  it('turns markers into markdown links and dims raw model markers while streaming', () => {
    expect(citationsToLinks('见 [L2] 和 [L1,L3]')).toBe('见 [L2](lref:2) 和 [L1,L3](lref:1,3)')
    expect(dimRawCitations('见 [C1:3-5] 与 [a/b.py:1-2]，不是 [链接](x)')).toBe('见 `[C1:3-5]` 与 `[a/b.py:1-2]`，不是 [链接](x)')
  })
})

describe('syntax highlighting', () => {
  it('keeps every line balanced when a token spans lines', () => {
    const lines = splitHtmlLines('<span class="a">x\ny <span class="b">z</span>\nw</span>')
    expect(lines).toEqual(['<span class="a">x</span>', '<span class="a">y <span class="b">z</span></span>', '<span class="a">w</span>'])
  })
  it('highlights python line by line and escapes plain text', () => {
    const lines = highlightLines('def f(a):\n    """doc\n    more"""\n    return a < 1\n', 'python')
    expect(lines).toHaveLength(4)
    expect(lines[0]).toContain('hljs-keyword')
    for (const l of lines) expect((l.match(/<span/g) ?? []).length).toBe((l.match(/<\/span>/g) ?? []).length)
    expect(highlightLines('a < b && c\n', null)).toEqual(['a &lt; b &amp;&amp; c'])
  })
  it('maps extensions (including Cython) to languages', () => {
    expect(languageForPath('code/FINDER.pyx')).toBe('python')
    expect(languageForPath('src/lib/graph.h')).toBe('cpp')
    expect(languageForPath('Dockerfile')).toBe('dockerfile')
    expect(languageForPath('weights.bin')).toBeNull()
  })
})

describe('layoutFlow', () => {
  const node = (id: number, order: number): FlowNode => ({
    id, label: `n${id}`, name: `n${id}`, path: 'a.py', kind: 'function', line_start: 1, line_end: 2, depth: 0, order, signature: '', doc: '', phase: '', uncertain: false
  })
  const edge = (source: number, target: number, order: number): FlowEdge => ({ source, target, line: 1, certainty: 'resolved', order })

  it('lays callers left of callees (callees in one column, in call order), ignores duplicate and self edges', () => {
    const lay = layoutFlow([node(1, 1), node(2, 2), node(3, 3)], [edge(1, 2, 1), edge(1, 3, 2), edge(1, 2, 3), edge(2, 2, 4)])
    const p = lay.positions
    expect(p.size).toBe(3)
    expect(p.get(1)!.x).toBeLessThan(p.get(2)!.x)
    expect(p.get(2)!.x).toBe(p.get(3)!.x)
    expect(p.get(2)!.y).toBeLessThan(p.get(3)!.y)
    expect(lay.width).toBeGreaterThan(0)
  })
  it('handles an empty graph', () => {
    expect(layoutFlow([], []).positions.size).toBe(0)
  })
})

describe('groupLinksByModule', () => {
  const link = (id: string, name: string, confidence: PaperCodeLink['confidence'], status: PaperCodeLink['status']): PaperCodeLink => ({
    id, paper_id: 'p', repo_id: 'r', module: { name, description: '', passage_ords: [], pages: [3], section: null }, symbol_id: 1, path: 'a.py', line_start: 1, line_end: 2,
    confidence, method: 'llm', basis: { text: '', matched_terms: [] }, status, created_at: '', symbol: null
  })
  it('groups by module, ranks confirmed/likely first and hides rejected by default', () => {
    const links = [link('1', 'Encoder', 'possible', 'suggested'), link('2', 'Encoder', 'likely', 'suggested'), link('3', 'Encoder', 'possible', 'confirmed'), link('4', 'Decoder', 'likely', 'rejected')]
    const groups = groupLinksByModule(links)
    expect(groups.map((g) => g.name)).toEqual(['Encoder'])
    expect(groups[0].links.map((l) => l.id)).toEqual(['3', '2', '1'])
    expect(groupLinksByModule(links, true).map((g) => g.name)).toEqual(['Encoder', 'Decoder'])
  })
})
