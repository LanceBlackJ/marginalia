import { memo, useEffect, useMemo, useState } from 'react'
import { ChevronRight, Search, Sparkles } from 'lucide-react'
import { Button, Input, toast, useContextMenu } from '@/components/ui'
import type { CodeFileEntry, CodeSymbol } from '@/lib/types.code'
import { ancestorDirs, buildFileTree, filterFiles, roleBadge, type TreeNode } from './codeUtils'
import { useCodeStore } from './codeStore'

const SKIP_LABEL: Record<string, string> = { binary: '二进制', too_large: '过大', unsafe: '已忽略' }

const copyText = (text: string, what: string): void => void navigator.clipboard.writeText(text).then(() => toast.success(`已复制${what}`))

const FileRow = memo(function FileRow({ file, name, depth, active, onOpen }: { file: CodeFileEntry; name: string; depth: number; active: boolean; onOpen: (p: string) => void }) {
  const badge = file.skipped ? SKIP_LABEL[file.skipped] ?? file.skipped : roleBadge(file.role)
  const explain = useCodeStore((s) => s.explain)
  const cm = useContextMenu()
  return (
    <>
    {cm.element}
    <button
      type="button"
      className="pd-tree-row"
      onContextMenu={(e) =>
        cm.open(e, [
          { key: 'open', label: '打开', onSelect: () => onOpen(file.path) },
          { key: 'explain', label: 'AI 解释这个文件', disabled: !!file.skipped, onSelect: () => void explain({ kind: 'file', path: file.path }, file.path.split('/').pop() ?? file.path) },
          { key: 'sep', separator: true },
          { key: 'cname', label: '复制文件名', onSelect: () => copyText(file.path.split('/').pop() ?? file.path, '文件名') },
          { key: 'cpath', label: '复制路径', onSelect: () => copyText(file.path, '路径') }
        ])
      } data-active={active || undefined} data-skipped={file.skipped ? 'true' : undefined} style={{ paddingLeft: 10 + depth * 14 + 16 }} title={file.path} onClick={() => onOpen(file.path)}>
      <span className="pd-tree-name pd-ellipsis">{name}</span>
      {badge && <span className="pd-tree-badge">{badge}</span>}
    </button>
    </>
  )
})

function DirRow({ node, depth, expanded, toggle, children }: { node: TreeNode; depth: number; expanded: boolean; toggle: (p: string) => void; children: React.ReactNode }) {
  const explain = useCodeStore((s) => s.explain)
  const cm = useContextMenu()
  return (
    <>
      {cm.element}
      <div
        className="pd-tree-dirwrap"
        onContextMenu={(e) =>
          cm.open(e, [
            { key: 'toggle', label: expanded ? '折叠' : '展开', onSelect: () => toggle(node.path) },
            { key: 'explain', label: 'AI 概述这个目录', onSelect: () => void explain({ kind: 'module', path: node.path }, `目录 ${node.path}`) },
            { key: 'sep', separator: true },
            { key: 'cpath', label: '复制路径', onSelect: () => copyText(node.path, '路径') }
          ])
        }
      >
        <button type="button" className="pd-tree-row pd-tree-dir" aria-expanded={expanded} style={{ paddingLeft: 10 + depth * 14 }} title={node.path} onClick={() => toggle(node.path)}>
          <ChevronRight size={13} className="pd-tree-chev" />
          <span className="pd-tree-name pd-ellipsis">{node.name}</span>
        </button>
        <button type="button" className="pd-tree-explain" title={`让 AI 概述目录 ${node.path}`} aria-label={`解释目录 ${node.path}`} onClick={() => void explain({ kind: 'module', path: node.path }, `目录 ${node.path}`)}>
          <Sparkles size={12} />
        </button>
      </div>
      {expanded && children}
    </>
  )
}

