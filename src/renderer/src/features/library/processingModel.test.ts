import { describe, expect, it } from 'vitest'
import type { ProcessingSummary, ProcessingStep } from '@/lib/types'
import { isProcessingActive, processingCell, processingNote } from './processingModel'

const step = (state: ProcessingStep['state'], done = 0, total = 0, reason = '', attention = false): ProcessingStep => ({ state, done, total, reason, attention })
const make = (overall: ProcessingSummary['overall'], over: Partial<ProcessingSummary['steps']> = {}, reasons: string[] = [], label = ''): ProcessingSummary => ({
  overall,
  label,
  reasons,
  steps: { analysis: step('done'), formulas: step('done'), tables: step('done'), index: step('done'), ...over }
})

describe('processingCell', () => {
  it('只有 overall=done 才显示“已完成”，即使整体分析已经好了', () => {
    const p = make('processing', { formulas: step('running', 3, 12) }, [], '处理中（公式 3/12）')
    const c = processingCell(p, 'ready')
    expect(c?.text).toBe('处理中（公式 3/12）')
    expect(c?.text).not.toContain('已完成')
    expect(processingCell(make('done'), 'ready')?.text).toBe('已完成')
  })
  it('需要注意时悬停提示列出每一条原因', () => {
    const c = processingCell(make('attention', { formulas: step('skipped', 0, 3, '未配置视觉模型，公式仍是截图', true) }, ['公式：未配置视觉模型，公式仍是截图']), 'ready')
    expect(c).toMatchObject({ text: '需要注意', tone: 'warning', title: '公式：未配置视觉模型，公式仍是截图' })
  })
  it('旧后端没有 processing 时退回只看分析状态', () => {
    expect(processingCell(undefined, 'ready')?.text).toBe('已分析')
    expect(processingCell(undefined, 'failed')?.tone).toBe('danger')
    expect(processingCell(undefined, 'none')).toBeNull()
  })
})

describe('processingNote / isProcessingActive', () => {
  it('公式或表格没处理好时说明“部分内容可能还是截图”', () => {
    const n = processingNote(make('processing', { formulas: step('running', 2, 12), tables: step('pending', 0, 3) }))
    expect(n).toContain('公式识别中 2/12')
    expect(n).toContain('表格还原尚未开始 0/3')
    expect(n).toContain('部分内容可能还是截图')
  })
  it('全部处理好了没有提示；还在跑/等着的需要继续轮询', () => {
    expect(processingNote(make('done'))).toBeNull()
    expect(isProcessingActive(make('processing'))).toBe(true)
    expect(isProcessingActive(make('waiting'))).toBe(true)
    expect(isProcessingActive(make('attention'))).toBe(false)
    expect(isProcessingActive(make('done'))).toBe(false)
  })
})
