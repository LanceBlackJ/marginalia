// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom'

const mocks = vi.hoisted(() => ({ loadSaved: vi.fn(), clicked: vi.fn(), basket: 0 }))

vi.mock('./discoveryStore', () => ({ useSaved: (sel: (s: { load: () => void }) => unknown) => sel({ load: mocks.loadSaved }) }))
vi.mock('./BasketBar', () => ({
  BasketBar: () => {
    mocks.basket++
    return <div data-testid="basket" />
  }
}))
vi.mock('./DiscoverPage', () => ({
  DiscoverPage: () => (
    <div data-testid="panel-search">
      <button type="button" data-dc-primary="search" onClick={() => mocks.clicked('search')}>
        搜索按钮
      </button>
    </div>
  )
}))
vi.mock('./RecommendPage', () => ({
  RecommendPage: () => (
    <div data-testid="panel-recommend">
      <button type="button" data-dc-primary="recommend" onClick={() => mocks.clicked('recommend')}>
        获取推荐
      </button>
    </div>
  ),
  SavedSection: () => <div data-testid="panel-saved" />
}))
vi.mock('./RandomPage', () => ({
  RandomPage: () => (
    <div data-testid="panel-random">
      <button type="button" data-dc-primary="random" onClick={() => mocks.clicked('random')}>
        随机探索一篇
      </button>
    </div>
  )
}))

import { DiscoverHub, DISCOVER_SECTIONS, LegacyRedirect, parseTab } from './DiscoverHub'

function Where() {
  const loc = useLocation()
  return <div data-testid="where">{loc.pathname + loc.search}</div>
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="discover" element={<DiscoverHub />} />
        <Route path="recommend" element={<LegacyRedirect tab="recommend" />} />
        <Route path="random" element={<LegacyRedirect tab="random" />} />
      </Routes>
      <Where />
    </MemoryRouter>
  )
}

beforeEach(() => {
  mocks.loadSaved.mockReset()
  mocks.clicked.mockReset()
  mocks.basket = 0
})
afterEach(cleanup)

describe('parseTab（只用于旧链接）', () => {
  it('只认三种旧方式，其它一律回到搜索', () => {
    expect(parseTab('recommend')).toBe('recommend')
    expect(parseTab('random')).toBe('random')
    expect(parseTab('search')).toBe('search')
    expect(parseTab(null)).toBe('search')
    expect(parseTab('mode')).toBe('search')
  })
})

describe('发现页：三个标签，一次一个面板', () => {
  it('有搜索、为我推荐、随机探索三个标签，默认选中搜索，只显示搜索面板和收藏', () => {
    renderAt('/discover')
    expect(screen.getByRole('heading', { level: 1, name: '发现' })).toBeTruthy()
    const tabs = screen.getAllByRole('tab')
    expect(tabs.map((t) => t.textContent)).toEqual(DISCOVER_SECTIONS.map((s) => s.title))
    expect(screen.getByRole('tab', { name: '搜索' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('panel-search')).toBeTruthy()
    expect(screen.getByTestId('panel-saved')).toBeTruthy()
    expect(screen.queryByTestId('panel-recommend')).toBeNull()
    expect(screen.queryByTestId('panel-random')).toBeNull()
  })

  it('点标签换面板并写入地址；切换不会点击任何按钮（不发请求）', () => {
    renderAt('/discover')
    fireEvent.click(screen.getByRole('tab', { name: '随机探索' }))
    expect(screen.getByTestId('where').textContent).toBe('/discover?tab=random')
    expect(screen.getByTestId('panel-random')).toBeTruthy()
    expect(screen.queryByTestId('panel-search')).toBeNull()
    fireEvent.click(screen.getByRole('tab', { name: '为我推荐' }))
    expect(screen.getByTestId('panel-recommend')).toBeTruthy()
    expect(screen.queryByTestId('panel-random')).toBeNull()
    expect(mocks.clicked).not.toHaveBeenCalled()
  })

  it('方向键在标签间移动', () => {
    renderAt('/discover')
    fireEvent.keyDown(screen.getByRole('tab', { name: '搜索' }), { key: 'ArrowRight' })
    expect(screen.getByRole('tab', { name: '为我推荐' }).getAttribute('aria-selected')).toBe('true')
  })

  it('默认打开不点击任何按钮；比较篮和收藏各只放一份、只加载一次', () => {
    renderAt('/discover')
    expect(mocks.clicked).not.toHaveBeenCalled()
    expect(screen.getAllByTestId('basket')).toHaveLength(1)
    expect(mocks.loadSaved).toHaveBeenCalledTimes(1)
  })
})

describe('旧路径', () => {
  it('/discover?tab=random 直接显示随机探索，但不点击（不发请求）', () => {
    renderAt('/discover?tab=random')
    expect(screen.getByRole('tab', { name: '随机探索' }).getAttribute('aria-selected')).toBe('true')
    expect(screen.getByTestId('panel-random')).toBeTruthy()
    expect(mocks.clicked).not.toHaveBeenCalled()
  })

  it('/recommend 与 /random 重定向到对应标签，不会 404', () => {
    renderAt('/recommend')
    expect(screen.getByTestId('where').textContent).toBe('/discover?tab=recommend')
    expect(screen.getByTestId('panel-recommend')).toBeTruthy()
    cleanup()
    renderAt('/random')
    expect(screen.getByTestId('where').textContent).toBe('/discover?tab=random')
    expect(screen.getByTestId('panel-random')).toBeTruthy()
    expect(mocks.clicked).not.toHaveBeenCalled()
  })
})
