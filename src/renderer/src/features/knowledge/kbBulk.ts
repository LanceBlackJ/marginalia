/** 知识条目多选：纯函数，便于测试。 */

export function toggleSelected(sel: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(sel)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  return next
}

/** Shift 点击：从上一次点击的位置到当前位置之间的条目全部选中（按当前列表顺序）。 */
export function selectRange(order: readonly string[], sel: ReadonlySet<string>, anchor: string | null, id: string): Set<string> {
  const to = order.indexOf(id)
  const from = anchor ? order.indexOf(anchor) : -1
  if (to < 0) return new Set(sel)
  if (from < 0) return toggleSelected(sel, id)
  const [a, b] = from < to ? [from, to] : [to, from]
  const next = new Set(sel)
  for (const x of order.slice(a, b + 1)) next.add(x)
  return next
}

/** 列表刷新后，去掉已经不在列表里的选中项。 */
export function pruneSelected(sel: ReadonlySet<string>, order: readonly string[]): Set<string> {
  const live = new Set(order)
  const next = new Set<string>()
  for (const id of sel) if (live.has(id)) next.add(id)
  return next.size === sel.size ? (sel as Set<string>) : next
}

export function allSelected(sel: ReadonlySet<string>, order: readonly string[]): boolean {
  return order.length > 0 && order.every((id) => sel.has(id))
}

/** 批量操作后的提示文字。 */
export function bulkSummary(action: 'add_topic' | 'remove_topic' | 'delete', r: { affected: number; skipped: number }): string {
  const verb = action === 'add_topic' ? '加入主题' : action === 'remove_topic' ? '移出主题' : '删除'
  return `${r.affected} 个条目已${verb}` + (r.skipped > 0 ? `，${r.skipped} 个已不存在而跳过` : '')
}
