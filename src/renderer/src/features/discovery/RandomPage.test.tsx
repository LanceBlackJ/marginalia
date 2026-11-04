// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  random: vi.fn(),
  profile: vi.fn(),
  fields: vi.fn(),
  venues: vi.fn(),
  similar: vi.fn(),
  loadSaved: vi.fn()
}))

vi.mock('@/lib/api.discovery', () => ({ discoveryApi: mocks, profileApi: { get: mocks.profile } }))
vi.mock('@/lib/api', () => ({ errorMessage: (e: unknown) => String(e) }))
vi.mock('./BasketBar', () => ({ BasketBar: () => null }))
vi.mock('./VenueIntro', () => ({ VenueIntro: () => null }))
vi.mock('./WorkDrawer', () => ({ WorkDrawer: () => null }))
vi.mock('./WorkItem', () => ({ WorkItem: ({ work }: { work: { title: string } }) => <div data-testid="work">{work.title}</div> }))
vi.mock('./discoveryStore', () => ({ useSaved: (sel: (s: { load: () => void }) => unknown) => sel({ load: mocks.loadSaved }) }))
vi.mock('./useWorkActions', () => ({ useLibraryMatchRefresh: () => undefined }))

import { RandomPage, useRandom } from './RandomPage'

const RESPONSE = {
  items: [
    {
      key: 'doi:10.1/x', title: 'A top venue paper', year: 2022, venue: 'International Conference on Machine Learning', in_library: null, summary: null,
      top_venue: { id: 'icml', name: 'International Conference on Machine Learning', short: 'ICML', kind: 'conference', category: '机器学习/AI 顶会', label: '已确认顶会' }
    }
  ],
  filter: {
    field: null, field_zh: null, field_id: null, venue_pool: 80, venue_policy: 'p',
    venues: [{ id: 'icml', name: 'International Conference on Machine Learning', short: 'ICML', kind: 'conference', category: 'x' }],
    pool_total: 100, pool_passed: 50, sampled: 20, dropped_unconfirmed: 3, after_exclusions: 10,
    thresholds: { fwci_min: 3, years: '2016-2025', venue: 'v', type: 't' }
  },
  ai: false
}

beforeEach(() => {
  mocks.random.mockReset().mockResolvedValue(RESPONSE)
  mocks.fields.mockReset().mockResolvedValue({ items: [{ id: 17, name: 'Computer Science', name_zh: '计算机科学' }] })
  mocks.profile.mockReset().mockResolvedValue({ meta: { interests: [] } })
  mocks.venues.mockReset().mockResolvedValue({ count: 1, policy: 'p', items: [] })
  mocks.loadSaved.mockReset()
  useRandom.setState({ history: [], current: null, filter: null, loading: false, error: null, similar: null, fieldId: '', topic: '', ai: true, yearFrom: '', yearTo: '', fields: [], interests: [], staticLoaded: false })
})
afterEach(cleanup)

describe('随机探索：只在点击按钮后才推荐', () => {
  it('打开页面不请求任何推荐，只显示说明和按钮', async () => {
    render(<RandomPage />)
    await waitFor(() => expect(mocks.fields).toHaveBeenCalled()) // 只加载静态的学科列表
    expect(mocks.random).not.toHaveBeenCalled()
    expect(mocks.venues).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: /随机探索一篇/ })).toBeTruthy()
    expect(screen.queryByTestId('work')).toBeNull()
    expect(screen.getByText(/只显示来源可确认的论文/)).toBeTruthy()
  })

  it('点击按钮才请求一次，并显示“已确认顶会”和来源名称、年份', async () => {
    render(<RandomPage />)
    fireEvent.click(screen.getByRole('button', { name: /随机探索一篇/ }))
    await waitFor(() => expect(screen.getByTestId('work')).toBeTruthy())
    expect(mocks.random).toHaveBeenCalledTimes(1)
    expect(mocks.random).toHaveBeenCalledWith({ field_id: null, topic: null, exclude: [], count: 1, ai: true, year_from: null, year_to: null })
    expect(screen.getByText('已确认顶会')).toBeTruthy()
    expect(screen.getByText(/International Conference on Machine Learning · 2022/)).toBeTruthy()
    expect(screen.getByRole('button', { name: /换一篇/ })).toBeTruthy()
  })

  it('离开再回到这个页面不会重新推荐，看到的还是上次点出来的内容', async () => {
    const first = render(<RandomPage />)
    fireEvent.click(screen.getByRole('button', { name: /随机探索一篇/ }))
    await waitFor(() => expect(screen.getByTestId('work')).toBeTruthy())
    first.unmount()
    render(<RandomPage />)
    expect(screen.getByTestId('work')).toBeTruthy()
    expect(mocks.random).toHaveBeenCalledTimes(1)
  })

  it('请求失败时显示错误，不自动重试', async () => {
    mocks.random.mockRejectedValueOnce(new Error('boom'))
    render(<RandomPage />)
    fireEvent.click(screen.getByRole('button', { name: /随机探索一篇/ }))
    await waitFor(() => expect(screen.getByText(/boom/)).toBeTruthy())
    expect(mocks.random).toHaveBeenCalledTimes(1)
  })

  it('研究画像里没有方向时不显示“我的方向”选项', async () => {
    render(<RandomPage />)
    await waitFor(() => expect(mocks.profile).toHaveBeenCalled())
    const select = screen.getByLabelText('学科或研究方向') as HTMLSelectElement
    expect([...select.options].some((o) => o.value.startsWith('topic:'))).toBe(false)
  })

  it('研究画像里的方向成为选项，选中后点击按钮会带上该方向、且不带学科', async () => {
    mocks.profile.mockResolvedValue({ meta: { interests: ['复杂网络', '  ', 'graph neural networks'] } })
    render(<RandomPage />)
    await waitFor(() => expect(screen.getByRole('option', { name: '我的方向：复杂网络' })).toBeTruthy())
    expect(screen.queryByRole('option', { name: /我的方向：\s*$/ })).toBeNull() // 空白方向被过滤
    expect(mocks.random).not.toHaveBeenCalled() // 加载画像不会触发任何推荐
    fireEvent.change(screen.getByLabelText('学科或研究方向'), { target: { value: 'topic:graph neural networks' } })
    fireEvent.click(screen.getByRole('button', { name: /随机探索一篇/ }))
    await waitFor(() => expect(mocks.random).toHaveBeenCalledTimes(1))
    expect(mocks.random).toHaveBeenCalledWith({ field_id: null, topic: 'graph neural networks', exclude: [], count: 1, ai: true, year_from: null, year_to: null })
  })
})


describe('随机探索：年份范围', () => {
  it('选了年份范围就随请求发出去', async () => {
    mocks.random.mockResolvedValue(RESPONSE)
    useRandom.setState({ yearFrom: '2018', yearTo: '2021' })
    render(<RandomPage />)
    fireEvent.click(screen.getByRole('button', { name: /随机探索一篇/ }))
    await waitFor(() => expect(mocks.random).toHaveBeenCalledTimes(1))
    expect(mocks.random.mock.calls[0][0]).toMatchObject({ year_from: 2018, year_to: 2021 })
    useRandom.setState({ yearFrom: '', yearTo: '' })
  })
})
