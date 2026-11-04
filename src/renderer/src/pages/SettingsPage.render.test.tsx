// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const mocks = vi.hoisted(() => ({
  hasKey: true,
  pages: [] as Array<{ items: unknown[]; has_more: boolean; total: number; task_types: unknown[] }>,
  calls: [] as Array<Record<string, unknown>>
}))

// 各内容区块依赖很多接口，这里只检查页面骨架与面板行为，所以替换成占位。
vi.mock('@/features/settings/AcademicSection', () => ({ AcademicSection: () => <div /> }))
vi.mock('@/features/settings/ProfileSection', () => ({ ProfileSection: () => <div /> }))
vi.mock('@/features/settings/UsageSection', () => ({ UsageSection: () => <div /> }))
vi.mock('@/features/settings/SimpleSections', () => ({
  AppearanceSection: () => <div />,
  CodeExecutionSection: () => <div />,
  FilesSection: () => <div />,
  GeneralSection: () => <div />,
  ShortcutsSection: () => <div />
}))
vi.mock('@/features/settings/AIModelSection', async () => {
  const { SettingsPanel } = await import('@/features/settings/SettingRow')
  return {
    AIModelSection: () => (
      <>
        <SettingsPanel id="models" title="连接与模型" summary="未保存 API Key" defaultOpen={!mocks.hasKey}>
          <div data-testid="models-body" />
        </SettingsPanel>
        <SettingsPanel id="cost" title="费用与预算" summary="已设 4 个模型的价格">
          <div />
        </SettingsPanel>
      </>
    )
  }
})
vi.mock('@/lib/native', () => ({ clearApiKey: vi.fn() }))
vi.mock('@/lib/api', () => ({
  api: {
    settings: { getAI: () => Promise.resolve({ has_api_key: mocks.hasKey }) },
    systemInfo: () => Promise.resolve({ version: '0.1.0', paper_count: 3, disk_usage_bytes: 2048 }),
    usage: () => Promise.resolve({ calls: 99, estimated_cost: 0.7777 }),
    privacyRequests: (q: Record<string, unknown>) => {
      mocks.calls.push(q)
      return Promise.resolve(mocks.pages.shift() ?? { items: [], has_more: false, total: 0, task_types: [] })
    }
  },
  errorMessage: (e: unknown) => String(e)
}))

import { SettingsPage, SETTINGS_TABS, tabOfPanel } from './SettingsPage'

const req = (id: number, summary = `摘要 ${id}`) => ({
  id: String(id), ts: '2026-10-30T10:00:00Z', task_type: 'ask', model: 'm', paper_id: null, paper_title: null,
  passage_count: 2, chars_sent: 1200, includes: { question: true, selection: false, notes: false, supplements: false, code: false }, summary
})

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/settings/:section" element={<SettingsPage />} />
      </Routes>
    </MemoryRouter>
  )
}

const goTab = (name: string): void => {
  fireEvent.click(screen.getByRole('tab', { name }))
}
const renderDataTab = (): ReturnType<typeof renderAt> => {
  const r = renderAt('/settings')
  goTab('数据与隐私')
  return r
}

beforeEach(() => {
  mocks.hasKey = true
  mocks.pages = []
  mocks.calls = []
})
afterEach(() => cleanup())

describe('设置页：三个标签，内容顺着往下排', () => {
  it('分成模型、通用、数据与隐私三个标签，一次只显示一个标签里的内容', async () => {
    const { container } = renderAt('/settings')
    expect(screen.getAllByRole('tab').map((t) => t.textContent)).toEqual(SETTINGS_TABS.map((t) => t.label))
    const ids = (): string[] => Array.from(container.querySelectorAll('.pd-panel')).map((el) => el.id)
    expect(ids()).toEqual(['models', 'cost', 'retrieval', 'usage'])
    goTab('通用')
    expect(ids()).toEqual(['appearance', 'shortcuts', 'about'])
    goTab('数据与隐私')
    await waitFor(() => expect(container.querySelector('#danger')).not.toBeNull())
    expect(ids()).toEqual(['sendlog', 'privacynote', 'data', 'danger'])
    expect(tabOfPanel('danger')).toBe('data')
  })

  it('没有折叠按钮：每一段的内容直接显示在标题下面', () => {
    const { container } = renderAt('/settings')
    expect(container.querySelector('.pd-panel-toggle, [aria-expanded]')).toBeNull()
    expect(container.querySelector('#models .pd-panel-body [data-testid="models-body"]')).not.toBeNull()
    goTab('通用')
    expect(container.querySelector('#appearance .pd-panel-body')).not.toBeNull()
  })

  it('旧链接会切到对应标签，并滚动到那一段，让标题闪一下', async () => {
    Element.prototype.scrollIntoView = vi.fn()
    renderAt('/settings/profile')
    expect(document.querySelector('#retrieval')).not.toBeNull()
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalled(), { timeout: 1000 })
    cleanup()
    renderAt('/settings/files')
    expect(screen.getByRole('tab', { name: '数据与隐私' }).getAttribute('aria-selected')).toBe('true')
    expect(document.querySelector('#data')).not.toBeNull()
  })


  it('面板头摘要显示当前状态', async () => {
    renderAt('/settings')
    await waitFor(() => expect(screen.getByText('本月 99 次调用 · 约 0.78 元')).toBeTruthy())
    goTab('数据与隐私')
    await waitFor(() => expect(screen.getByText('3 篇论文 · 占用 2.0 KB')).toBeTruthy())
    goTab('通用')
    await waitFor(() => expect(screen.getByText('Marginalia · 版本 0.1.0')).toBeTruthy())
  })
})

