import type { ActivityDay, ActivityStats } from '@/lib/types'

const CELL = 12
const GAP = 3
const LEFT = 26
const TOP = 18
const WEEKDAYS = ['一', '', '三', '', '五', '', '日']

/** 数量 → 深浅 0..4。按这一年里最忙的那天来分档；0 永远是空方框。 */
export function levelOf(count: number, max: number): number {
  if (count <= 0 || max <= 0) return 0
  return Math.min(4, Math.max(1, Math.ceil((count / max) * 4)))
}

/** 把每天排进“周 × 星期”的格子：第一列从周一开始，前面不足的格子留空。 */
export function layoutWeeks(days: ActivityDay[]): (ActivityDay | null)[][] {
  if (days.length === 0) return []
  const first = new Date(`${days[0].date}T00:00:00`)
  const lead = (first.getDay() + 6) % 7 // 周一 = 0
  const cells: (ActivityDay | null)[] = [...Array<null>(lead).fill(null), ...days]
  const weeks: (ActivityDay | null)[][] = []
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7))
  return weeks
}

export function dayTitle(d: ActivityDay): string {
  if (d.count === 0) return `${d.date}：没有记录`
  const parts = [
    ['导入', d.imports],
    ['打开阅读', d.opened],
    ['笔记', d.notes],
    ['标注', d.annotations],
    ['书签', d.bookmarks],
    ['AI 问答', d.ai]
  ] as const
  return `${d.date}：${parts.filter(([, n]) => n > 0).map(([k, n]) => `${k} ${n}`).join('，')}`
}

/** Codeforces / GitHub 那种日历方框图：一格一天，最近一年，颜色越深那天做的事越多。 */
export function ActivityCalendar({ activity }: { activity: ActivityStats }) {
  const weeks = layoutWeeks(activity.days)
  const max = Math.max(0, ...activity.days.map((d) => d.count))
  const width = LEFT + weeks.length * (CELL + GAP)
  const height = TOP + 7 * (CELL + GAP)
  let lastMonth = -1
  const months: { x: number; label: string }[] = []
  weeks.forEach((w, i) => {
    const d = w.find((c) => c !== null)
    if (!d) return
    const m = Number(d.date.slice(5, 7))
    if (m !== lastMonth && (i === 0 || w[0] !== null)) {
      const x = LEFT + i * (CELL + GAP)
      const prev = months[months.length - 1]
      if (prev && x - prev.x < 3 * (CELL + GAP)) months.pop() // 两个月份标签挨得太近（开头那一小截）：留后一个
      months.push({ x, label: `${m} 月` })
      lastMonth = m
    }
  })
  return (
    <div className="pd-cal">
      <div className="pd-cal-scroll">
        <svg width={width} height={height} role="img" aria-label="最近一年每天的活动方框图">
          {months.map((m) => (
            <text key={m.x} x={m.x} y={11} className="pd-cal-label">
              {m.label}
            </text>
          ))}
          {WEEKDAYS.map((w, i) => (
            <text key={i} x={0} y={TOP + i * (CELL + GAP) + CELL - 2} className="pd-cal-label">
              {w}
            </text>
          ))}
          {weeks.map((w, wi) =>
            w.map((d, di) =>
              d ? (
                <rect
                  key={d.date}
                  x={LEFT + wi * (CELL + GAP)}
                  y={TOP + di * (CELL + GAP)}
                  width={CELL}
                  height={CELL}
                  rx={2}
                  className={`pd-cal-cell pd-cal-l${levelOf(d.count, max)}`}
                >
                  <title>{dayTitle(d)}</title>
                </rect>
              ) : null
            )
          )}
        </svg>
      </div>
      <div className="pd-cal-foot">
        <span>
          最近一年 <b>{activity.active_days}</b> 天有记录 · 共 <b>{activity.total}</b> 次 · 当前连续 <b>{activity.streak}</b> 天 · 最长连续 <b>{activity.longest}</b> 天
        </span>
        <span className="pd-cal-legend" aria-hidden="true">
          少
          {[0, 1, 2, 3, 4].map((l) => (
            <i key={l} className={`pd-cal-cell pd-cal-l${l}`} />
          ))}
          多
        </span>
      </div>
      <p className="pd-aux pd-muted">
        每一格是一天，数的是：导入论文、打开阅读、笔记、标注、书签、保存的 AI 问答。“打开阅读”每篇论文只记得最近一次打开的那天，所以更早的阅读天数会偏少。
      </p>
    </div>
  )
}
