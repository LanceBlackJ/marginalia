import { memo, useEffect, useMemo } from 'react'
import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from '@xyflow/react'
import { Sparkles } from 'lucide-react'
import { Button, EmptyState, Segmented, Select, Spinner } from '@/components/ui'
import type { Flow, FlowAnnotation, FlowNode, FlowRoot } from '@/lib/types.code'
import { CERTAINTY_LABEL, FLOW_NODE_H, FLOW_NODE_W, ROLE_LABEL, layoutFlow, lineRange } from './codeUtils'
import { useCodeStore } from './codeStore'
import '@xyflow/react/dist/style.css'

interface NodeData extends Record<string, unknown> {
  n: FlowNode
  ann?: FlowAnnotation
  selected: boolean
}

const FlowCard = memo(function FlowCard({ data }: NodeProps<Node<NodeData>>) {
  const { n, ann, selected } = data
  return (
    <div className="pd-fnode" data-selected={selected || undefined} data-uncertain={n.uncertain || undefined} style={{ width: FLOW_NODE_W, height: FLOW_NODE_H }}>
      <Handle type="target" position={Position.Left} className="pd-fhandle" isConnectable={false} />
      <div className="pd-fnode-top">
        <span className="pd-fnode-order">{n.order}</span>
        <span className="pd-fnode-title pd-ellipsis" title={ann?.title ?? n.label}>
          {ann?.title ?? n.label}
        </span>
        {ann?.ai && <span className="pd-fnode-ai">AI</span>}
      </div>
      <div className="pd-fnode-sub pd-ellipsis" title={`${n.path}:${n.line_start}-${n.line_end}`}>
        {n.phase ? `${n.phase} · ` : ''}
        {ann ? n.label : n.path.split('/').pop()}
      </div>
      <Handle type="source" position={Position.Right} className="pd-fhandle" isConnectable={false} />
    </div>
  )
})

const nodeTypes = { flow: FlowCard }

function build(flow: Flow, selected: number | null): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const lay = layoutFlow(flow.nodes, flow.edges)
  const nodes: Node<NodeData>[] = flow.nodes.map((n) => ({
    id: String(n.id),
    type: 'flow',
    position: lay.positions.get(n.id) ?? { x: 0, y: 0 },
    data: { n, ann: flow.annotations[String(n.id)], selected: n.id === selected },
    draggable: false,
    selectable: true,
    width: FLOW_NODE_W,
    height: FLOW_NODE_H
  }))
  const seen = new Set<string>()
  const edges: Edge[] = []
  for (const e of flow.edges) {
    const key = `${e.source}>${e.target}`
    if (seen.has(key) || e.source === e.target) continue
    seen.add(key)
    const dashed = e.certainty === 'name' || e.certainty === 'ambiguous'
    edges.push({
      id: key,
      source: String(e.source),
      target: String(e.target),
      type: 'smoothstep',
      style: { stroke: 'var(--text)', strokeWidth: 1, strokeDasharray: dashed ? '5 4' : undefined },
      markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--text)', width: 14, height: 14 },
      data: { certainty: e.certainty }
    })
  }
  return { nodes, edges }
}

/** 起点下拉里的一项：文件路径，括号里写角色和是否在拷贝来的第三方目录里（入口都是 __main__，不重复写）。 */
function rootLabel(r: FlowRoot): string {
  const tags = [r.role ? ROLE_LABEL[r.role] : '', r.vendored ? '第三方拷贝' : ''].filter(Boolean)
  return tags.length ? `${r.path}（${tags.join('，')}）` : r.path
}

/** 说明为什么被截断、怎么看到更多：深度不够 → 调大深度；节点数到上限 → 换起点。 */
function truncationHint(flow: Flow): string {
  const by = flow.truncated_by ?? []
  const parts: string[] = []
  if (by.includes('depth')) parts.push(`还有更深的调用没有展开，当前深度 ${flow.max_depth}，可调大上方的“深度”`)
  if (by.includes('nodes')) parts.push(`已达 ${flow.max_nodes ?? 60} 个节点的显示上限，其余调用被省略，可选择别的起点查看`)
  return parts.length ? parts.join('；') : '部分调用没有显示'
}