describe('发送记录：默认 5 条、显示更多、筛选', () => {
  it('摘要是总条数和最新时间；展开后默认只列 5 条，点一行才看详情', async () => {
    mocks.pages = [{ items: [1, 2, 3, 4, 5].map((i) => req(i)), has_more: true, total: 42, task_types: [{ task_type: 'ask', count: 42 }] }]
    const { container } = renderDataTab()
    await waitFor(() => expect(container.querySelector('#sendlog .pd-panel-sum')?.textContent).toMatch(/^共 42 条 · 最新 /))
    const rows = container.querySelectorAll('#sendlog .pd-log-row')
    expect(rows.length).toBe(5)
    expect(mocks.calls[0]).toMatchObject({ limit: 5 })
    expect(container.querySelector('#sendlog .pd-log-detail')).toBeNull() // 详情默认不展开
    fireEvent.click(rows[0])
    expect(container.querySelector('#sendlog .pd-log-detail')).not.toBeNull()
  })

  it('“显示更多”用游标再取 20 条；到底了写“没有更早的了”', async () => {
    mocks.pages = [
      { items: [20, 19, 18, 17, 16].map((i) => req(i)), has_more: true, total: 6, task_types: [{ task_type: 'ask', count: 6 }] },
      { items: [req(15)], has_more: false, total: 6, task_types: [{ task_type: 'ask', count: 6 }] }
    ]
    const { container } = renderDataTab()
    const more = await screen.findByRole('button', { name: '显示更多' })
    fireEvent.click(more)
    await waitFor(() => expect(container.querySelectorAll('#sendlog .pd-log-row').length).toBe(6))
    expect(mocks.calls[1]).toMatchObject({ limit: 20, before_id: 16 })
    expect(within(container.querySelector('#sendlog') as HTMLElement).getByText('没有更早的了')).toBeTruthy()
    expect(screen.queryByRole('button', { name: '显示更多' })).toBeNull()
  })

  it('按任务类型筛选会回到第一页并带上 task_type', async () => {
    mocks.pages = [
      { items: [3, 2, 1].map((i) => req(i)), has_more: false, total: 3, task_types: [{ task_type: 'ask', count: 2 }, { task_type: 'translate', count: 1 }] },
      { items: [req(2)], has_more: false, total: 1, task_types: [{ task_type: 'ask', count: 2 }, { task_type: 'translate', count: 1 }] }
    ]
    const { container } = renderDataTab()
    const select = (await screen.findByLabelText('按任务类型筛选')) as HTMLSelectElement | HTMLElement
    fireEvent.change(select, { target: { value: 'translate' } })
    await waitFor(() => expect(mocks.calls.some((c) => c.task_type === 'translate')).toBe(true))
    expect(mocks.calls[1]).toMatchObject({ limit: 5, task_type: 'translate' })
    expect(container.querySelector('#sendlog .pd-panel-sum')?.textContent).toMatch(/^共 3 条/) // 摘要始终是总数，不受筛选影响
  })

  it('没有任何记录时说明还没有发送过内容，也不显示“显示更多”', async () => {
    mocks.pages = [{ items: [], has_more: false, total: 0, task_types: [] }]
    const { container } = renderDataTab()
    await waitFor(() => expect(container.querySelector('#sendlog .pd-panel-sum')?.textContent).toBe('还没有发送过内容'))
    expect(screen.queryByRole('button', { name: '显示更多' })).toBeNull()
  })

  it('没有保存过 API Key 时没有危险操作可做，整个面板不显示', async () => {
    mocks.hasKey = false
    const { container } = renderDataTab()
    await new Promise((r) => setTimeout(r, 30))
    expect(container.querySelector('#danger')).toBeNull()
  })
})