export function FileTree() {
  const files = useCodeStore((s) => s.files)
  const openPath = useCodeStore((s) => s.openPath)
  const openFile = useCodeStore((s) => s.openFile)
  const explain = useCodeStore((s) => s.explain)
  const [q, setQ] = useState('')
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const tree = useMemo(() => buildFileTree(files), [files])

  // 首次载入：展开第一层目录
  useEffect(() => {
    setExpanded(new Set(tree.filter((n) => n.dir).map((n) => n.path)))
  }, [tree])
  // 打开文件时展开它的所有上级目录
  useEffect(() => {
    if (openPath) setExpanded((prev) => new Set([...prev, ...ancestorDirs(openPath)]))
  }, [openPath])

  const toggle = (p: string): void =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(p)) next.delete(p)
      else next.add(p)
      return next
    })

  const open = (p: string): void => void openFile(p)
  const results = useMemo(() => filterFiles(files, q), [files, q])

  const renderNodes = (nodes: TreeNode[], depth: number): React.ReactNode =>
    nodes.map((n) =>
      n.dir ? (
        <DirRow key={n.path} node={n} depth={depth} expanded={expanded.has(n.path)} toggle={toggle}>
          {renderNodes(n.children, depth + 1)}
        </DirRow>
      ) : (
        <FileRow key={n.path} file={n.file!} name={n.name} depth={depth} active={n.path === openPath} onOpen={open} />
      )
    )

  return (
    <div className="pd-tree">
      <div className="pd-tree-overall">
        <Button size="sm" icon={<Sparkles size={14} />} onClick={() => void explain({ kind: 'repo' }, '整个仓库的总讲解')} title="让 AI 按目录说明每个地方是干什么的">
          总讲解
        </Button>
      </div>
      <div className="pd-tree-search">
        <Input value={q} onChange={setQ} icon={<Search size={14} />} placeholder="搜索文件名" aria-label="搜索文件名" clearable />
      </div>
      <div className="pd-tree-scroll" role="tree" aria-label="文件树">
        {q.trim() ? (
          results.length === 0 ? (
            <p className="pd-aux pd-muted pd-tree-empty">没有匹配的文件</p>
          ) : (
            results.map((f) => <FileRow key={f.path} file={f} name={f.path} depth={-1} active={f.path === openPath} onOpen={open} />)
          )
        ) : files.length === 0 ? (
          <p className="pd-aux pd-muted pd-tree-empty">仓库里没有文件</p>
        ) : (
          renderNodes(tree, 0)
        )}
      </div>
    </div>
  )
}

const KIND_LABEL: Record<string, string> = { class: '类', function: '函数', method: '方法', main: '入口', module: '模块' }

/** 当前文件的符号大纲。 */
export function Outline() {
  const file = useCodeStore((s) => s.file)
  const selected = useCodeStore((s) => s.selectedSymbol)
  const selectSymbol = useCodeStore((s) => s.selectSymbol)
  // 函数里面的小辅助函数（比如到处都有的 def T(w)）不列进大纲：它们不是读代码的入口，只会把真正的函数淹没
  const symbols = useMemo(() => (file?.symbols ?? []).filter((s: CodeSymbol) => s.kind !== 'module' && !(s.kind === 'function' && s.parent)), [file])
  if (!file || symbols.length === 0) return null
  return (
    <div className="pd-outline">
      <div className="pd-outline-head">
        大纲 <span className="pd-muted">{symbols.length}</span>
      </div>
      <div className="pd-outline-list">
        {symbols.map((s) => (
          <button
            key={s.id}
            type="button"
            className="pd-outline-row"
            data-active={selected?.id === s.id || undefined}
            style={{ paddingLeft: 12 + Math.min(3, s.qualname.split('.').length - 1) * 12 }}
            title={`${s.qualname}  ${s.path}:${s.line_start}-${s.line_end}`}
            onClick={() => void selectSymbol(s)}
          >
            <span className="pd-outline-kind">{KIND_LABEL[s.kind] ?? s.kind}</span>
            <span className="pd-ellipsis">{s.name === '<main>' ? '主程序入口' : s.name}</span>
            <span className="pd-outline-line">{s.line_start}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
