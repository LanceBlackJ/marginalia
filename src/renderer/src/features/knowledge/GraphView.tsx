import { memo, useEffect, useMemo, useState } from 'react'
import { Background, Controls, Handle, Position, ReactFlow, ReactFlowProvider, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Button, EmptyState, Select, Skeleton, Tag } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { kbGraphApi, type GraphNode, type KbGraph } from '@/lib/api.export'
import { useKb } from './knowledgeStore'
import { KIND_OPTIONS, kindLabel } from './kbUtils'
import { DEFAULT_FILTER, EDGE_LABEL, TYPE_LABEL, edgeStyle, filterGraph, layoutGraph, type GraphFilter } from './graphLayout'
import { ItemDetail } from './ItemDetail'

type NodeData = { node: GraphNode; selected: boolean }

const GraphNodeView = memo(function GraphNodeView({ data }: NodeProps<Node<NodeData>>) {
  const { node, selected } = data
  return (
    <div className={`pd-kg-node pd-kg-node--${node.type}${selected ? ' is-selected' : ''}`} title={node.label}>
      <Handle type="target" position={Position.Left} className="pd-kg-handle" isConnectable={false} />
      <span className="pd-kg-node-kind">{node.type === 'item' ? kindLabel(node.kind as never) : TYPE_LABEL[node.type]}</span>
      <span className="pd-kg-node-label">{node.label}</span>
      <Handle type="source" position={Position.Right} className="pd-kg-handle" isConnectable={false} />
    </div>
  )
})
const nodeTypes = { kg: GraphNodeView }

/**
 * 关系图：节点 = 知识条目 / 主题 / 论文；边 = 已确认的学术关系（黑色实线）、AI 建议但未确认的关系（虚线并标“待确认”），
 * 以及条目归属主题、条目来自论文这两种浅灰结构线。点击条目节点在右侧看详情（含原文证据）。
 */
