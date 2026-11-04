// @vitest-environment jsdom
import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { ActivityDay, ActivityStats } from '@/lib/types'
import { ActivityCalendar, layoutWeeks, levelOf } from './ActivityCalendar'

afterEach(cleanup)

const day = (date: string, count = 0, extra: Partial<ActivityDay> = {}): ActivityDay => ({
  date, count, imports: 0, opened: 0, notes: 0, annotations: 0, bookmarks: 0, ai: 0, ...extra
})

describe('日历方框图', () => {
  it('颜色深浅：没记录一定是空方框，其余按最忙的那天分 1-4 档', () => {
    expect(levelOf(0, 10)).toBe(0)
    expect(levelOf(1, 10)).toBe(1)
    expect(levelOf(10, 10)).toBe(4)
    expect(levelOf(5, 0)).toBe(0)
  })

  it('按周排列：2026-11-01 是周日，前面空 6 格，一列 7 天', () => {
    const weeks = layoutWeeks([day('2026-11-01'), day('2026-11-02'), day('2026-11-03')])
    expect(weeks[0].slice(0, 6).every((c) => c === null)).toBe(true)
    expect(weeks[0][6]?.date).toBe('2026-11-01')
    expect(weeks[1][0]?.date).toBe('2026-11-02')
  })

  it('每个方框的提示写清当天做了什么，没有记录就说没有记录', () => {
    const days = [day('2026-10-30'), day('2026-10-31', 3, { notes: 2, imports: 1 })]
    const activity: ActivityStats = { days, active_days: 1, total: 3, streak: 0, longest: 1 }
    const { container } = render(<ActivityCalendar activity={activity} />)
    expect(container.querySelectorAll('rect.pd-cal-cell')).toHaveLength(2)
    expect(screen.getByText('2026-10-31：导入 1，笔记 2')).toBeTruthy()
    expect(screen.getByText('2026-10-30：没有记录')).toBeTruthy()
  })
})
