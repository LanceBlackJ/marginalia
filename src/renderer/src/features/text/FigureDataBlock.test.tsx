// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import type { TableAnalysisItem } from '@/lib/api.text'
import { FigureDataBlock } from './FigureDataBlock'

afterEach(cleanup)

const rows = Array.from({ length: 10 }, (_, i) => [`Net${i + 1}`, `${(i + 1) * 1.11}`, `${20 + i}`])
const item = (over: Partial<TableAnalysisItem> = {}): TableAnalysisItem =>
  ({
    caption_ord: 7,
    label: 'Fig. 5',
    page: 6,
    body_ords: [51, 52],
    bbox: [0, 0, 100, 100],
    status: 'ready',
    source: 'layout',
    error: null,
    model: null,
    kind: 'figure_data',
    data: {
      title: '',
      columns: [],
      rows: [],
      notes: '',
      unverified_cells: [],
      verified_ratio: 1,
      warnings: [],
      blocks: [{ title: 'CN – node-random-weighted', columns: ['', 'HDA', 'CI'], rows, confidence: 1 }]
    }
  }) as unknown as TableAnalysisItem

describe('图中数据表的版式', () => {
  it('子表标题在表格上方，表格放在占满栏宽的外层里，数值列的表头与数据同向右对齐', () => {
    const { container } = render(<FigureDataBlock paperId="p" t={item()} running={false} onRetry={() => undefined} />)
    const title = container.querySelector('.pd-fd-title') as HTMLElement
    const wrap = container.querySelector('.pd-fd-wrap') as HTMLElement
    expect(title.textContent).toBe('CN – node-random-weighted')
    expect(title.compareDocumentPosition(wrap) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    const heads = Array.from(wrap.querySelectorAll('thead th'))
    expect(heads).toHaveLength(3)
    expect(heads[0].textContent).toBe('') // 左上角空格也在，线是连续的
    expect(heads[1].className).toContain('pd-num')
    expect(heads[2].className).toContain('pd-num')
    expect(heads[0].className).not.toContain('pd-num')
    const firstRow = wrap.querySelector('tbody tr') as HTMLElement
    expect(firstRow.children[1].className).toContain('pd-num')
  })

  it('默认只显示前 5 行，操作行靠左并写“展开 10 行”，展开后变成“收起”', () => {
    const { container } = render(<FigureDataBlock paperId="p" t={item()} running={false} onRetry={() => undefined} />)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(5)
    const actions = container.querySelector('.pd-fd-actions') as HTMLElement
    expect(actions).toBeTruthy()
    const toggle = screen.getByRole('button', { name: '展开 10 行' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(toggle)
    expect(container.querySelectorAll('tbody tr')).toHaveLength(10)
    expect(screen.getByRole('button', { name: '收起' }).getAttribute('aria-expanded')).toBe('true')
    // 操作行里是文字按钮（pd-fd-act），不是居中的 pd-tp-link
    expect(actions.querySelectorAll('.pd-fd-act').length).toBeGreaterThanOrEqual(3)
    expect(actions.querySelector('.pd-tp-link')).toBeNull()
    expect(screen.getByRole('button', { name: '复制为 TSV' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '复制为 Markdown' })).toBeTruthy()
  })

  it('不超过 5 行的表没有展开按钮', () => {
    const t = item()
    ;(t.data as unknown as { blocks: Array<{ rows: string[][] }> }).blocks[0].rows = rows.slice(0, 3)
    render(<FigureDataBlock paperId="p" t={t} running={false} onRetry={() => undefined} />)
    expect(screen.queryByRole('button', { name: /展开/ })).toBeNull()
  })
})
