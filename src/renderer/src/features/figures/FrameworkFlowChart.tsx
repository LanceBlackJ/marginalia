import { useEffect, useMemo, useState, type ReactElement } from 'react'
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Edge,
  type Node,
  type NodeProps
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Maximize2 } from 'lucide-react'
import { Button, Dialog, Tag } from '@/components/ui'
import { ZoomImage } from '@/components/ZoomImage'
import { textApi } from '@/lib/api.text'
import type { FigureAnalysis, FigureGroup, FrameworkData, FrameworkModule } from '@/lib/types.vision'
import { useReaderStore } from '@/features/reader/readerStore'
import { layoutFramework } from './frameworkLayout'
import { useBlobUrl } from './useBlobUrl'

interface ModuleNodeData extends Record<string, unknown> {
  label: string
  uncertain: boolean
  selected: boolean
  kind: FrameworkModule['kind']
}

const KIND_LABEL: Record<FrameworkModule['kind'], string> = { input: '输入', process: '处理', model: '模型', output: '输出', data: '数据', other: '其他' }

function ModuleNode({ data }: NodeProps<Node<ModuleNodeData>>): ReactElement {
  return (
    <div className="pd-ff-node" data-uncertain={data.uncertain} data-selected={data.selected} title={data.uncertain ? '这个名字没有在图的文字层里找到，可能读错了' : undefined}>
      <Handle type="target" position={Position.Left} className="pd-ff-handle" isConnectable={false} />
      <span className="pd-ff-label">{data.label}</span>
      {data.uncertain && <span className="pd-ff-q" aria-label="不确定">?</span>}
      <Handle type="source" position={Position.Right} className="pd-ff-handle" isConnectable={false} />
    </div>
  )
}

function GroupNode({ data }: NodeProps<Node<{ label: string }>>): ReactElement {
  return <div className="pd-ff-group"><span className="pd-ff-group-title">{data.label}</span></div>
}

const nodeTypes = { fwModule: ModuleNode, fwGroup: GroupNode }

/**
 * 把框架图的结构化结果画成流程图：分组是外框，模块是细黑边方框，连线带箭头，可缩放平移、拖动节点。
 * 虚线 = 不确定（相连模块的名字没有在图的文字层里找到）；点线 = 控制流。位置由程序自动排版，不代表原图里的位置。
 */
export function FrameworkFlowChart({
  data, selectedId, onSelect, height = 340
}: { data: FrameworkData; selectedId: string | null; onSelect: (id: string | null) => void; height?: number | string }): ReactElement {
  const layout = useMemo(() => layoutFramework(data), [data])
  const byId = useMemo(() => new Map(data.modules.map((m) => [m.id, m])), [data])

  const initialNodes = useMemo<Node[]>(() => {
    const groups: Node[] = layout.groups.map((g) => ({
      id: `grp:${g.id}`, type: 'fwGroup', position: { x: g.x, y: g.y }, data: { label: g.label }, style: { width: g.w, height: g.h },
      draggable: false, selectable: false, zIndex: 0
    }))
    const mods: Node[] = layout.modules.map((m) => {
      const src = byId.get(m.id)
      return {
        id: m.id, type: 'fwModule', position: { x: m.x, y: m.y }, parentId: m.group ? `grp:${m.group}` : undefined,
        data: { label: m.label, uncertain: src?.in_text === false, selected: false, kind: src?.kind ?? 'other' },
        style: { width: m.w, height: m.h }, zIndex: 1
      }
    })
    return [...groups, ...mods]
  }, [layout, byId])
  const initialEdges = useMemo<Edge[]>(
    () => layout.edges.map((e) => ({
      id: e.id, source: e.from, target: e.to, type: 'smoothstep', label: e.label || undefined,
      markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: 'var(--text)' },
      style: { stroke: 'var(--text)', strokeWidth: 1.2, strokeDasharray: e.uncertain ? '6 4' : e.kind === 'control' ? '2 3' : undefined },
      labelStyle: { fontSize: 11, fill: 'var(--text)' }, labelBgStyle: { fill: 'var(--bg)' }, labelBgPadding: [3, 1] as [number, number],
      zIndex: 2
    })),
    [layout]
  )
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialNodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialEdges)
  useEffect(() => setNodes(initialNodes), [initialNodes, setNodes])
  useEffect(() => setEdges(initialEdges), [initialEdges, setEdges])
  useEffect(() => {
    setNodes((ns) => ns.map((n) => (n.type === 'fwModule' ? { ...n, data: { ...n.data, selected: n.id === selectedId } } : n)))
  }, [selectedId, setNodes])

  if (data.modules.length === 0) return <p className="pd-fig-weak">没有读出可画的模块。</p>
  return (
    <div className="pd-ff" style={{ height }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={(_e, n) => n.type === 'fwModule' && onSelect(n.id)}
        onPaneClick={() => onSelect(null)}
        nodesConnectable={false}
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.12 }}
        minZoom={0.15}
        maxZoom={2.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  )
}

