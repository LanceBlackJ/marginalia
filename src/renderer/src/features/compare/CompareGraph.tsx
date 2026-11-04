import { useMemo } from 'react'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { CompareResult } from '@/lib/types.discovery'
import { groupEdges, layoutGraph, NODE_W as W, NODE_H as H } from './graphLayout'

interface PaperNodeData extends Record<string, unknown> {
  id: string
  title: string
  year: number | null
}

function PaperNode({ data, selected }: NodeProps<Node<PaperNodeData>>) {
  return (
    <div className={`pd-cmp-node${selected ? ' is-selected' : ''}`} style={{ width: W, minHeight: H }}>
      <Handle type="target" position={Position.Left} isConnectable={false} />
      <span className="pd-cmp-node-label">{data.id}</span>
      <span className="pd-cmp-node-title">{data.title}</span>
      {data.year && <span className="pd-cmp-node-year">{data.year}</span>}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
}

const nodeTypes = { paper: PaperNode }

export function CompareGraph({ graph, selectedEdge, onSelect }: { graph: CompareResult['graph']; selectedEdge: string | null; onSelect: (id: string | null) => void }) {
  const { nodes, edges } = useMemo(() => {
    const groups = groupEdges(graph.edges)
    const pos = layoutGraph(graph.nodes, groups)
    const nodes: Node<PaperNodeData>[] = graph.nodes.map((n) => ({
      id: n.id,
      type: 'paper',
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { id: n.id, title: n.title, year: n.year }
    }))
    const edges: Edge[] = groups.map((g) => {
      const allAi = g.edges.every((e) => e.ai_inferred)
      const label = g.edges.map((e) => `${e.relation_label}${e.ai_inferred ? ' · AI 推断' : ''}`).join(' / ')
      return {
        id: g.id,
        source: g.from,
        target: g.to,
        label,
        type: 'default',
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16, color: 'var(--text)' },
        style: { stroke: 'var(--text)', strokeWidth: selectedEdge === g.id ? 2.4 : 1.4, strokeDasharray: allAi ? '6 4' : undefined }
      }
    })
    return { nodes, edges }
  }, [graph, selectedEdge])

  return (
    <div className="pd-cmp-graph" aria-label="论文关系图">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.18 }}
        minZoom={0.4}
        maxZoom={1.6}
        nodesConnectable={false}
        elementsSelectable
        onEdgeClick={(_e, edge) => onSelect(edge.id)}
        onPaneClick={() => onSelect(null)}
        proOptions={{ hideAttribution: false }}
      >
        <Background gap={24} color="var(--border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  )
}
