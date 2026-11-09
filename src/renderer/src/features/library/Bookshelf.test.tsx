// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render as rtlRender, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

const render = (ui: React.ReactElement): ReturnType<typeof rtlRender> => rtlRender(<MemoryRouter>{ui}</MemoryRouter>)

vi.mock('@/lib/api', () => ({ api: { papers: { cover: vi.fn(() => Promise.reject(new Error('no cover'))) } } }))

import { Bookshelf, sectionsFor, type BookshelfActions } from './Bookshelf'
import type { PaperSummary } from '@/lib/types'
import type { FolderItem } from '@/lib/types.library'

const paper = (id: string, over: Partial<PaperSummary> = {}): PaperSummary =>
  ({
    id, title: `论文 ${id}`, authors: ['A B'], year: 2024, venue: 'V', doi: null, has_pdf: true, status: 'ready', parse_error: null,
    page_count: 10, language: 'en', favorite: false, reading_page: 1, read_progress: 0, last_opened_at: null, imported_at: '2026-01-01',
    source_folder: null, analysis_status: 'none', ...over
  }) as PaperSummary

const folder = (id: string, name: string, parent: string | null = null, sort = 0): FolderItem => ({ id, name, parent_id: parent, sort, paper_count: 0, total_count: 0 })

const actions = (over: Partial<BookshelfActions> = {}): BookshelfActions => ({
  onOpen: vi.fn(),
  onToggleFavorite: vi.fn(),
  onMove: vi.fn(),
  onCreateFolderFor: vi.fn(async () => undefined),
  ...over
})

afterEach(cleanup)

describe('书架', () => {
  it('点封面和点书名都能打开这篇论文', () => {
    const a = actions()
    render(<Bookshelf papers={[paper('a'), paper('b')]} folders={[]} grouped={false} actions={a} />)
    const opens = screen.getAllByRole('button', { name: '打开《论文 b》' })
    expect(opens).toHaveLength(2) // 封面一个、书名一个
    fireEvent.click(opens[0])
    fireEvent.click(opens[1])
    const calls = (a.onOpen as ReturnType<typeof vi.fn>).mock.calls
    expect(calls).toHaveLength(2)
    expect(calls.every((c) => c[0].id === 'b')).toBe(true)
  })

  it('加了标签的论文，标签直接显示在卡片上（最多三个，其余折成 +N）', () => {
    const tags = ['图神经网络', '综述', '待读', '复现'].map((name, i) => ({ id: `t${i}`, name }))
    const { container } = render(<Bookshelf papers={[paper('a', { tags })]} folders={[]} grouped={false} actions={actions()} />)
    const shown = [...container.querySelectorAll('.pd-book-tags .pd-book-tag')].map((e) => e.textContent)
    expect(shown).toEqual(['图神经网络', '综述', '待读', '+1'])
  })

  it('书名旁带一个完整标题的提示：标题、原标题、作者、来源', () => {
    render(<Bookshelf papers={[paper('a', { alias: '我的名字', title: 'A very long original title that does not fit', venue: 'Nature', year: 2020 })]} folders={[]} grouped={false} actions={actions()} />)
    const tip = screen.getByRole('tooltip')
    expect(tip.textContent).toContain('我的名字')
    expect(tip.textContent).toContain('原标题：A very long original title that does not fit')
    expect(tip.textContent).toContain('Nature · 2020')
  })

  it('有备注名就显示备注名', () => {
    render(<Bookshelf papers={[paper('a', { alias: '我的综述' })]} folders={[]} grouped={false} actions={actions()} />)
    expect(screen.getAllByRole('button', { name: '打开《我的综述》' }).length).toBeGreaterThan(0)
  })

  it('点星标只切换收藏，不会打开论文', () => {
    const a = actions()
    render(<Bookshelf papers={[paper('a')]} folders={[]} grouped={false} actions={a} />)
    fireEvent.click(screen.getByRole('button', { name: '收藏' }))
    expect(a.onToggleFavorite).toHaveBeenCalledTimes(1)
    expect(a.onOpen).not.toHaveBeenCalled()
  })

  it('解析中、没有全文的论文在封面上标出状态；有阅读进度时画出进度条', () => {
    const { container } = render(
      <Bookshelf
        papers={[paper('a', { status: 'parsing' }), paper('b', { status: 'metadata_only', has_pdf: false }), paper('c', { read_progress: 0.4 })]}
        folders={[]}
        grouped={false}
        actions={actions()}
      />
    )
    expect(screen.getByText('解析中')).toBeTruthy()
    expect(screen.getByText('无全文')).toBeTruthy()
    expect((container.querySelector('.pd-book-progress') as HTMLElement).style.width).toBe('40%')
  })

  it('点“放进文件夹”，再点一个文件夹就移过去', () => {
    const a = actions()
    render(<Bookshelf papers={[paper('a')]} folders={[folder('f1', '图神经网络')]} grouped={false} actions={a} />)
    fireEvent.click(screen.getByRole('button', { name: '放进文件夹' }))
    fireEvent.click(screen.getByText('图神经网络'))
    expect(a.onMove).toHaveBeenCalledTimes(1)
    expect((a.onMove as ReturnType<typeof vi.fn>).mock.calls[0][1]).toBe('f1')
  })
})

describe('按文件夹分层', () => {
  const folders = [folder('f1', '图神经网络', null, 0), folder('f2', '注意力', 'f1', 0), folder('f3', '空文件夹', null, 1)]

  it('文件夹按树的顺序各占一层，没有论文的文件夹不显示，没放进文件夹的在最后叫“未分类”', () => {
    const s = sectionsFor([paper('a', { folder_id: 'f2' }), paper('b'), paper('c', { folder_id: 'f1' })], folders, true)
    expect(s.map((x) => [x.title, x.papers.map((p) => p.id)])).toEqual([
      ['图神经网络', ['c']],
      ['图神经网络 / 注意力', ['a']],
      ['未分类', ['b']]
    ])
  })

  it('不分层时只有一层，没有标题；全都没分类时也不写“未分类”', () => {
    expect(sectionsFor([paper('a'), paper('b')], folders, false)).toEqual([{ key: 'all', title: null, papers: [paper('a'), paper('b')] }])
    expect(sectionsFor([paper('a')], folders, true)[0].title).toBeNull()
  })

  it('指向已删除文件夹的论文算未分类', () => {
    const s = sectionsFor([paper('a', { folder_id: 'gone' }), paper('b', { folder_id: 'f1' })], folders, true)
    expect(s.map((x) => x.title)).toEqual(['图神经网络', '未分类'])
  })

  it('点文件夹标签可以收起这一层', () => {
    render(<Bookshelf papers={[paper('a', { folder_id: 'f1' }), paper('b')]} folders={folders} grouped actions={actions()} />)
    expect(screen.getAllByRole('button', { name: '打开《论文 a》' }).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByText('图神经网络'))
    expect(screen.queryAllByRole('button', { name: '打开《论文 a》' })).toHaveLength(0)
    expect(screen.getAllByRole('button', { name: '打开《论文 b》' }).length).toBeGreaterThan(0)
  })
})
