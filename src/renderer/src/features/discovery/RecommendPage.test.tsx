// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'

const mocks = vi.hoisted(() => ({ direction: vi.fn(), personal: vi.fn(), loadSaved: vi.fn() }))

vi.mock('@/lib/api.discovery', () => ({ discoveryApi: { direction: mocks.direction, personal: mocks.personal } }))
vi.mock('@/lib/api', () => ({ errorMessage: (e: unknown) => String(e) }))
vi.mock('./WorkDrawer', () => ({ WorkDrawer: () => null }))
vi.mock('./WorkItem', () => ({ WorkItem: ({ work }: { work: { title: string } }) => <div data-testid="work">{work.title}</div> }))
vi.mock('./discoveryStore', () => ({ useSaved: () => ({ items: [], load: mocks.loadSaved }) }))
vi.mock('./useWorkActions', () => ({ useLibraryMatchRefresh: () => undefined }))

import { RecommendPage } from './RecommendPage'

const personal = (keys: string[]) => ({
  interests: ['x'], queries: [], sources: [], warnings: [], ai_used: false,
  results: keys.map((k) => ({ key: k, title: `论文 ${k}`, year: 2022, venue: 'V', in_library: null }))
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('为我推荐：筛选与再推荐', () => {
  it('点“再推荐一些”会带上已显示的论文，并把新结果接在后面', async () => {
    mocks.personal.mockResolvedValueOnce(personal(['a', 'b'])).mockResolvedValueOnce(personal(['c']))
    render(<RecommendPage />)
    fireEvent.click(screen.getByRole('button', { name: /根据我的文献库推荐/ }))
    await waitFor(() => expect(screen.getAllByTestId('work')).toHaveLength(2))
    expect(mocks.personal.mock.calls[0][1]).toMatchObject({ level: 'any', year_from: null, year_to: null, exclude: [] })
    fireEvent.click(screen.getByRole('button', { name: /再推荐一些/ }))
    await waitFor(() => expect(screen.getAllByTestId('work')).toHaveLength(3))
    expect(mocks.personal.mock.calls[1][1].exclude).toEqual(['a', 'b'])
  })
})
