import { describe, expect, it } from 'vitest'
import { firstSentence, leadSentence, plainText } from './overviewModel'

describe('overviewModel', () => {
  it('去掉依据标记和 Markdown 符号', () => {
    expect(plainText('**FINDER** 在 `大规模` 网络上更快 [P12][P15]。')).toBe('FINDER 在 大规模 网络上更快。')
    expect(plainText('结论【P3、P7】成立 [S1P2]')).toBe('结论成立')
    expect(plainText('保留普通方括号 [1] 与 [abc]')).toBe('保留普通方括号 [1] 与 [abc]')
  })

  it('取第一句：中文句号、英文句点、没有句号时取整段', () => {
    expect(firstSentence('该方法更快 [P1]。第二句不要。')).toBe('该方法更快。')
    expect(firstSentence('FINDER is faster. It also scales.')).toBe('FINDER is faster.')
    expect(firstSentence('没有句号的一段话')).toBe('没有句号的一段话')
    expect(firstSentence('')).toBe('')
  })

  it('过长截断并加省略号，不超过上限', () => {
    const s = firstSentence('很'.repeat(200), 20)
    expect(s.length).toBeLessThanOrEqual(20)
    expect(s.endsWith('…')).toBe(true)
  })

  it('一句话结论按 结论→结果→方法 的顺序取', () => {
    expect(leadSentence({ conclusion: { text: '结论一句。' }, results: { text: '结果。' } })).toBe('结论一句。')
    expect(leadSentence({ conclusion: { text: '' }, results: { text: '结果一句。更多。' } })).toBe('结果一句。')
    expect(leadSentence({ method: { text: '方法设计很好。' } })).toBe('方法设计很好。')
    expect(leadSentence({})).toBe('')
  })
})
