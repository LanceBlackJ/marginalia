// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('@/stores/jobsStore', () => ({
  useActiveJobs: () => [],
  useJobsStore: (sel: (s: { finishedTick: number }) => unknown) => sel({ finishedTick: 0 })
}))
vi.mock('@/stores/backendStore', () => ({
  useBackendStore: (sel: (s: { status: string }) => unknown) => sel({ status: 'idle' })
}))
vi.mock('@/features/importing/importActions', () => ({
  importFolder: vi.fn(),
  importLocalFiles: vi.fn(),
  useImportUi: () => ({ openDoi: vi.fn(), openUrl: vi.fn() })
}))

import { Sidebar } from './Sidebar'
import { Topbar } from './Topbar'

afterEach(cleanup)

function renderTopbar(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Topbar />
    </MemoryRouter>
  )
}

describe('侧栏', () => {
  it('主导航只有五项，任务中心与设置固定在底部；不再重复“最近阅读”“收藏”', () => {
    render(
      <MemoryRouter initialEntries={['/library']}>
        <Sidebar collapsed={false} onToggle={() => undefined} />
      </MemoryRouter>
    )
    const nav = screen.getByRole('navigation', { name: '主导航' })
    for (const label of ['文献库', '发现', '多论文比较', '主题知识库', '阅读笔记']) {
      expect(within(nav).getByText(label)).toBeTruthy()
    }
    expect(within(nav).queryByText('最近阅读')).toBeNull()
    expect(within(nav).queryByText('收藏')).toBeNull()
    const system = nav.querySelector('.pd-nav-system') as HTMLElement
    expect(within(system).getByText('任务中心')).toBeTruthy()
    expect(within(system).getByText('设置')).toBeTruthy()
    // 当前页高亮
    expect(within(nav).getByText('文献库').closest('button')?.getAttribute('aria-current')).toBe('page')
  })
})

describe('顶栏', () => {
  it('文库页：不重复页面标题，也不再放第二个“导入”按钮', () => {
    renderTopbar('/library')
    expect(screen.queryByRole('button', { name: '导入' })).toBeNull()
    expect(document.querySelector('.pd-crumbs')).toBeNull()
    expect(screen.getByLabelText('全局搜索')).toBeTruthy()
  })

  it('发现页：同样不重复标题；其它页面显示标题和次要的“导入”按钮', () => {
    renderTopbar('/discover')
    expect(document.querySelector('.pd-crumbs')).toBeNull()
    cleanup()
    renderTopbar('/tasks')
    expect(document.querySelector('.pd-crumbs')?.textContent).toBe('任务中心')
    const importBtn = screen.getByRole('button', { name: '导入' })
    expect(importBtn.className).not.toContain('primary')
  })
})
