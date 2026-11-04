import { useMemo } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { type MindNode, type MindSearchHit, type MindView } from '@/lib/types.mindmap'
import { useMindmapStore } from './mindmapStore'
import { buildIndex, filterByKind, kindCounts, outlineRows } from './tree'

/** 左侧节点目录：搜索结果 / 按类型过滤的列表 / 完整的可折叠大纲。点击任一项，画布定位到该节点。 */
export function NodeDirectory({ nodes, hits: allHits, query }: { nodes: MindNode[]; hits: MindSearchHit[]; query: string }) {
  // 思维导图只有“逻辑框架”一个视图：旧版本生成过的方法、实验视角的节点不再出现在搜索结果里
  const hits = allHits.filter((h) => h.view === 'logic')
  const collapsed = useMindmapStore((s) => s.collapsed[s.view])
  const kindFilter = useMindmapStore((s) => s.kindFilter)
  const selectedId = useMindmapStore((s) => s.selectedId)
  const kinds = useMemo(() => kindCounts(nodes), [nodes])
  const index = useMemo(() => buildIndex(nodes), [nodes])
  const filtered = useMemo(() => (kindFilter.size > 0 ? filterByKind(nodes, kindFilter) : null), [nodes, kindFilter])
  const rows = useMemo(() => outlineRows(nodes, collapsed ?? new Set()), [nodes, collapsed])
  const pick = (id: string): void => void useMindmapStore.getState().select(id, { focus: true })

  const pathOf = (n: MindNode): string => {
    const out: string[] = []
    let cur = n.parent_id ? index.byId.get(n.parent_id) : undefined
    while (cur && cur.parent_id) {
      out.unshift(cur.title)
      cur = index.byId.get(cur.parent_id)
    }
    return out.join(' › ')
  }

  const pickHit = async (h: MindSearchHit): Promise<void> => {
    const st = useMindmapStore.getState()
    if (h.view !== st.view) await st.setView(h.view as MindView)
    await useMindmapStore.getState().select(h.id, { focus: true })
  }

  return (
    <nav className="pd-mmd" aria-label="节点目录">
      {query.trim() ? (
        <>
          <div className="pd-mmd-h">搜索结果 · {hits.length}</div>
          <div className="pd-mmd-list">
            {hits.length === 0 && <div className="pd-mmd-empty">没有匹配的节点</div>}
            {hits.map((h) => (
              <button key={h.id} type="button" className="pd-mmd-hit" data-selected={h.id === selectedId} onClick={() => void pickHit(h)}>
                <span className="pd-mmd-hit-t pd-ellipsis">{h.title}</span>
                <span className="pd-mmd-hit-s pd-ellipsis">
                  {h.match === 'summary' ? h.summary : h.path.length > 0 ? h.path.join(' › ') : h.kind_label}
                </span>
              </button>
            ))}
          </div>
        </>
      ) : (
        <>
          <div className="pd-mmd-h">
            类型
            {kindFilter.size > 0 && (
              <button type="button" className="pd-mmd-clear" onClick={() => useMindmapStore.getState().clearKinds()}>
                清除过滤
              </button>
            )}
          </div>
          <div className="pd-mmd-kinds" role="group" aria-label="按类型过滤">
            {kinds.map((k) => (
              <button key={k.kind} type="button" className="pd-mmd-kind" aria-pressed={kindFilter.has(k.kind)} onClick={() => useMindmapStore.getState().toggleKind(k.kind)}>
                {k.label} <span>{k.count}</span>
              </button>
            ))}
          </div>
          <div className="pd-mmd-h">{filtered ? `节点 · ${filtered.length}` : '大纲'}</div>
          <div className="pd-mmd-list" role={filtered ? 'list' : 'tree'}>
            {filtered
              ? filtered.map((n) => (
                  <button key={n.id} type="button" className="pd-mmd-hit" data-selected={n.id === selectedId} onClick={() => pick(n.id)}>
                    <span className="pd-mmd-hit-t pd-ellipsis">{n.title}</span>
                    <span className="pd-mmd-hit-s pd-ellipsis">{pathOf(n) || '根'}</span>
                  </button>
                ))
              : rows.map(({ node: n, level, open, expandable }) => (
                  <div key={n.id} className="pd-mmd-row" data-selected={n.id === selectedId} style={{ paddingLeft: 6 + level * 14 }}>
                    <button type="button" className="pd-mmp-chev" aria-label={open ? '折叠' : '展开'} disabled={!expandable} onClick={() => useMindmapStore.getState().toggleCollapse(n.id)}>
                      {expandable ? open ? <ChevronDown size={13} /> : <ChevronRight size={13} /> : null}
                    </button>
                    <button type="button" className="pd-mmd-name pd-ellipsis" data-depth={Math.min(n.depth, 3)} onClick={() => pick(n.id)} title={n.title}>
                      {n.title}
                    </button>
                  </div>
                ))}
          </div>
        </>
      )}
    </nav>
  )
}
