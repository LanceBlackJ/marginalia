// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import type { PaperSummary } from '@/lib/types'
import { PaperRow, type PaperItemActions } from './PaperItems'

afterEach(cleanup)

const paper = (over: Partial<PaperSummary> = {}): PaperSummary => ({
  id: 'p1',
  title: '一篇旧版解析的论文',
  authors: ['A. Author'],
  year: 2020,
  venue: 'Nature',
  doi: null,
  has_pdf: true,
  status: 'ready',
  parse_error: null,
  page_count: 8,
  language: 'en',
  favorite: false,
  reading_page: 1,
  read_progress: 0,
  last_opened_at: null,
  imported_at: '2026-01-01T00:00:00Z',
  source_folder: null,
  analysis_status: 'none',
  parser_version: 1,
  parser_outdated: true,
  ...over
})

const actions = (over: Partial<PaperItemActions> = {}): PaperItemActions => ({
  onOpen: vi.fn(),
  onToggleFavorite: vi.fn(),
  onDelete: vi.fn(),
  onEditTags: vi.fn(),
  onExport: vi.fn(),
  onReparse: vi.fn(),
  ...over
})

const renderRow = (p: PaperSummary, a: PaperItemActions) =>
  render(
    <MemoryRouter>
      <PaperRow paper={p} actions={a} />
    </MemoryRouter>
  )

describe('文库行：有新版解析的提示', () => {
  it('旧版解析的论文显示“有新版解析，可重新解析”，点一下触发重新解析而不是打开论文', () => {
    const a = actions()
    renderRow(paper(), a)
    const hint = screen.getByRole('button', { name: /有新版解析，可重新解析/ })
    fireEvent.click(hint)
    expect(a.onReparse).toHaveBeenCalledTimes(1)
    expect(a.onOpen).not.toHaveBeenCalled() // 点提示不应冒泡成“打开阅读”
  })

  it('新版解析、没有 PDF、补充材料子论文都不显示提示', () => {
    for (const p of [paper({ parser_outdated: false }), paper({ has_pdf: false, status: 'metadata_only' }), paper({ role: 'supplement' })]) {
      const { unmount } = renderRow(p, actions())
      expect(screen.queryByText(/有新版解析/)).toBeNull()
      unmount()
    }
  })

  it('没有提供 onReparse 时不显示提示（不出现点了没反应的按钮）', () => {
    renderRow(paper(), actions({ onReparse: undefined }))
    expect(screen.queryByText(/有新版解析/)).toBeNull()
  })
})
