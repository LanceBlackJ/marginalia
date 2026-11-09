import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { BarChart3, Compass, Library, Download, FileText, FolderInput, FolderOpen, LayoutGrid, Link2, List, ListFilter, MoreHorizontal, Plus, Quote, RefreshCw, X } from 'lucide-react'
import { Button, Dialog, EmptyState, IconButton, Menu, Segmented, Select, Skeleton, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { libraryApi } from '@/lib/api.library'
import { importFolder, importLocalFiles, useImportUi } from '@/features/importing/importActions'
import { PaperInfoDialog } from '@/features/library/PaperInfoDialog'
import { TagDialog } from '@/features/library/TagDialog'
import { CiteDialog } from '@/features/library/CiteDialog'
import { ExportDialog, type ExportScope } from '@/features/library/ExportDialog'
import { FolderRail } from '@/features/library/FolderRail'
import { FilterChips, FilterPanel } from '@/features/library/FilterPanel'
import { DeleteFolderDialog, MoveDialog, SaveViewDialog, SuggestionsBar } from '@/features/library/LibraryDialogs'
import { descendantIds, filtersToQuery, folderPath, hasTextQuery, isEmptyFilters, normalizeFilters, parseFilters, serializeFilters } from '@/features/library/filterModel'
import { useFacets, useFolders, useSuggestions } from '@/features/library/useLibraryMeta'
import { Bookshelf } from '@/features/library/Bookshelf'
import { PaperCard, PaperRow, PaperTableHeader, RecentItem, type PaperItemActions } from '@/features/library/PaperItems'
import { usePapers } from '@/features/library/usePapers'
import { isProcessingActive } from '@/features/library/processingModel'
import { ReparseDialog } from '@/features/library/ReparseDialog'
import { reparsePapers } from '@/features/library/reparseActions'
import { canReparse, outdatedCount } from '@/features/library/reparseModel'
import { usePrefs } from '@/stores/prefsStore'
import type { PaperSummary, PaperTag } from '@/lib/types'
import type { FolderItem, PaperFilters, SmartView } from '@/lib/types.library'
import '@/features/library/library.css'
import '@/features/library/folders.css'

type View = 'all' | 'recent' | 'favorite'
type Sort = 'recent' | 'title' | 'author' | 'year' | 'imported' | 'relevance'

const SORT_OPTIONS = [
  { value: 'imported', label: '导入时间' },
  { value: 'recent', label: '最近阅读' },
  { value: 'title', label: '标题' },
  { value: 'author', label: '作者' },
  { value: 'year', label: '年份' }
]

function ImportMenuButton({ primary = true }: { primary?: boolean }) {
  const importUi = useImportUi()
  return (
    <Menu
      align="end"
      trigger={({ toggle, open }) => (
        <Button variant={primary ? 'primary' : 'secondary'} icon={<Plus size={15} />} onClick={toggle} aria-haspopup="menu" aria-expanded={open}>
          导入
        </Button>
      )}
      items={[
        { key: 'files', label: '本地 PDF…', icon: <FileText size={15} />, onSelect: () => void importLocalFiles() },
        { key: 'folder', label: '文件夹…', icon: <FolderOpen size={15} />, onSelect: () => void importFolder() },
        { key: 'doi', label: 'DOI…', icon: <Quote size={15} />, onSelect: importUi.openDoi },
        { key: 'url', label: '论文链接…', icon: <Link2 size={15} />, onSelect: importUi.openUrl }
      ]}
    />
  )
}

export function LibraryPage() {
  const navigate = useNavigate()
  const importUi = useImportUi()
  const [params, setParams] = useSearchParams()
  const q = params.get('q') ?? ''
  const view = (params.get('view') as View | null) ?? 'all'
  const legacyTag = params.get('tag') ?? ''
  const rawFilters = params.get('f')

  const prefs = usePrefs()
  const meta = useFolders()

  // 筛选条件放在 URL 里（f=JSON），刷新、前进后退都能还原；旧的 ?tag= 参数并入标签条件
  const filters: PaperFilters = useMemo(() => {
    const f = parseFilters(rawFilters)
    return legacyTag ? normalizeFilters({ ...f, tags: [...new Set([...(f.tags ?? []), legacyTag])] }) : f
  }, [rawFilters, legacyTag])
  const filterActive = !isEmptyFilters(filters)
  const textQuery = hasTextQuery(filters, q)

  const [sortChoice, setSortChoice] = useState<Sort | null>(null)
  const sort: Sort = view === 'recent' ? 'recent' : sortChoice ?? (textQuery ? 'relevance' : prefs.librarySort)
  const order = sort === 'title' || sort === 'author' ? 'asc' : 'desc'
  const query = useMemo(
    () => ({ q: q || undefined, view, sort, order, limit: 200, ...filtersToQuery(filters) }) as const,
    [q, view, sort, order, filters]
  )
  const list = usePapers(query)
  // 有论文正在后台处理公式/表格时，低频刷新列表（6 秒一次），处理完才显示“已完成”；没有就不轮询
  const anyProcessing = list.items.some((p) => isProcessingActive(p.processing) && p.processing?.overall === 'processing')
  useEffect(() => {
    if (!anyProcessing) return
    const timer = window.setInterval(() => list.reload(), 6000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyProcessing])
  const recentQuery = usePapers({ view: 'recent', sort: 'recent', order: 'desc', limit: 5 }, view === 'all' && !q && !filterActive)

  const [info, setInfo] = useState<PaperSummary | null>(null)
  const [tagging, setTagging] = useState<PaperSummary | null>(null)
  const [exporting, setExporting] = useState<ExportScope | null>(null)
  const [citing, setCiting] = useState<PaperSummary | null>(null)
  const [toDelete, setToDelete] = useState<PaperSummary | null>(null)
  const [reparseIds, setReparseIds] = useState<string[] | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const facets = useFacets(panelOpen, list.total)
  const [tagNames, setTagNames] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [moving, setMoving] = useState<string[] | null>(null)
  const [folderToDelete, setFolderToDelete] = useState<FolderItem | null>(null)
  const [savingView, setSavingView] = useState(false)
  const [appliedView, setAppliedView] = useState<{ id: string; key: string } | null>(null)
  const [suggestions, setSuggestions] = useSuggestions(`${meta.folders.counts.unfiled}:${meta.folders.items.length}:${meta.folders.counts.all}`)

  // 标签名字：芯片和导出用
  useEffect(() => {
    if (!facets) return
    setTagNames(Object.fromEntries(facets.tags.map((t) => [t.id, t.name])))
  }, [facets])
  useEffect(() => {
    if (!legacyTag || Object.keys(tagNames).length > 0) return
    import('@/lib/api.discovery').then(({ kbApi }) => kbApi.topics().then((r) => setTagNames(Object.fromEntries(r.items.map((t) => [t.id, t.name]))))).catch(() => undefined)
  }, [legacyTag, tagNames])

  // 列表变了：去掉已不在列表里的勾选
  useEffect(() => {
    setSelected((s) => {
      if (s.size === 0) return s
      const ids = new Set(list.items.map((p) => p.id))
      const next = new Set([...s].filter((id) => ids.has(id)))
      return next.size === s.size ? s : next
    })
  }, [list.items])

  const writeParams = useCallback(
    (mutate: (next: URLSearchParams) => void, push = false): void => {
      const next = new URLSearchParams(params)
      mutate(next)
      setParams(next, { replace: !push })
    },
    [params, setParams]
  )

  const setFilters = useCallback(
    (f: PaperFilters, keepView = true): void => {
      const n = normalizeFilters(f)
      writeParams((next) => {
        next.delete('tag')
        const s = serializeFilters(n)
        if (s) next.set('f', s)
        else next.delete('f')
        if (!keepView) next.delete('view')
      })
    },
    [writeParams]
  )

  const lookup = useMemo(
    () => ({ tagName: (id: string) => tagNames[id] ?? '标签', folderPath: (id: string) => folderPath(meta.folders.items, id) || '已删除的文件夹' }),
    [tagNames, meta.folders.items]
  )

  const setView = (v: View): void => {
    writeParams((next) => {
      if (v === 'all') next.delete('view')
      else next.set('view', v)
    }, true)
  }
  const selectFolder = (id: string | undefined): void => setFilters({ ...filters, folder: id }, true)
  const clearSearch = (): void => writeParams((next) => next.delete('q'))
  const clearAll = (): void =>
    writeParams((next) => {
      next.delete('q')
      next.delete('f')
      next.delete('tag')
    })

  /** 书架上的书：先进详情页，在那里决定怎么读。解析中的论文提示一下。 */
  const openProfile = useCallback(
    (p: PaperSummary): void => {
      if (p.status === 'parsing') toast.info('这篇论文仍在解析中', '解析完成后即可阅读。')
      else navigate(`/paper/${p.id}`)
    },
    [navigate]
  )

  const open = useCallback(
    (p: PaperSummary): void => {
      if (p.status === 'ready') navigate(`/reader/${p.id}`)
      else if (p.status === 'parsing') toast.info('这篇论文仍在解析中', '解析完成后即可阅读。')
      else setInfo(p)
    },
    [navigate]
  )

  const toggleFavorite = useCallback(
    async (p: PaperSummary): Promise<void> => {
      list.patchLocal(p.id, { favorite: !p.favorite })
      try {
        await api.papers.patch(p.id, { favorite: !p.favorite })
        if (view === 'favorite' || filters.favorite) list.reload()
        meta.reload()
      } catch (err) {
        list.patchLocal(p.id, { favorite: p.favorite })
        toast.error('收藏失败', errorMessage(err))
      }
    },
    [list, view, filters.favorite, meta]
  )

  const onTagsChange = (paperId: string, tags: PaperTag[]): void => {
    list.patchLocal(paperId, { tags })
    recentQuery.patchLocal(paperId, { tags })
    setTagging((p) => (p && p.id === paperId ? { ...p, tags } : p))
    if (filters.tags?.length) list.reload()
  }

  const confirmDelete = async (): Promise<void> => {
    if (!toDelete) return
    setDeleting(true)
    try {
      const res = await api.papers.remove(toDelete.id)
      list.removeLocal(toDelete.id)
      recentQuery.reload()
      meta.reload()
      toast.success('已从文献库删除', '原文件未受影响。')
      if (res.warnings && res.warnings.length > 0) toast.info('有文件没能清理', res.warnings[0])
      setToDelete(null)
      setInfo(null)
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    } finally {
      setDeleting(false)
    }
  }

  // ---------------------------------------------------------------- 文件夹操作
  const refreshAll = useCallback((): void => {
    meta.reload()
    list.reload()
  }, [meta, list])

  const movePapers = useCallback(
    async (ids: string[], folderId: string | null): Promise<void> => {
      try {
        const r = await libraryApi.movePapers(ids, folderId)
        const target = folderId ? folderPath(meta.folders.items, folderId) : '未分类'
        toast.success(`已移动 ${r.moved} 篇到“${target}”`, r.skipped_supplements > 0 ? `${r.skipped_supplements} 份补充材料跟随主论文，没有单独移动。` : undefined)
        setSelected(new Set())
        setMoving(null)
        refreshAll()
      } catch (err) {
        toast.error('移动失败', errorMessage(err))
      }
    },
    [meta.folders.items, refreshAll]
  )

  const createFolder = useCallback(
    async (name: string, parentId: string | null): Promise<FolderItem | null> => {
      try {
        const f = await libraryApi.createFolder(name, parentId)
        meta.reload()
        return f
      } catch (err) {
        toast.error('无法新建文件夹', errorMessage(err))
        return null
      }
    },
    [meta]
  )

  const folderActions = {
    onCreate: async (name: string, parentId: string | null): Promise<boolean> => (await createFolder(name, parentId)) !== null,
    onRename: async (f: FolderItem, name: string): Promise<boolean> => {
      try {
        await libraryApi.renameFolder(f.id, name)
        refreshAll()
        return true
      } catch (err) {
        toast.error('无法重命名', errorMessage(err))
        return false
      }
    },
    onMoveFolder: (id: string, parentId: string | null): void => {
      libraryApi
        .moveFolder(id, parentId)
        .then(refreshAll)
        .catch((err) => toast.error('无法移动文件夹', errorMessage(err)))
    },
    onReorder: (parentId: string | null, ids: string[]): void => {
      libraryApi
        .reorderFolders(parentId, ids)
        .then(() => meta.reload())
        .catch((err) => toast.error('无法调整顺序', errorMessage(err)))
    }
  }

  const confirmDeleteFolder = async (): Promise<void> => {
    if (!folderToDelete) return
    try {
      const r = await libraryApi.deleteFolder(folderToDelete.id)
      toast.success('已删除文件夹', r.moved_papers > 0 ? `${r.moved_papers} 篇论文回到了“未分类”。` : undefined)
      if (filters.folder && (filters.folder === folderToDelete.id || descendantIds(meta.folders.items, folderToDelete.id).includes(filters.folder))) {
        setFilters({ ...filters, folder: undefined })
      }
      setFolderToDelete(null)
      refreshAll()
    } catch (err) {
      toast.error('删除失败', errorMessage(err))
    }
  }

  // ---------------------------------------------------------------- 智能视图
  const applyView = (v: SmartView): void => {
    setAppliedView({ id: v.id, key: serializeFilters(v.query) })
    writeParams((next) => {
      next.delete('tag')
      next.delete('view')
      next.delete('q')
      const s = serializeFilters(v.query)
      if (s) next.set('f', s)
      else next.delete('f')
    }, true)
  }
  const activeViewId = appliedView && appliedView.key === serializeFilters(filters) ? appliedView.id : null

  const saveView = async (name: string): Promise<boolean> => {
    try {
      const v = await libraryApi.createSmartView(name, normalizeFilters(filters))
      toast.success('已保存智能视图', `“${v.name}”出现在左侧。`)
      setAppliedView({ id: v.id, key: serializeFilters(v.query) })
      meta.reload()
      return true
    } catch (err) {
      toast.error('无法保存', errorMessage(err))
      return false
    }
  }

  // ---------------------------------------------------------------- 建议归类
  const acceptSuggestion = (paperId: string, folderId: string): void => void movePapers([paperId], folderId)
  const ignoreSuggestion = (paperId: string, folderId: string): void => {
    setSuggestions((s) => (s ? { ...s, items: s.items.filter((x) => !(x.paper_id === paperId && x.folder_id === folderId)) } : s))
    libraryApi.ignoreSuggestion(paperId, folderId).catch((err) => toast.error('无法忽略', errorMessage(err)))
  }

  // ---------------------------------------------------------------- 论文行动作
  const toggleSelect = useCallback((p: PaperSummary): void => {
    setSelected((s) => {
      const n = new Set(s)
      if (n.has(p.id)) n.delete(p.id)
      else n.add(p.id)
      return n
    })
  }, [])

  const actions: PaperItemActions = useMemo(
    () => ({
      onOpen: openProfile,
      onRead: open,
      onToggleFavorite: (p) => void toggleFavorite(p),
      onDelete: setToDelete,
      onEditTags: setTagging,
      onExport: (p) => setExporting({ label: `《${p.title.slice(0, 30)}》`, paperIds: [p.id] }),
      onCite: setCiting,
      onMove: (p) => setMoving(selected.has(p.id) ? [...selected] : [p.id]),
      onReparse: (p) => setReparseIds(selected.has(p.id) ? [...selected] : [p.id]),
      selected,
      onToggleSelect: toggleSelect
    }),
    [open, openProfile, toggleFavorite, selected, toggleSelect]
  )

  // 导出（引文与阅读笔记）：只有一个标签条件时按该标签导出，否则导出全部
  const openExport = (): void => {
    const only = filters.tags?.length === 1 ? filters.tags[0] : null
    setExporting(only ? { label: `标签：${tagNames[only] ?? '所选标签'}`, tagId: only } : { label: '全部文献' })
  }
  const recents = recentQuery.items.filter((p) => p.last_opened_at).slice(0, 3)
  const showRecent = view === 'all' && !q && !filterActive && recents.length > 0
  const isEmptyLibrary = !list.loading && !list.error && list.total === 0 && !q && view === 'all' && !filterActive
  const anyNarrowing = Boolean(q) || filterActive
  const activeCount = Object.keys(normalizeFilters(filters)).length
  const folderLabel = filters.folder === 'none' ? '未分类' : filters.folder ? lookup.folderPath(filters.folder) : null
  const movingCurrent = moving && moving.length === 1 ? (list.items.find((p) => p.id === moving[0])?.folder_id ?? null) : undefined
  const sortOptions = textQuery ? [{ value: 'relevance', label: '相关度' }, ...SORT_OPTIONS] : SORT_OPTIONS

  return (
    <div className="pd-page">
      <div className="pd-page-inner">
        <div className="pd-lib-head">
          <div>
            <h1 className="pd-page-title">我的文献</h1>
            <p className="pd-page-subtitle">管理和探索你的研究资料。</p>
          </div>
          <div className="pd-lib-head-actions">
            <Button icon={<BarChart3 size={15} />} onClick={() => navigate('/stats')}>
              统计
            </Button>
            <ImportMenuButton />
            <Menu
              align="end"
              trigger={({ toggle, open }) => (
                <IconButton icon={<MoreHorizontal size={17} />} label="更多" selected={open} onClick={toggle} aria-haspopup="menu" aria-expanded={open} />
              )}
              items={[
                { key: 'discover', label: '发现论文', icon: <Compass size={15} />, onSelect: () => navigate('/discover') },
                { key: 'export', label: '导出引文与阅读笔记…', icon: <Download size={15} />, disabled: list.total === 0, onSelect: openExport }
              ]}
            />
          </div>
        </div>

        <div className="pd-lib-shell">
          <FolderRail
            folders={meta.folders}
            views={meta.views}
            view={view}
            folderId={filters.folder}
            activeViewId={activeViewId}
            onSelectView={setView}
            onSelectFolder={selectFolder}
            onApplyView={applyView}
            onDeleteView={(v) => {
              libraryApi
                .deleteSmartView(v.id)
                .then(() => meta.reload())
                .catch((err) => toast.error('无法删除', errorMessage(err)))
            }}
            onDropPapers={(folderId, ids) => void movePapers(ids, folderId)}
            onDelete={setFolderToDelete}
            {...folderActions}
          />

          <div className="pd-lib-main">
            {showRecent && (
              <section className="pd-recent" aria-label="最近阅读">
                <h2 className="pd-section-title">继续阅读</h2>
                <div className="pd-recent-row">
                  {recents.map((p) => (
                    <RecentItem key={p.id} paper={p} onOpen={open} />
                  ))}
                </div>
              </section>
            )}

            <SuggestionsBar data={suggestions} onAccept={acceptSuggestion} onIgnore={ignoreSuggestion} />

            <div className="pd-lib-toolbar">
              <Button size="sm" icon={<ListFilter size={14} />} aria-expanded={panelOpen} onClick={() => setPanelOpen((v) => !v)}>
                筛选{activeCount > 0 ? ` · ${activeCount}` : ''}
              </Button>
              <span className="pd-aux pd-muted">
                {list.loading ? '' : `${folderLabel ? `${folderLabel} · ` : ''}${q ? `“${q}” ` : ''}共 ${list.total} 篇`}
              </span>
              <div className="pd-lib-toolbar-right">
                {view !== 'recent' && (
                  <Select
                    size="sm"
                    aria-label="排序"
                    value={sort}
                    onChange={(v) => {
                      setSortChoice(v as Sort)
                      if (v !== 'relevance') prefs.set({ librarySort: v as Exclude<Sort, 'relevance'> })
                    }}
                    options={sortOptions}
                    style={{ width: 120 }}
                  />
                )}
                <Segmented<'shelf' | 'list' | 'grid'>
                  variant="text"
                  aria-label="视图"
                  value={prefs.libraryLayout}
                  onChange={(v) => prefs.set({ libraryLayout: v })}
                  options={[
                    { value: 'shelf', label: <Library size={14} /> },
                    { value: 'list', label: <List size={14} /> },
                    { value: 'grid', label: <LayoutGrid size={14} /> }
                  ]}
                />
              </div>
            </div>

            {panelOpen && <FilterPanel filters={filters} facets={facets} onChange={(f) => setFilters(f)} onSaveView={() => setSavingView(true)} />}
            <FilterChips filters={filters} lookup={lookup} onChange={(f) => setFilters(f)} />

            {selected.size === 0 && outdatedCount(list.items) > 0 && (
              <div className="pd-reparse-banner" role="status">
                <RefreshCw size={13} />
                <span>有 {outdatedCount(list.items)} 篇论文是用旧版解析器解析的，重新解析可以获得更好的图表、表格和正文结构；笔记、批注和卡片会保留。</span>
                <Button size="sm" onClick={() => setReparseIds(list.items.filter((p) => canReparse(p) && p.parser_outdated).map((p) => p.id))}>
                  全部重新解析…
                </Button>
              </div>
            )}

            {selected.size > 0 && (
              <div className="pd-bulk" role="toolbar" aria-label="批量操作">
                <span>已选 {selected.size} 篇</span>
                <Button size="sm" variant="primary" icon={<FolderInput size={14} />} onClick={() => setMoving([...selected])}>
                  移动到文件夹…
                </Button>
                <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => setReparseIds([...selected])}>
                  重新解析…
                </Button>
                <Button size="sm" onClick={() => setSelected(new Set(list.items.map((p) => p.id)))}>
                  选择当前列表全部 {list.items.length} 篇
                </Button>
                <Button size="sm" variant="ghost" icon={<X size={14} />} onClick={() => setSelected(new Set())}>
                  取消选择
                </Button>
                <span className="pd-aux pd-weak-text">也可以直接把论文拖到左侧的文件夹里</span>
              </div>
            )}

            {list.loading ? (
              <div className="pd-lib-list" aria-busy>
                {Array.from({ length: 6 }, (_, i) => (
                  <div key={i} style={{ padding: '16px 0', borderBottom: '1px solid var(--border)' }}>
                    <Skeleton width="55%" height={14} />
                    <Skeleton width="35%" height={11} style={{ marginTop: 8 }} />
                  </div>
                ))}
              </div>
            ) : list.error ? (
              <EmptyState tone="danger" title="文献列表加载失败" description={list.error} action={<Button onClick={list.reload}>重试</Button>} />
            ) : isEmptyLibrary ? (
              <EmptyState
                title="文献库还是空的"
                description="导入本地 PDF，或用 DOI / 链接添加论文，开始你的第一次精读。"
                action={
                  <>
                    <ImportMenuButton />
                    <Button icon={<Quote size={15} />} onClick={importUi.openDoi}>
                      DOI 导入
                    </Button>
                  </>
                }
              />
            ) : list.items.length === 0 ? (
              <EmptyState
                title={anyNarrowing ? '没有符合条件的论文' : view === 'favorite' ? '还没有收藏的论文' : '这里还没有内容'}
                description={anyNarrowing ? '试试去掉几个条件，或者换个关键词。' : view === 'favorite' ? '在论文右侧点击星标即可收藏。' : view === 'recent' ? '打开过的论文会出现在这里。' : undefined}
                action={anyNarrowing ? <Button onClick={q && !filterActive ? clearSearch : clearAll}>清除搜索和筛选</Button> : undefined}
              />
            ) : prefs.libraryLayout === 'shelf' ? (
              <Bookshelf
                papers={list.items}
                folders={meta.folders.items}
                grouped={!q && !filterActive && view === 'all'}
                actions={{
                  onOpen: openProfile,
                  onToggleFavorite: actions.onToggleFavorite,
                  onEditTags: setTagging,
                  onMove: (p, fid) => void movePapers([p.id], fid),
                  more: actions,
                  onCreateFolderFor: async (p, name) => {
                    const f = await createFolder(name, null)
                    if (f) await movePapers([p.id], f.id)
                  }
                }}
              />
            ) : prefs.libraryLayout === 'list' ? (
              <div className="pd-lib-list" role="table">
                <PaperTableHeader />
                {list.items.map((p) => (
                  <PaperRow key={p.id} paper={p} actions={actions} />
                ))}
              </div>
            ) : (
              <div className="pd-lib-grid">
                {list.items.map((p) => (
                  <PaperCard key={p.id} paper={p} actions={actions} />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      <TagDialog paper={tagging} onClose={() => setTagging(null)} onChange={onTagsChange} />
      <ExportDialog scope={exporting} onClose={() => setExporting(null)} />
      <CiteDialog paperId={citing?.id ?? null} title={citing?.title} onClose={() => setCiting(null)} />
      <PaperInfoDialog paper={info} onClose={() => setInfo(null)} onDelete={setToDelete} />
      <MoveDialog
        open={!!moving}
        count={moving?.length ?? 0}
        folders={meta.folders.items}
        currentFolderId={movingCurrent}
        onClose={() => setMoving(null)}
        onMove={(folderId) => movePapers(moving ?? [], folderId)}
        onCreate={createFolder}
      />
      <DeleteFolderDialog
        folder={folderToDelete ? { ...folderToDelete, total_count: meta.folders.items.find((f) => f.id === folderToDelete.id)?.total_count ?? 0 } : null}
        childCount={folderToDelete ? descendantIds(meta.folders.items, folderToDelete.id).length - 1 : 0}
        onClose={() => setFolderToDelete(null)}
        onConfirm={confirmDeleteFolder}
      />
      <SaveViewDialog open={savingView} onClose={() => setSavingView(false)} onSave={saveView} />

      <Dialog
        open={!!toDelete}
        onClose={() => !deleting && setToDelete(null)}
        title="从文献库删除这篇论文？"
        width={460}
        footer={
          <>
            <Button onClick={() => setToDelete(null)} disabled={deleting}>
              取消
            </Button>
            <Button variant="danger" solid loading={deleting} onClick={() => void confirmDelete()}>
              删除
            </Button>
          </>
        }
      >
        <p style={{ fontWeight: 500 }}>{toDelete?.title}</p>
        <p className="pd-muted" style={{ marginTop: 8 }}>
          只会删除 Marginalia 内保存的副本，以及这篇论文的分析、研究卡片、笔记和批注。你电脑上的原始文件不会被移动或删除。{toDelete && (toDelete.supplement_count ?? 0) > 0 ? `这篇论文的 ${toDelete.supplement_count} 份补充材料也会一并删除。` : ''}此操作无法撤销。
        </p>
      </Dialog>

      <ReparseDialog
        open={!!reparseIds}
        count={reparseIds?.length ?? 0}
        onClose={() => setReparseIds(null)}
        onConfirm={() => {
          const ids = (reparseIds ?? []).filter((id) => {
            const p = list.items.find((x) => x.id === id)
            return !p || canReparse(p)
          })
          setReparseIds(null)
          if (ids.length === 0) toast.info('没有可以重新解析的论文', '只有带 PDF 的正文论文才能重新解析。')
          else void reparsePapers(ids, { onDone: refreshAll })
        }}
      />
    </div>
  )
}
