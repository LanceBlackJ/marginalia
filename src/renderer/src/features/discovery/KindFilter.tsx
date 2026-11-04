import { useMemo, useState } from 'react'
import type { Work } from '@/lib/types.discovery'

/** 类型的显示顺序：先读的综述排最前。 */
export const KIND_ORDER = ['review', 'research', 'dataset', 'perspective', 'tutorial', 'thesis', 'book'] as const

export interface KindCount {
  kind: string
  label: string
  count: number
}

/** 这一批论文里各类型有多少篇（只列出现过的类型，按固定顺序）。 */
export function countKinds(works: Pick<Work, 'kind' | 'kind_label'>[]): KindCount[] {
  const by = new Map<string, KindCount>()
  for (const w of works) {
    const c = by.get(w.kind) ?? { kind: w.kind, label: w.kind_label, count: 0 }
    c.count += 1
    by.set(w.kind, c)
  }
  const rank = (k: string): number => {
    const i = (KIND_ORDER as readonly string[]).indexOf(k)
    return i < 0 ? 99 : i
  }
  return [...by.values()].sort((a, b) => rank(a.kind) - rank(b.kind))
}

export function filterByKind<T extends Pick<Work, 'kind'>>(works: T[], kind: string | null): T[] {
  return kind ? works.filter((w) => w.kind === kind) : works
}

/** 论文类型筛选：一行小标签，点一下只看这一类，再点一下取消。类型没有两种以上时不显示。 */
export function KindFilter({ works, value, onChange }: { works: Pick<Work, 'kind' | 'kind_label'>[]; value: string | null; onChange: (k: string | null) => void }) {
  const kinds = useMemo(() => countKinds(works), [works])
  if (kinds.length < 2) return null
  return (
    <div className="pd-dc-kinds" role="group" aria-label="按论文类型筛选">
      <span className="pd-dc-kinds-label">类型</span>
      <button type="button" className="pd-dc-kind" data-active={value === null} onClick={() => onChange(null)}>
        全部 {works.length}
      </button>
      {kinds.map((k) => (
        <button key={k.kind} type="button" className="pd-dc-kind" data-active={value === k.kind} onClick={() => onChange(value === k.kind ? null : k.kind)}>
          {k.label} {k.count}
        </button>
      ))}
    </div>
  )
}

/** 页面里用：保存当前选中的类型；选中的类型已经不在这批论文里（换了一次搜索）时自动回到“全部”。 */
export function useKindFilter(works: Pick<Work, 'kind' | 'kind_label'>[]): { kind: string | null; setKind: (k: string | null) => void } {
  const [kind, setKind] = useState<string | null>(null)
  const present = kind !== null && works.some((w) => w.kind === kind)
  return { kind: present ? kind : null, setKind }
}
