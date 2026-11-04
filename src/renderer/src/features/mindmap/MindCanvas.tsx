import { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import {
  Background,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesState,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type NodeProps
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Maximize2, Minus, Plus } from 'lucide-react'
import type { MindNode } from '@/lib/types.mindmap'
import { useMindmapStore } from './mindmapStore'
import { layoutTree, nodeSize, planMove, visibleNodes } from './tree'

interface UiCtx {
  matchIds: Set<string>
  editingId: string | null
  dropId: string | null
  onToggle: (id: string) => void
  onStartEdit: (id: string) => void
  onCommit: (id: string, title: string) => void
  onCancel: () => void
}

const MindUi = createContext<UiCtx>({
  matchIds: new Set(),
  editingId: null,
  dropId: null,
  onToggle: () => undefined,
  onStartEdit: () => undefined,
  onCommit: () => undefined,
  onCancel: () => undefined
})

interface MindNodeData extends Record<string, unknown> {
  node: MindNode
  collapsed: boolean
}

function TitleEditor({ node, onCommit, onCancel }: { node: MindNode; onCommit: (t: string) => void; onCancel: () => void }) {
  const [text, setText] = useState(node.title)
  const ref = useRef<HTMLInputElement>(null)
  useEffect(() => {
    ref.current?.focus()
    ref.current?.select()
  }, [])
  return (
    <input
      ref={ref}
      className="pd-mmn-input nodrag nopan"
      value={text}
      maxLength={60}
      aria-label="节点标题"
      onChange={(e) => setText(e.target.value)}
      onBlur={() => onCommit(text)}
      onKeyDown={(e) => {
        e.stopPropagation()
        if (e.key === 'Enter' && !e.nativeEvent.isComposing) onCommit(text)
        if (e.key === 'Escape') onCancel()
      }}
    />
  )
}

const MindNodeView = memo(function MindNodeView({ data, selected }: NodeProps<Node<MindNodeData>>) {
  const ui = useContext(MindUi)
  const { node, collapsed } = data
  const { w, h } = nodeSize(node)
  const editing = ui.editingId === node.id
  const expandable = node.child_count > 0
  return (
    <div
      className="pd-mmn"
      data-depth={Math.min(node.depth, 4)}
      data-selected={selected || undefined}
      data-match={ui.matchIds.has(node.id) || undefined}
      data-user={node.origin === 'user' || undefined}
      data-drop={ui.dropId === node.id || undefined}
      style={{ width: w, height: h }}
      title={editing ? undefined : `${node.title}${node.summary ? `\n${node.summary}` : ''}`}
      onDoubleClick={(e) => {
        e.stopPropagation()
        ui.onStartEdit(node.id)
      }}
    >
      <Handle type="target" position={Position.Left} isConnectable={false} />
      {editing ? (
        <TitleEditor node={node} onCommit={(t) => ui.onCommit(node.id, t)} onCancel={ui.onCancel} />
      ) : (
        <>
          {node.depth === 2 && (
            <span className="pd-mmn-n" aria-hidden="true">
              {node.ord + 1}
            </span>
          )}
          <span className="pd-mmn-t">{node.title}</span>
        </>
      )}
      {expandable && (
        <button
          type="button"
          className="pd-mmn-tg nodrag nopan"
          aria-label={collapsed ? `展开 ${node.child_count} 个子节点` : '折叠子节点'}
          aria-expanded={!collapsed}
          title={collapsed ? `展开 ${node.child_count} 个子节点 · 空格` : '折叠子节点 · 空格'}
          onClick={(e) => {
            e.stopPropagation()
            ui.onToggle(node.id)
          }}
        >
          {collapsed ? node.child_count : '−'}
        </button>
      )}
      <Handle type="source" position={Position.Right} isConnectable={false} />
    </div>
  )
})

const nodeTypes = { mind: MindNodeView }

/** 初始视野：保证文字可读（缩放不低于 0.7）；整张图放不下时可以拖动、缩放，或点“适应”。 */
const READABLE_FIT = { padding: 0.08, minZoom: 0.7, maxZoom: 1.1, duration: 0 } as const
const FIT_ALL = { padding: 0.1, maxZoom: 1.1, duration: 250 } as const

function ZoomBar() {
  const rf = useReactFlow()
  const zoom = useStore((s) => s.transform[2])
  return (
    <div className="pd-mm-zoom" role="group" aria-label="缩放">
      <button type="button" aria-label="缩小" title="缩小 · 滚轮向下" onClick={() => void rf.zoomOut({ duration: 120 })}>
        <Minus size={13} />
      </button>
      <button type="button" className="pd-mm-zoom-pct" title="恢复 100%" onClick={() => void rf.zoomTo(1, { duration: 150 })}>
        {Math.round(zoom * 100)}%
      </button>
      <button type="button" aria-label="放大" title="放大 · 滚轮向上" onClick={() => void rf.zoomIn({ duration: 120 })}>
        <Plus size={13} />
      </button>
      <span className="pd-mm-zoom-sep" />
      <button type="button" className="pd-mm-zoom-fit" title="整张图适应窗口 · 双击空白处" onClick={() => void rf.fitView(FIT_ALL)}>
        <Maximize2 size={12} />
        适应
      </button>
    </div>
  )
}

function Inner({ matchIds }: { matchIds: Set<string> }) {
  const view = useMindmapStore((s) => s.view)
  const tree = useMindmapStore((s) => s.trees[s.view])
  const collapsedSet = useMindmapStore((s) => s.collapsed[s.view])
  const selectedId = useMindmapStore((s) => s.selectedId)
  const focus = useMindmapStore((s) => s.focus)
  const editRequest = useMindmapStore((s) => s.editRequest)
  const rf = useReactFlow()
  const [editingId, setEditingId] = useState<string | null>(null)
  const [dropId, setDropId] = useState<string | null>(null)
  const [nonce, setNonce] = useState(0)
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<MindNodeData>>([])
  const fitted = useRef<string>('')
  const dragId = useRef<string | null>(null)
  // 画布实际尺寸：自己用 ResizeObserver 量（详情栏开合会改变它；不依赖 React Flow 内部记录的宽高）
  const wrapRef = useRef<HTMLDivElement>(null)
  const [box, setBox] = useState({ w: 0, h: 0 })
  const cw = box.w
  const ch = box.h
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const measure = (): void => setBox((b) => (b.w === el.clientWidth && b.h === el.clientHeight ? b : { w: el.clientWidth, h: el.clientHeight }))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  const visible = useMemo(() => (tree ? visibleNodes(tree.nodes, collapsedSet ?? new Set()) : []), [tree, collapsedSet])
  const layout = useMemo(() => layoutTree(visible), [visible])

  // 布局（可见节点集合 / 内容变化）→ 重建画布节点；选中状态单独同步，避免选中时重置位置
  useEffect(() => {
    const sel = useMindmapStore.getState().selectedId
    setNodes(
      visible.map((n) => {
        const b = layout.get(n.id)!
        return {
          id: n.id,
          type: 'mind',
          position: { x: b.x, y: b.y },
          data: { node: n, collapsed: !!collapsedSet?.has(n.id) },
          selected: n.id === sel,
          draggable: n.parent_id !== null
        }
      })
    )
  }, [visible, layout, collapsedSet, setNodes, nonce])
  useEffect(() => {
    setNodes((ns) => ns.map((n) => (n.selected === (n.id === selectedId) ? n : { ...n, selected: n.id === selectedId })))
  }, [selectedId, setNodes])

  const edges = useMemo<Edge[]>(() => {
    const ids = new Set(visible.map((n) => n.id))
    return visible
      .filter((n) => n.parent_id && ids.has(n.parent_id))
      .map((n) => ({ id: `e-${n.id}`, source: n.parent_id as string, target: n.id, selectable: false, focusable: false, className: n.depth === 2 ? 'pd-mme-stage' : undefined }))
  }, [visible])

  // 保证某个节点完全在视野内：被详情栏、工具条挡住或在屏幕外时，最小幅度平移过去；已经可见就不动
  const layoutRef = useRef(layout)
  layoutRef.current = layout
  const ensureVisible = useCallback(
    (id: string): void => {
      const b = layoutRef.current.get(id)
      if (!b || cw === 0 || ch === 0) return
      const { x: tx, y: ty, zoom } = rf.getViewport()
      const pad = 48
      const left = b.x * zoom + tx
      const right = (b.x + b.w) * zoom + tx
      const top = b.y * zoom + ty
      const bottom = (b.y + b.h) * zoom + ty
      let dx = 0
      let dy = 0
      if (left < pad) dx = pad - left
      else if (right > cw - pad) dx = cw - pad - right
      if (top < pad) dy = pad - top
      else if (bottom > ch - pad - 30) dy = ch - pad - 30 - bottom // 底部留出状态行
      if (dx !== 0 || dy !== 0) void rf.setViewport({ x: tx + dx, y: ty + dy, zoom }, { duration: 220 })
    },
    [rf, cw, ch]
  )
  // 选中、新增节点、详情栏开合（画布宽度变化）之后都检查一次
  useEffect(() => {
    if (!selectedId) return
    const t = window.setTimeout(() => ensureVisible(selectedId), 180)
    return () => window.clearTimeout(t)
  }, [selectedId, ensureVisible, visible])

  // 折叠 / 展开 / 切换后，如果视野里一个节点都没有了（内容整体移到了别处），自动适应画面，避免看到一片空白
  useEffect(() => {
    if (layout.size === 0 || cw === 0 || ch === 0) return
    const t = window.setTimeout(() => {
      const { x: tx, y: ty, zoom } = rf.getViewport()
      for (const b of layout.values()) {
        const l = b.x * zoom + tx
        const r = (b.x + b.w) * zoom + tx
        const tp = b.y * zoom + ty
        const bt = (b.y + b.h) * zoom + ty
        if (r > 0 && l < cw && bt > 0 && tp < ch) return
      }
      void rf.fitView({ ...READABLE_FIT, duration: 250 })
    }, 200)
    return () => window.clearTimeout(t)
  }, [layout, cw, ch, rf])

  // 首次载入 / 切换视角后适应画面（保证文字可读）
  useEffect(() => {
    if (!tree || nodes.length === 0) return
    const key = `${tree.paper_id}:${view}`
    if (fitted.current === key) return
    fitted.current = key
    // 节点还没量好尺寸时 fitView 会返回 false：隔一会儿再试，最多约 1 秒
    let tries = 0
    let t = 0
    const attempt = (): void => {
      void rf.fitView(READABLE_FIT).then((ok) => {
        if (!ok && tries++ < 12) t = window.setTimeout(attempt, 80)
      })
    }
    t = window.setTimeout(attempt, 30)
    return () => window.clearTimeout(t)
  }, [tree, view, nodes.length, rf])

  // 搜索 / 目录选择：把视野移到该节点
  useEffect(() => {
    if (!focus) return
    const t = window.setTimeout(() => {
      const b = layout.get(focus.id)
      if (!b) return
      const n = rf.getNode(focus.id)
      const x = (n?.position.x ?? b.x) + b.w / 2
      const y = (n?.position.y ?? b.y) + b.h / 2
      void rf.setCenter(x, y, { zoom: Math.max(rf.getZoom(), 0.9), duration: 350 })
    }, 60)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus?.seq])

  // 外部“整理布局”：位置被清空后重新适应
  useEffect(() => {
    const onFit = (): void => void rf.fitView(FIT_ALL)
    window.addEventListener('pd-mindmap-fit', onFit)
    return () => window.removeEventListener('pd-mindmap-fit', onFit)
  }, [rf])

  // 快捷键（Tab / Enter 新增、F2）请求某个节点进入改名
  useEffect(() => {
    if (editRequest) setEditingId(editRequest.id)
  }, [editRequest])

  const ctx = useMemo<UiCtx>(
    () => ({
      matchIds,
      editingId,
      dropId,
      onToggle: (id) => useMindmapStore.getState().toggleCollapse(id),
      onStartEdit: (id) => {
        void useMindmapStore.getState().select(id)
        setEditingId(id)
      },
      onCommit: (id, title) => {
        setEditingId(null)
        const cur = useMindmapStore.getState().trees[useMindmapStore.getState().view]?.nodes.find((n) => n.id === id)
        const t = title.trim()
        if (cur && t && t !== cur.title) void useMindmapStore.getState().patchNode(id, { title: t })
      },
      onCancel: () => setEditingId(null)
    }),
    [matchIds, editingId, dropId]
  )

  /** 拖动中：找出与被拖节点重叠面积最大的节点，作为“放下后成为它的子节点/同级”的目标并高亮。 */
  const findTarget = useCallback(
    (dragged: Node): string | null => {
      const dw = dragged.measured?.width ?? 0
      const dh = dragged.measured?.height ?? 0
      let best: { id: string; area: number } | null = null
      for (const n of rf.getIntersectingNodes(dragged, true)) {
        if (n.id === dragged.id) continue
        const w = n.measured?.width ?? 0
        const h = n.measured?.height ?? 0
        const ox = Math.min(dragged.position.x + dw, n.position.x + w) - Math.max(dragged.position.x, n.position.x)
        const oy = Math.min(dragged.position.y + dh, n.position.y + h) - Math.max(dragged.position.y, n.position.y)
        const area = Math.max(0, ox) * Math.max(0, oy)
        if (area > 0 && (!best || area > best.area)) best = { id: n.id, area }
      }
      if (!best || !tree) return null
      return planMove(tree.nodes, dragged.id, best.id, 0, 0) ? best.id : null
    },
    [rf, tree]
  )

  const onDragStop = useCallback(
    (_e: unknown, dragged: Node) => {
      const id = dragId.current
      dragId.current = null
      setDropId(null)
      const target = id ? findTarget(dragged) : null
      if (id && target && tree) {
        const over = rf.getNode(target)
        const plan = planMove(
          tree.nodes,
          id,
          target,
          dragged.position.y + (dragged.measured?.height ?? 0) / 2,
          (over?.position.y ?? 0) + (over?.measured?.height ?? 0) / 2
        )
        if (plan) void useMindmapStore.getState().moveNode(id, plan.parentId, plan.index)
      }
      // 没放到任何节点上，或移动完成后：回到整齐的自动布局位置
      setNonce((n) => n + 1)
    },
    [rf, tree, findTarget]
  )

  return (
    <MindUi.Provider value={ctx}>
      <div
        ref={wrapRef}
        className="pd-mm-flow"
        onDoubleClick={(e) => {
          if ((e.target as HTMLElement).classList.contains('react-flow__pane')) void rf.fitView(FIT_ALL)
        }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onNodeDragStart={(_e, n) => {
            dragId.current = n.id
          }}
          onNodeDrag={(_e, n) => setDropId(findTarget(n))}
          onNodeDragStop={onDragStop}
          onNodeClick={(_e, n) => void useMindmapStore.getState().select(n.id)}
          onPaneClick={() => {
            setEditingId(null)
            void useMindmapStore.getState().select(null)
          }}
          nodesConnectable={false}
          zoomOnDoubleClick={false}
          deleteKeyCode={null}
          selectionKeyCode={null}
          disableKeyboardA11y
          minZoom={0.15}
          maxZoom={1.8}
          onlyRenderVisibleElements
          proOptions={{ hideAttribution: false }}
          defaultEdgeOptions={{ type: 'smoothstep' }}
        >
          <Background gap={28} size={1} color="var(--border)" />
          <ZoomBar />
        </ReactFlow>
      </div>
    </MindUi.Provider>
  )
}

export function MindCanvas({ matchIds }: { matchIds: Set<string> }) {
  return (
    <ReactFlowProvider>
      <Inner matchIds={matchIds} />
    </ReactFlowProvider>
  )
}