export function FlowView() {
  const flow = useCodeStore((s) => s.flow)
  const loading = useCodeStore((s) => s.flowLoading)
  const error = useCodeStore((s) => s.flowError)
  const depth = useCodeStore((s) => s.flowDepth)
  const selectedId = useCodeStore((s) => s.flowSelected)
  const annotating = useCodeStore((s) => s.annotating)
  const loadFlow = useCodeStore((s) => s.loadFlow)
  const selectFlowNode = useCodeStore((s) => s.selectFlowNode)
  const annotateFlow = useCodeStore((s) => s.annotateFlow)
  const goToSymbolId = useCodeStore((s) => s.goToSymbolId)
  const explain = useCodeStore((s) => s.explain)

  useEffect(() => {
    if (!flow && !loading && !error) void loadFlow()
  }, [flow, loading, error, loadFlow])

  const graph = useMemo(() => (flow ? build(flow, selectedId) : null), [flow, selectedId])
  // 初始视野只框住起点附近的两层，保证文字可读；其余的靠平移/缩放查看
  const startNodes = useMemo(() => (flow ? flow.nodes.filter((n) => n.depth <= 2).slice(0, 12).map((n) => ({ id: String(n.id) })) : []), [flow])
  const selected = flow?.nodes.find((n) => n.id === selectedId) ?? null
  const ann = selected && flow ? flow.annotations[String(selected.id)] : undefined

  if (error) {
    return (
      <div className="pd-flow-empty">
        <EmptyState tone="danger" title="无法生成运行流程" description={error} action={<Button onClick={() => void loadFlow()}>重试</Button>} />
      </div>
    )
  }
  if (!flow || !graph) {
    return (
      <div className="pd-flow-empty">
        <EmptyState icon={<Spinner size={20} />} title="正在整理运行流程…" />
      </div>
    )
  }
  if (flow.nodes.length === 0) {
    return (
      <div className="pd-flow-empty">
        <EmptyState title="没有找到可以展示的流程" description={flow.note || '仓库里没有识别到入口函数。可以在代码里选一个函数，用“以它为起点看运行流程”。'} />
      </div>
    )
  }

  const hasAnnotations = Object.keys(flow.annotations).length > 0
  // 起点下拉：文件路径 +（角色 / 第三方拷贝）；作者自己的排前面，拷贝来的第三方入口排后面
  const rootOptions = [...flow.roots]
    .sort((a, b) => Number(!!a.vendored) - Number(!!b.vendored))
    .map((r) => ({ value: String(r.symbol_id), label: rootLabel(r) }))
  if (!rootOptions.some((o) => o.value === String(flow.root))) {
    const rn = flow.nodes.find((n) => n.id === flow.root)
    rootOptions.unshift({ value: String(flow.root), label: rn ? `${rn.path} · ${rn.label}` : `符号 ${flow.root}` })
  }

  return (
    <div className="pd-flow">
      <div className="pd-flow-bar">
        <div className="pd-flow-ctl">
          <span className="pd-aux pd-muted">起点</span>
          <Select aria-label="流程起点" size="sm" value={String(flow.root)} options={rootOptions} onChange={(v) => void loadFlow(Number(v))} style={{ maxWidth: 320 }} />
        </div>
        <div className="pd-flow-ctl">
          <span className="pd-aux pd-muted">深度</span>
          <Segmented
            aria-label="展开深度"
            value={String(depth)}
            onChange={(v) => void loadFlow(flow.root, Number(v))}
            options={['2', '3', '4', '6'].map((d) => ({ value: d, label: d }))}
          />
        </div>
        <span className="pd-aux pd-muted">
          {flow.nodes.length} 个节点 · {flow.edges.length} 条调用{flow.truncated ? ` · ${truncationHint(flow)}` : ''}
        </span>
        <div className="pd-spacer" />
        <Button size="sm" icon={<Sparkles size={14} />} loading={annotating} onClick={() => void annotateFlow()}>
          {hasAnnotations ? '重新让 AI 命名节点' : 'AI 命名节点'}
        </Button>
      </div>
      <div className="pd-flow-legend pd-aux pd-muted">
        <svg width="34" height="8" aria-hidden>
          <line x1="0" y1="4" x2="34" y2="4" stroke="currentColor" />
        </svg>
        确定的调用
        <svg width="34" height="8" aria-hidden>
          <line x1="0" y1="4" x2="34" y2="4" stroke="currentColor" strokeDasharray="5 4" />
        </svg>
        不确定：{CERTAINTY_LABEL.name}或{CERTAINTY_LABEL.ambiguous}
        <span className="pd-flow-note">{flow.note}</span>
      </div>

      <div className="pd-flow-canvas">
        <ReactFlow
          key={`${flow.root}-${depth}-${flow.nodes.length}`}
          nodes={graph.nodes}
          edges={graph.edges}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.1, maxZoom: 1, minZoom: 0.62, nodes: startNodes }}
          minZoom={0.15}
          maxZoom={1.6}
          nodesDraggable={false}
          nodesConnectable={false}
          onNodeClick={(_e, node) => selectFlowNode(Number(node.id))}
          onPaneClick={() => selectFlowNode(null)}
          proOptions={{ hideAttribution: true }}
        >
          <Background gap={24} size={1} color="var(--border)" />
          <Controls showInteractive={false} position="bottom-left" />
        </ReactFlow>
      </div>

      <div className="pd-flow-detail">
        {selected ? (
          <>
            <div className="pd-flow-detail-main">
              <div className="pd-flow-detail-title">
                <span className="pd-fnode-order">{selected.order}</span>
                {ann?.title ?? selected.label}
                {ann?.ai && <span className="pd-fnode-ai" title={`由 ${ann.model} 命名，流程结构本身来自静态分析`}>AI</span>}
                {selected.phase && <span className="pd-aux pd-muted">{selected.phase}</span>}
                {selected.uncertain && <span className="pd-aux pd-muted">指向不确定</span>}
              </div>
              <div className="pd-aux pd-muted pd-ellipsis">
                {selected.label} · {selected.path}:{lineRange(selected.line_start, selected.line_end)}
              </div>
              {ann?.summary && <p className="pd-flow-summary">{ann.summary}</p>}
              {!ann?.summary && selected.doc && <p className="pd-flow-summary pd-muted">{selected.doc}</p>}
            </div>
            <div className="pd-flow-detail-actions">
              <Button size="sm" onClick={() => void goToSymbolId(selected.id)}>
                查看代码
              </Button>
              <Button size="sm" icon={<Sparkles size={14} />} onClick={() => void explain({ kind: 'function', path: selected.path, symbol_id: selected.id, symbol: selected.label }, selected.label)}>
                AI 解释
              </Button>
              {selected.id !== flow.root && (
                <Button size="sm" variant="ghost" onClick={() => void loadFlow(selected.id)}>
                  以它为起点
                </Button>
              )}
            </div>
          </>
        ) : (
          <p className="pd-aux pd-muted">点击流程图里的节点查看详情。流程来自静态调用图，按代码里调用出现的先后排列。</p>
        )}
      </div>
    </div>
  )
}