function GraphInner() {
  const { topics, version } = useKb()
  const [topicId, setTopicId] = useState('')
  const [kind, setKind] = useState('')
  const [filter, setFilter] = useState<GraphFilter>(DEFAULT_FILTER)
  const [graph, setGraph] = useState<KbGraph | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    setGraph(null)
    kbGraphApi
      .graph({ topic_id: topicId || undefined, kinds: kind ? [kind] : undefined })
      .then((g) => {
        if (alive) {
          setGraph(g)
          setError(null)
        }
      })
      .catch((err) => alive && setError(errorMessage(err)))
    return () => {
      alive = false
    }
  }, [topicId, kind, version])

  const { nodes, edges, view } = useMemo(() => {
    if (!graph) return { nodes: [] as Node<NodeData>[], edges: [] as Edge[], view: null }
    const f = filterGraph(graph, filter)
    const placed = layoutGraph(f.nodes, f.edges)
    const ns: Node<NodeData>[] = f.nodes.map((n) => {
      const p = placed.get(n.id)!
      return { id: n.id, type: 'kg', position: { x: p.x, y: p.y }, data: { node: n, selected: selected === n.id }, width: p.width, height: p.height, draggable: true }
    })
    const es: Edge[] = f.edges.map((e) => {
      const st = edgeStyle(e)
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        label: st.label || undefined,
        animated: false,
        style: {
          stroke: e.type === 'relation' ? 'var(--text)' : 'var(--line-mid)',
          strokeWidth: st.strong ? 1.8 : 1,
          strokeDasharray: st.dashed ? '6 4' : undefined
        },
        labelStyle: { fontSize: 11, fill: 'var(--text)' },
        labelBgStyle: { fill: 'var(--bg)' },
        interactionWidth: 12
      }
    })
    return { nodes: ns, edges: es, view: f }
  }, [graph, filter, selected])

  const toggleType = (t: GraphNode['type']): void => {
    const next = new Set(filter.types)
    if (next.has(t)) next.delete(t)
    else next.add(t)
    setFilter({ ...filter, types: next })
  }

  const selectedNode = graph?.nodes.find((n) => n.id === selected) ?? null

  return (
    <div className="pd-kg">
      <div className="pd-kg-bar">
        <Select
          size="sm"
          aria-label="按主题过滤"
          value={topicId}
          onChange={setTopicId}
          options={[{ value: '', label: '全部主题' }, ...topics.map((t) => ({ value: t.id, label: `${t.name} · ${t.item_count}` }))]}
          style={{ width: 170 }}
        />
        <Select
          size="sm"
          aria-label="按条目类型过滤"
          value={kind}
          onChange={setKind}
          options={[{ value: '', label: '全部类型' }, ...KIND_OPTIONS.map((o) => ({ value: o.value, label: o.label }))]}
          style={{ width: 130 }}
        />
        <span className="pd-kg-chips" role="group" aria-label="显示的节点">
          {(['item', 'topic', 'paper'] as const).map((t) => (
            <button key={t} type="button" className={`pd-kg-chip${filter.types.size === 0 || filter.types.has(t) ? ' is-on' : ''}`} onClick={() => toggleType(t)}>
              {TYPE_LABEL[t]}
            </button>
          ))}
        </span>
        <label className="pd-kg-check">
          <input type="checkbox" checked={filter.hideStructure} onChange={(e) => setFilter({ ...filter, hideStructure: e.target.checked })} />
          只看学术关系
        </label>
        <label className="pd-kg-check">
          <input type="checkbox" checked={filter.onlyConnected} onChange={(e) => setFilter({ ...filter, onlyConnected: e.target.checked })} />
          隐藏孤立节点
        </label>
        <span className="pd-spacer" />
        {graph && (
          <span className="pd-aux pd-muted">
            {graph.counts.items} 个条目 · {graph.counts.relations} 条关系{graph.counts.suggested > 0 ? `，${graph.counts.suggested} 条待确认` : ''}
          </span>
        )}
      </div>

      <div className="pd-kg-legend" aria-label="图例">
        <span><i className="pd-kg-line pd-kg-line--strong" /> {EDGE_LABEL.relation} · 已确认</span>
        <span><i className="pd-kg-line pd-kg-line--dashed" /> AI 建议，待确认</span>
        <span><i className="pd-kg-line pd-kg-line--thin" /> {EDGE_LABEL.member} / {EDGE_LABEL.source}</span>
      </div>

      <div className="pd-kg-body">
        <div className="pd-kg-canvas">
          {error ? (
            <p className="pd-status-text pd-status-text--danger" style={{ padding: 16 }}>{error}</p>
          ) : !graph ? (
            <div style={{ padding: 16 }}><Skeleton height={14} width="50%" /></div>
          ) : nodes.length === 0 ? (
            <EmptyState title="没有可显示的节点" description="先在“知识条目”里添加条目，或到“待确认候选”采纳候选；条目之间的关系在条目详情里建立。" />
          ) : (
            <ReactFlow
              nodes={nodes}
              edges={edges}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.15, maxZoom: 1.1 }}
              minZoom={0.15}
              maxZoom={2}
              nodesConnectable={false}
              proOptions={{ hideAttribution: true }}
              onNodeClick={(_e, n) => setSelected(n.id)}
              onPaneClick={() => setSelected(null)}
            >
              <Background gap={24} color="var(--border)" />
              <Controls showInteractive={false} />
            </ReactFlow>
          )}
        </div>
        <aside className="pd-kg-side">
          {!selectedNode ? (
            <p className="pd-dc-hint">点击一个节点查看详情。条目会显示内容、原文证据和关系；主题、论文显示基本信息。</p>
          ) : selectedNode.type === 'item' ? (
            <ItemDetail key={selectedNode.ref} itemId={selectedNode.ref} onDeleted={() => setSelected(null)} />
          ) : (
            <div>
              <Tag>{TYPE_LABEL[selectedNode.type]}</Tag>
              <h3 style={{ margin: '8px 0 4px' }}>{selectedNode.label}</h3>
              {selectedNode.year && <p className="pd-aux pd-muted">{selectedNode.year}</p>}
              <p className="pd-aux pd-muted" style={{ marginTop: 8 }}>
                {view?.edges.filter((e) => e.source === selectedNode.id || e.target === selectedNode.id).length ?? 0} 条连线
              </p>
              {selectedNode.type === 'paper' && (
                <Button size="sm" style={{ marginTop: 10 }} onClick={() => (window.location.hash = `#/reader/${selectedNode.ref}`)}>
                  打开阅读
                </Button>
              )}
            </div>
          )}
        </aside>
      </div>
    </div>
  )
}

export function GraphView() {
  return (
    <ReactFlowProvider>
      <GraphInner />
    </ReactFlowProvider>
  )
}
