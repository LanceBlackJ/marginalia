import { describe, expect, it } from 'vitest'
import { parseAnswerSections } from './sections'

describe('parseAnswerSections', () => {
  it('完整结构', () => {
    const raw = [
      '## 直接回答',
      '作者引入该模块是为了降低方差 [P3]。',
      '',
      '## 详细解释',
      '细节……',
      '',
      '## 依据',
      '- [P3] 说明了动机',
      '',
      '## AI 推断',
      '- 可能还为了加速收敛（依据：[P3]）',
      '',
      '## 后续问题',
      '- 消融实验是否验证？'
    ].join('\n')
    const s = parseAnswerSections(raw)
    expect(s.direct).toBe('作者引入该模块是为了降低方差 [P3]。')
    expect(s.detail).toBe('细节……')
    expect(s.evidence).toContain('[P3]')
    expect(s.inference).toContain('可能还为了加速收敛')
    expect(s.followUps).toContain('消融实验')
  })

  it('流式只到一半：只有直接回答', () => {
    const s = parseAnswerSections('## 直接回答\n正在输出')
    expect(s.direct).toBe('正在输出')
    expect(s.detail).toBeNull()
  })

  it('没有任何标题时整段为直接回答', () => {
    expect(parseAnswerSections('只是一段话').direct).toBe('只是一段话')
  })

  it('无关标题保留在当前小节内', () => {
    const s = parseAnswerSections('## 直接回答\n甲\n### 小标题\n乙')
    expect(s.direct).toContain('甲')
    expect(s.direct).toContain('乙')
  })
})
