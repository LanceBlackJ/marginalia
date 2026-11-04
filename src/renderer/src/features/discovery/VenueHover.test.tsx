// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { VenueHover } from './VenueHover'

const venueInfo = vi.fn()
vi.mock('@/lib/api.discovery', () => ({ discoveryApi: { venueInfo: (...a: unknown[]) => venueInfo(...a) } }))

describe('期刊名悬停介绍', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    cleanup()
    vi.useRealTimers()
    venueInfo.mockReset()
  })

  it('鼠标停一会儿才查并显示介绍；移开后收起；只有统计数字时明说不是影响因子', async () => {
    venueInfo.mockResolvedValue({
      name: 'Nature Communications', listed: null, note: null,
      stats: { name: 'Nature Communications', type: 'journal', publisher: 'Springer Nature', country: 'GB', works_count: 54000, cited_by_count: 2500000, mean_citedness_2y: 17.2, h_index: 480, is_oa: true, in_doaj: true, homepage: 'https://www.nature.com/ncomms/', issn: null, openalex_id: null },
      summary: '《Nature Communications》是由 Springer Nature 出版的期刊，h 指数 480。'
    })
    render(
      <MemoryRouter>
        <VenueHover name="Nature Communications" type="journal">
          Nature Communications
        </VenueHover>
      </MemoryRouter>
    )
    const trigger = screen.getByText('Nature Communications')
    fireEvent.mouseEnter(trigger)
    expect(venueInfo).not.toHaveBeenCalled() // 一扫而过不触发
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
    })
    expect(venueInfo).toHaveBeenCalledTimes(1)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50)
    })
    expect(screen.getByText(/h 指数 480/)).toBeTruthy()
    expect(screen.getByText(/不是官方影响因子/)).toBeTruthy()
    expect(screen.getByText(/不在收录的顶刊顶会名单里/)).toBeTruthy()
    fireEvent.mouseLeave(trigger)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300)
    })
    expect(screen.queryByRole('tooltip')).toBeNull()
  })
})
