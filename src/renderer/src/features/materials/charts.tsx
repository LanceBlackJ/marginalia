import type { HistogramBin } from '@/lib/types.materials'

/** 紧凑数字：整数带千分位，小数保留 4 位有效数字。 */
export function fmtNum(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'string') return v
  if (!Number.isFinite(v)) return String(v)
  if (Number.isInteger(v)) return v.toLocaleString('zh-CN')
  const a = Math.abs(v)
  if (a !== 0 && (a < 0.001 || a >= 1e7)) return v.toExponential(2)
  return Number(v.toPrecision(4)).toString()
}

/** 数值直方图：纯黑柱、发丝线基线，两端标注取值范围；悬停显示区间与计数。 */
export function Histogram({ bins, width = 220, height = 56 }: { bins: HistogramBin[]; width?: number; height?: number }) {
  if (bins.length === 0) return null
  const max = Math.max(...bins.map((b) => b.count), 1)
  const gap = 1
  const bw = Math.max(2, (width - gap * (bins.length - 1)) / bins.length)
  const labelH = 14
  const plotH = height - labelH
  return (
    <svg className="pd-hist" width={width} height={height} role="img" aria-label={`数值分布，范围 ${fmtNum(bins[0].start)} 到 ${fmtNum(bins[bins.length - 1].end)}`}>
      {bins.map((b, i) => {
        const h = b.count === 0 ? 0 : Math.max(1, Math.round((b.count / max) * plotH))
        return (
          <rect key={i} x={i * (bw + gap)} y={plotH - h} width={bw} height={h}>
            <title>{`${fmtNum(b.start)} – ${fmtNum(b.end)}：${b.count.toLocaleString('zh-CN')}`}</title>
          </rect>
        )
      })}
      <line x1={0} x2={width} y1={plotH + 0.5} y2={plotH + 0.5} stroke="var(--border-strong)" strokeWidth={1} />
      <text x={0} y={height - 2} textAnchor="start">
        {fmtNum(bins[0].start)}
      </text>
      <text x={width} y={height - 2} textAnchor="end">
        {fmtNum(bins[bins.length - 1].end)}
      </text>
    </svg>
  )
}

/** 类别取值的横向条形（占非空值的比例）。 */
export function TopBars({ values, total }: { values: Array<{ value: string; count: number }>; total: number }) {
  const max = Math.max(...values.map((v) => v.count), 1)
  return (
    <div className="pd-bars">
      {values.map((v, i) => (
        <div key={i} className="pd-bar" title={`${v.value}：${v.count.toLocaleString('zh-CN')}${total ? ` · ${((v.count / total) * 100).toFixed(1)}%` : ''}`}>
          <span className="pd-bar-label">{v.value === '' ? '空字符串' : v.value}</span>
          <span className="pd-bar-track">
            <i style={{ width: `${(v.count / max) * 100}%` }} />
          </span>
          <span className="pd-bar-n">{v.count.toLocaleString('zh-CN')}</span>
        </div>
      ))}
    </div>
  )
}
