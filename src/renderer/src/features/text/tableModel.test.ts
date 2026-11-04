import { describe, expect, it } from 'vitest'
import { isNumericCell, misplacedCount, misplacedSet, numericColumns, tableToMarkdown, tableToTsv, unverifiedCount, unverifiedSet } from './tableModel'

const T = {
  columns: ['Method', 'Crime', 'Digg'],
  rows: [
    ['FINDER', '8.19', '5.90'],
    ['No-GraphSAGE', '8.87', '12.52'],
    ['S2V-DQN', '8.48', 'NA']
  ]
}

describe('isNumericCell', () => {
  it('recognizes plain numbers, signs, percentages and ± forms', () => {
    for (const s of ['8.19', '12', '-3.5', '−0.4', '45%', '5 × 10', '0.10 ± 0.02', '1,234', '(0.5)', '8.19*']) expect(isNumericCell(s), s).toBe(true)
  })
  it('rejects words, empty cells and mixed labels', () => {
    for (const s of ['', '  ', 'FINDER', 'NA', 'S2V-DQN', 'ANC (×0.01)', '12 nodes']) expect(isNumericCell(s), s).toBe(false)
  })
})

describe('numericColumns', () => {
  it('marks numeric columns for right alignment and ignores the header', () => {
    expect(numericColumns(T)).toEqual([false, true, true]) // Digg 列里的 “NA” 是缺失值，不计入判断
  })
  it('needs at least 70% numeric cells', () => {
    expect(numericColumns({ columns: ['a', 'b'], rows: [['x', '1'], ['y', 'two'], ['z', 'three']] })).toEqual([false, false])
    expect(numericColumns({ columns: ['a'], rows: [] })).toEqual([false])
  })
})

describe('copy formats', () => {
  it('builds a markdown table and escapes pipes', () => {
    const md = tableToMarkdown({ columns: ['a|b', 'c'], rows: [['1', '2'], ['3']] })
    expect(md.split('\n')).toEqual(['| a\\|b | c |', '| --- | --- |', '| 1 | 2 |', '| 3 |  |'])
  })
  it('builds TSV that Excel can paste and flattens tabs/newlines inside cells', () => {
    expect(tableToTsv({ columns: ['a', 'b'], rows: [['x\ty', '2\n3']] })).toBe('a\tb\nx y\t2 3')
  })
})

describe('misplaced cells', () => {
  it('are tracked separately from cells missing in the text layer', () => {
    const d = { unverified_cells: [[0, 1]] as Array<[number, number]>, misplaced_cells: [[1, 2]] as Array<[number, number]> }
    expect(misplacedSet(d).has('1,2')).toBe(true)
    expect(misplacedSet(d).has('0,1')).toBe(false)
    expect(unverifiedSet(d).has('1,2')).toBe(false)
    expect(misplacedCount(d)).toBe(1)
  })
  it('treats results saved before the field existed as having none', () => {
    expect(misplacedSet({}).size).toBe(0)
    expect(misplacedCount({})).toBe(0)
  })
})

describe('unverified cells', () => {
  it('turns [row, col] pairs into a lookup set', () => {
    const s = unverifiedSet({ unverified_cells: [[0, 1], [2, 2]] })
    expect(s.has('0,1') && s.has('2,2') && !s.has('1,1')).toBe(true)
    expect(unverifiedCount({ unverified_cells: [[0, 1], [2, 2]] })).toBe(2)
    expect(unverifiedCount({ unverified_cells: undefined as unknown as [] })).toBe(0)
  })
})
