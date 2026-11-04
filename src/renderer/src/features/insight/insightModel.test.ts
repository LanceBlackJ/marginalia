import { describe, expect, it } from 'vitest'
import { cardMetaParts, questionLine, shortModelName, verifyLabel } from './insightModel'

describe('shortModelName', () => {
  it('去掉日期、量化和 Instruct 后缀，保留能辨认的名字', () => {
    expect(shortModelName('DeepSeek-V4-Flash-0731-W8A8')).toBe('DeepSeek-V4-Flash')
    expect(shortModelName('MiniMax-M2.7-bf16')).toBe('MiniMax-M2.7')
    expect(shortModelName('Qwen3-VL-32B-Instruct')).toBe('Qwen3-VL-32B')
    expect(shortModelName('deepseek-coder-33b-instruct')).toBe('deepseek-coder-33b')
    expect(shortModelName('claude-sonnet-4-20250514')).toBe('claude-sonnet-4')
  })
  it('带提供商前缀时只取模型名；空值返回空串', () => {
    expect(shortModelName('openai/gpt-4o-mini')).toBe('gpt-4o-mini')
    expect(shortModelName('')).toBe('')
    expect(shortModelName(null)).toBe('')
  })
  it('版本号里的数字不会被当成日期删掉', () => {
    expect(shortModelName('Qwen3-235B-A22B')).toBe('Qwen3-235B-A22B')
    expect(shortModelName('gpt-4o')).toBe('gpt-4o')
  })
})

describe('verifyLabel', () => {
  it('四种核实状态各一个短词', () => {
    expect(verifyLabel('verified')).toBe('已核实')
    expect(verifyLabel('partial')).toBe('部分核实')
    expect(verifyLabel('unverified')).toBe('未核实')
    expect(verifyLabel('no_evidence')).toBe('无依据')
    expect(verifyLabel('whatever')).toBe('')
  })
})

describe('cardMetaParts', () => {
  const base = {
    task_type: 'term',
    intent: 'term',
    object_type: 'selection',
    selection: { page: 1, text: 'giant connected component', passage_ords: [3] },
    model: 'DeepSeek-V4-Flash-0731-W8A8',
    created_at: new Date().toISOString(),
    saved: true
  } as never
  it('类型 · 页码 · 模型短名 · 时间，合成一行', () => {
    const parts = cardMetaParts(base)
    expect(parts.slice(0, 3)).toEqual(['术语解释', '第 1 页', 'DeepSeek-V4-Flash'])
    expect(parts[3]).toBe('刚刚')
  })
  it('自由提问没有页码这一项', () => {
    const parts = cardMetaParts({ ...(base as object), selection: null, object_type: 'free' } as never)
    expect(parts).not.toContain('第 1 页')
  })
  it('公式卡片写“公式”', () => {
    expect(cardMetaParts({ ...(base as object), object_type: 'equation' } as never)).toContain('公式')
  })
})

describe('questionLine', () => {
  it('选中的文字和问题合成一行', () => {
    const q = questionLine('这是什么', 'giant connected component')
    expect(q.text).toBe('“giant connected component” · 这是什么')
    expect(q.full).toContain('giant connected component')
  })
  it('两者相同只留一份，都没有则为空', () => {
    expect(questionLine('abc', 'abc').text).toBe('“abc”')
    expect(questionLine('', '').text).toBe('')
    expect(questionLine('只有问题', null).text).toBe('只有问题')
  })
  it('过长的选中文字截断', () => {
    expect(questionLine('', 'x'.repeat(200)).text.length).toBeLessThan(80)
  })
})
