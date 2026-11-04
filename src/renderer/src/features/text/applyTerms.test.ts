import { describe, expect, it } from 'vitest'
import { applyTerms, termRegExp } from './textModel'

const text = (segs: Array<{ text: string; term?: string }>): string => segs.map((s) => s.text).join('')

describe('applyTerms（正文里标出术语表的词，只标这一段里的第一次出现）', () => {
  it('只切分、不改文字；每个词只标第一次出现', () => {
    const segs = applyTerms([{ text: 'A residual connection helps. The residual connection is cheap.' }], ['residual connection'])
    expect(text(segs)).toBe('A residual connection helps. The residual connection is cheap.')
    expect(segs.filter((s) => s.term).map((s) => s.text)).toEqual(['residual connection'])
  })
  it('英文词按词边界匹配：不会标到更长的词里面', () => {
    expect(applyTerms([{ text: 'Preresidual and residuals' }], ['residual']).some((s) => s.term)).toBe(false)
  })
  it('缩写区分大小写，普通词忽略大小写', () => {
    expect(applyTerms([{ text: 'the bleu score' }], ['BLEU']).some((s) => s.term)).toBe(false)
    expect(applyTerms([{ text: 'the BLEU score' }], ['BLEU']).some((s) => s.term)).toBe(true)
    expect(applyTerms([{ text: 'Attention is all' }], ['attention']).some((s) => s.term)).toBe(true)
  })
  it('公式、批注、搜索命中的分段不动', () => {
    const segs = applyTerms([{ text: 'residual', tex: 'x' }, { text: 'residual', mark: true }, { text: 'residual', annId: 'a' }], ['residual'])
    expect(segs.some((s) => s.term)).toBe(false)
  })
  it('多个词、含空格和连字符的词', () => {
    const segs = applyTerms([{ text: 'Use self-attention with d_model units.' }], ['self-attention', 'd_model'])
    expect(segs.filter((s) => s.term).map((s) => s.term)).toEqual(['self-attention', 'd_model'])
    expect(text(segs)).toBe('Use self-attention with d_model units.')
    expect(termRegExp('self-attention').test('a self-attention layer')).toBe(true)
  })
  it('没有术语时原样返回', () => {
    const s = [{ text: 'x' }]
    expect(applyTerms(s, [])).toBe(s)
  })
})
