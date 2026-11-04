import { describe, expect, it } from 'vitest'
import type { PlanStep } from '@/lib/types.repro'
import { commandEnabled, execActive, parseLimit, sourceLabel, envToText, evidenceLabel, formatNum, formatRel, parseEnvText, pendingCount, relToTolPercent, tolPercentToRel } from './reproUtils'

const step = (over: Partial<PlanStep>): PlanStep => ({
  id: 's', ord: 1, phase: 'run', phase_label: '完整运行', title: 't', detail: '', command: '', cwd: '', side_effects: [], risk: '', evidence: [],
  needs_approval: false, approval: 'none', done: false, note: '', origin: 'generated', updated_at: '', approval_log: [], ...over
})

describe('formatNum / formatRel', () => {
  it('rounds to 4 significant digits and handles empty values', () => {
    expect(formatNum(0.123456)).toBe('0.1235')
    expect(formatNum(90)).toBe('90')
    expect(formatNum(0)).toBe('0')
    expect(formatNum(null)).toBe('—')
    expect(formatNum(Number.NaN)).toBe('—')
    expect(formatNum(12345678)).toMatch(/e\+?7$/)
  })
  it('formats relative error as a percentage', () => {
    expect(formatRel(0.04)).toBe('4%')
    expect(formatRel(null)).toBe('—')
  })
})

describe('tolerance input', () => {
  it('turns a percentage into a relative tolerance', () => {
    expect(tolPercentToRel('5')).toBeCloseTo(0.05)
    expect(tolPercentToRel('2.5%')).toBeCloseTo(0.025)
    expect(tolPercentToRel('  ')).toBeNull()
    expect(tolPercentToRel('abc')).toBeUndefined()
    expect(tolPercentToRel('-1')).toBeUndefined()
    expect(relToTolPercent(0.05)).toBe('5')
    expect(relToTolPercent(null)).toBe('')
  })
})

describe('environment text', () => {
  it('parses key=value lines and round-trips', () => {
    const env = parseEnvText('python=3.8\nseed: 42\n随机种子：7\n  \n没有等号的行\ntorch = 1.9.0 ')
    expect(env).toEqual({ python: '3.8', seed: '42', 随机种子: '7', torch: '1.9.0' })
    expect(parseEnvText(envToText(env))).toEqual(env)
  })
})

describe('evidence label', () => {
  it('shows path:line for code, page for paper, and just the path for whole-file evidence', () => {
    expect(evidenceLabel({ type: 'code', path: 'a/b.py', line_start: 3, line_end: 3, quote: '' })).toBe('a/b.py:3')
    expect(evidenceLabel({ type: 'code', path: 'a/b.py', line_start: 3, line_end: 9, quote: '' })).toBe('a/b.py:3-9')
    expect(evidenceLabel({ type: 'code', path: 'w/model.pt', line_start: 0, line_end: 0, quote: '' })).toBe('w/model.pt')
    expect(evidenceLabel({ type: 'paper', page: 4, ord: 1, quote: '' })).toBe('论文第 4 页')
  })
})

describe('approval helpers', () => {
  it('counts pending steps and only enables approved commands', () => {
    const steps = [
      step({ id: 'a', command: 'pip install x', needs_approval: true, approval: 'pending' }),
      step({ id: 'b', command: 'python t.py', needs_approval: true, approval: 'approved' }),
      step({ id: 'c', command: 'rm -rf x', needs_approval: true, approval: 'rejected' }),
      step({ id: 'd' })
    ]
    expect(pendingCount(steps)).toBe(1)
    expect(steps.map(commandEnabled)).toEqual([false, true, false, false])
  })
})

describe('isolated run helpers', () => {
  it('accepts limits only inside the allowed range', () => {
    expect(parseLimit('512', 128, 8192)).toBe(512)
    expect(parseLimit('0.5', 0.25, 8)).toBe(0.5)
    expect(parseLimit('99999', 128, 8192)).toBeNull()
    expect(parseLimit('10', 128, 8192)).toBeNull()
    expect(parseLimit('', 1, 5)).toBeNull()
    expect(parseLimit('abc', 1, 5)).toBeNull()
  })
  it('tells where a number came from and never invents a source', () => {
    expect(sourceLabel({ run_id: 'r', kind: 'log', path: 'run.log', line: 12 })).toBe('日志第 12 行')
    expect(sourceLabel({ run_id: 'r', kind: 'file', path: 'results.csv', line: null })).toBe('输出文件 results.csv')
    expect(sourceLabel(undefined)).toBe('你导入的内容')
  })
  it('treats queued and running as active, everything else as finished', () => {
    expect(execActive({ status: 'running' })).toBe(true)
    expect(execActive({ status: 'queued' })).toBe(true)
    for (const s of ['succeeded', 'failed', 'timeout', 'cancelled'] as const) expect(execActive({ status: s })).toBe(false)
  })
})
