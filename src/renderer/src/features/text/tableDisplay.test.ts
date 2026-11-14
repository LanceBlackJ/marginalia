import { describe, expect, it } from 'vitest'
import type { TableAnalysisItem } from '@/lib/api.text'
import { tableDisplayReason, tablePrefersImage } from './tableDisplay'

const item = (over: Partial<TableAnalysisItem> = {}): TableAnalysisItem => ({
  kind: 'table', caption_ord: 1, label: 'Table 1', page: 1, body_ords: [], bbox: [0, 0, 100, 100],
  status: 'ready', source: 'layout', error: null, model: null,
  data: { title: '', columns: ['A'], rows: [['1']], notes: '', unverified_cells: [], misplaced_cells: [], verified_ratio: 1, warnings: [] },
  ...over
})

describe('table display mode', () => {
  it('keeps a clean text-layer table structured', () => {
    expect(tablePrefersImage(item({ source: 'text' }))).toBe(false)
  })

  it('defaults visual and figure-embedded tables to the original image', () => {
    expect(tablePrefersImage(item({ source: 'vision' }))).toBe(true)
    expect(tablePrefersImage(item({ kind: 'figure_data', source: 'layout' }))).toBe(true)
  })

  it('keeps a usable layout table structured even when coordinate confidence is low', () => {
    expect(tablePrefersImage(item({ data: { ...item().data!, layout: { confidence: 0.72, blocks: 1 } } }))).toBe(false)
    expect(tableDisplayReason(item({ data: { ...item().data!, layout: { confidence: 0.72, blocks: 1 } } }))).toContain('建议对照原图')
  })

  it('falls back to the image when a structured result is unusable', () => {
    expect(tablePrefersImage(item({ data: { ...item().data!, unverified_cells: [[0, 0]] } }))).toBe(true)
    expect(tablePrefersImage(item({ data: { ...item().data!, columns: [], rows: [] } }))).toBe(true)
    expect(tableDisplayReason(item({ source: 'vision' }))).toContain('原图优先')
  })
})