/** 点击模块后显示：它的角色、论文正文里对它的描述（带原文引用跳转）、不确定的标记。 */
export function ModuleDetail({ module: m, a, md }: { module: FrameworkModule | null; a: FigureAnalysis; md: (s: string) => ReactElement }): ReactElement {
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  if (!m) return <p className="pd-fig-weak pd-ff-hint">点击流程图里的模块，查看它在论文正文里的描述。</p>
  const map = a.mapping?.modules?.[m.id]
  return (
    <div className="pd-ff-detail">
      <div className="pd-fig-mod-head">
        <strong>{m.label}</strong>
        <Tag tone="outline">{KIND_LABEL[m.kind]}</Tag>
        {m.in_text === false && <Tag tone="warning" title="这个名字没有出现在图的文字层里，可能是模型读错或自己起的名字">图中未找到该文字</Tag>}
      </div>
      {m.role && <div className="pd-fig-weak">{m.role}</div>}
      {map?.found && map.description ? (
        <>
          {md(map.description)}
          <div className="pd-ff-jumps">
            {map.ords.map((o) => (
              <button key={o} type="button" className="pd-tp-link" onClick={() => void jumpToOrd(o)}>跳到原文</button>
            ))}
          </div>
        </>
      ) : (
        <div className="pd-fig-weak">论文正文里没有找到对它的描述。</div>
      )}
    </div>
  )
}

const LEGEND = '实线 = 数据流 · 点线 = 控制流 · 虚线和“?”表示不确定：模块名没有在图的文字层里找到。位置由程序自动排版，不是原图里的位置。'

/** 流程图 + 模块详情；全屏时左边是原图，右边是流程图，便于逐项对照。 */
export function FrameworkFlowSection({
  data, a, md, paperId, fig
}: { data: FrameworkData; a: FigureAnalysis; md: (s: string) => ReactElement; paperId: string; fig?: FigureGroup }): ReactElement {
  const [selected, setSelected] = useState<string | null>(null)
  const [full, setFull] = useState(false)
  const mod = selected ? data.modules.find((m) => m.id === selected) ?? null : null
  const orig = useBlobUrl(full && fig ? () => textApi.regionVector(paperId, fig.page, fig.bbox, 130) : null, [full, paperId, fig?.id])
  return (
    <section>
      <div className="pd-ff-head">
        <h4 className="pd-card-sec">流程图 · {data.modules.length} 个模块 · {data.flows.length} 条连线</h4>
        <Button size="sm" variant="ghost" icon={<Maximize2 size={12} />} onClick={() => setFull(true)}>全屏对照原图</Button>
      </div>
      <FrameworkFlowChart data={data} selectedId={selected} onSelect={setSelected} />
      <p className="pd-ff-legend">{LEGEND}</p>
      <ModuleDetail module={mod} a={a} md={md} />
      <Dialog open={full} onClose={() => setFull(false)} title="框架图：流程图与原图对照" width={1180}>
        <div className="pd-ff-full">
          <div className="pd-ff-full-orig">
            {orig.url ? <ZoomImage src={orig.url} alt="论文里的原图" /> : <p className="pd-fig-weak">{orig.error ? '原图加载失败。' : '正在加载原图…'}</p>}
          </div>
          <div className="pd-ff-full-chart">
            <FrameworkFlowChart data={data} selectedId={selected} onSelect={setSelected} height={520} />
            <ModuleDetail module={mod} a={a} md={md} />
          </div>
        </div>
        <p className="pd-ff-legend">{LEGEND}</p>
      </Dialog>
    </section>
  )
}
