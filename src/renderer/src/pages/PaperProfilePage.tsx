import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, BookOpen, Check, Code2, Download, FolderInput, Globe, Network, Paperclip, Pencil, Quote, Tag as TagIcon, Trash2, X } from 'lucide-react'
import { Button, IconButton, Input, Skeleton, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { textApi } from '@/lib/api.text'
import { useBack } from '@/lib/useBack'
import { friendlyTranslateError, streamTranslate } from '@/lib/translateText'
import { SelectTranslate } from '@/components/SelectTranslate'
import { libraryApi } from '@/lib/api.library'
import { formatAuthors, formatRelativeTime } from '@/lib/format'
import { getDraft, setDraft } from '@/lib/drafts'
import { paperName } from '@/lib/paperName'
import type { Note, PaperDetail, PaperProfile } from '@/lib/types'
import type { FolderItem } from '@/lib/types.library'
import { useBlobUrl } from '@/features/figures/useBlobUrl'
import { VenueIntro } from '@/features/discovery/VenueIntro'
import { CiteDialog } from '@/features/library/CiteDialog'
import { NoteBody } from '@/features/notes/NoteBody'
import { Clamp } from '@/features/notes/Clamp'
import { NewStickyTile, StickyEditor } from '@/features/notes/StickyEditor'
import { FolderQuickMenu } from '@/features/library/FolderQuickMenu'
import { TagDialog } from '@/features/library/TagDialog'
import { VenueHover } from '@/features/discovery/VenueHover'
import { folderPath } from '@/features/library/filterModel'
import { attachLocalPdf, autoFetchFullText, downloadInBrowser } from '@/features/importing/fulltext'
import { isActiveJob } from '@/lib/format'
import { useJobsStore } from '@/stores/jobsStore'
import './paperProfile.css'

/** 同一篇论文在一次运行里只自动找一次全文，找不到也不反复重试 */
const autoTried = new Set<string>()

const STATUS_ZH: Record<string, string> = { ready: '', parsing: '解析中', metadata_only: '仅元数据', failed: '解析失败' }
const NOTES_SHOWN = 5

function Cover({ paper }: { paper: PaperDetail }) {
  // 封面用第一页的矢量图，放大不糊；导不出来（太复杂等）才退回缩略图
  const { url } = useBlobUrl(
    paper.has_pdf && paper.status !== 'parsing' ? () => textApi.regionSvg(paper.id, 1, [0, 0, 2000, 3000]).catch(() => api.papers.cover(paper.id)) : null,
    [paper.id, paper.status]
  )
  return (
    <div className="pd-pp-cover">
      {url ? (
        <img src={url} alt="第一页缩略图" />
      ) : (
        <div className="pd-pp-cover-blank" aria-hidden="true">
          {paperName(paper)}
        </div>
      )}
    </div>
  )
}

function Stat({ n, label }: { n: number | string; label: string }) {
  return (
    <div className="pd-pp-stat">
      <div className="pd-pp-stat-n">{n}</div>
      <div className="pd-pp-stat-l">{label}</div>
    </div>
  )
}

/** 论文详情页：像影视作品的详情页，开始读之前先看看这是什么、读到哪了、里面有什么，以及我自己的笔记和和 AI 的对话。数字都是这篇论文已有的数据。 */
export function PaperProfilePage() {
  const { paperId = '' } = useParams()
  const navigate = useNavigate()
  const back = useBack('/library')
  const [paper, setPaper] = useState<PaperDetail | null>(null)
  const [profile, setProfile] = useState<PaperProfile | null>(null)
  const [notes, setNotes] = useState<Note[]>([])
  const [folders, setFolders] = useState<FolderItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const [citing, setCiting] = useState(false)
  const [tagging, setTagging] = useState(false)
  const [fullAbstract, setFullAbstract] = useState(false)
  const [editingAlias, setEditingAlias] = useState(false)
  const [aliasDraft, setAliasDraft] = useState('')
  const [allNotes, setAllNotes] = useState(false)
  const [noteDraft, setNoteDraftRaw] = useState('')
  const [composing, setComposing] = useState(false)
  // 没写完的便签存在本机：切到别的页面再回来还在
  const setNoteDraft = useCallback(
    (v: string): void => {
      setNoteDraftRaw(v)
      setDraft('profile', paperId, v)
    },
    [paperId]
  )
  useEffect(() => {
    const d = getDraft<string>('profile', paperId)
    setNoteDraftRaw(d ?? '')
    setComposing(!!d)
  }, [paperId])
  const [editingNote, setEditingNote] = useState<{ id: string; text: string } | null>(null)
  const [savingNote, setSavingNote] = useState(false)
  const [tr, setTr] = useState<{ status: 'idle' | 'streaming' | 'done' | 'error'; text: string; error?: string }>({ status: 'idle', text: '' })
  const trCtl = useRef<AbortController | null>(null)

  /** 翻译摘要：流式显示，同一段后端有缓存，不会重复发送。 */
  const translateAbstract = async (text: string): Promise<void> => {
    trCtl.current?.abort()
    const ctl = new AbortController()
    trCtl.current = ctl
    setTr({ status: 'streaming', text: '' })
    await streamTranslate(
      paperId,
      text.slice(0, 8000),
      {
        onDelta: (d) => setTr((x) => ({ ...x, status: 'streaming', text: x.text + d })),
        onDone: (r) => setTr({ status: 'done', text: r.translation }),
        onError: (e) => setTr({ status: 'error', text: '', error: friendlyTranslateError(e) })
      },
      ctl.signal
    )
  }

  // 以前翻译过摘要的话，打开页面就直接显示，不用再点、也不再调用模型
  useEffect(() => {
    const raw = (paper?.abstract ?? '').replace(/^\s*abstract[\s:.—-]*/i, '').trim()
    if (!raw) return
    let alive = true
    api.ai
      .lookupTranslation(paperId, raw.slice(0, 8000))
      .then((r) => {
        if (alive && r.translation) setTr((x) => (x.status === 'idle' ? { status: 'done', text: r.translation as string } : x))
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [paperId, paper?.abstract])

  const reloadPaper = useCallback(() => api.papers.get(paperId).then(setPaper), [paperId])

  // 补全文的任务（自动获取 / 附加 PDF）：进行中时按钮置灰；结束后重新读取论文，状态从“仅元数据”变成可阅读
  const fulltextBusy = useJobsStore((s) => s.jobs.some((j) => j.paper_id === paperId && ['fetch_fulltext', 'attach_pdf'].includes(j.type) && isActiveJob(j.status)))
  const finishedTick = useJobsStore((s) => s.finishedTick)
  useEffect(() => {
    if (finishedTick > 0) void reloadPaper().catch(() => undefined)
  }, [finishedTick, reloadPaper])

  // 只有题录、但能确定 arXiv 编号：打开页面就直接去拿全文，不用再点
  useEffect(() => {
    if (!paper || paper.status !== 'metadata_only' || paper.has_pdf || fulltextBusy || autoTried.has(paper.id)) return
    const arxiv = !!paper.arxiv_id || /^10\.48550\/arxiv\./i.test(paper.doi ?? '')
    if (!arxiv) return
    autoTried.add(paper.id)
    void autoFetchFullText(paper.id)
  }, [paper, fulltextBusy])

  useEffect(() => {
    let alive = true
    setPaper(null)
    setProfile(null)
    setNotes([])
    setError(null)
    setEditingAlias(false)
    trCtl.current?.abort()
    setTr({ status: 'idle', text: '' })
    api.papers
      .get(paperId)
      .then((d) => alive && setPaper(d))
      .catch((e) => alive && setError(errorMessage(e)))
    api.papers
      .profile(paperId)
      .then((d) => alive && setProfile(d))
      .catch(() => undefined)
    api.notes
      .list(paperId)
      .then((d) => alive && setNotes(d.items))
      .catch(() => undefined)
    libraryApi
      .folders()
      .then((f) => alive && setFolders(f.items))
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [paperId])

  const saveAlias = async (): Promise<void> => {
    try {
      const d = await api.papers.patch(paperId, { alias: aliasDraft.trim() })
      setPaper(d)
      setEditingAlias(false)
    } catch (e) {
      toast.error('没能保存备注名', errorMessage(e))
    }
  }

  const moveTo = async (folderId: string | null): Promise<void> => {
    try {
      await libraryApi.movePapers([paperId], folderId)
      await reloadPaper()
      libraryApi.folders().then((f) => setFolders(f.items)).catch(() => undefined)
      toast.success(folderId ? `已放进“${folderPath(folders, folderId)}”` : '已移出文件夹')
    } catch (e) {
      toast.error('移动失败', errorMessage(e))
    }
  }

  const createAndMove = async (name: string): Promise<void> => {
    try {
      const f = await libraryApi.createFolder(name, null)
      await moveTo(f.id)
    } catch (e) {
      toast.error('无法新建文件夹', errorMessage(e))
    }
  }

  const addNote = async (): Promise<void> => {
    const text = noteDraft.trim()
    if (!text) return
    setSavingNote(true)
    try {
      const n = await api.notes.create(paperId, { text })
      setNotes((x) => [n, ...x])
      setNoteDraft('')
      setComposing(false)
      setProfile((p) => (p ? { ...p, notes: p.notes + 1 } : p))
    } catch (e) {
      toast.error('没能保存笔记', errorMessage(e))
    } finally {
      setSavingNote(false)
    }
  }

  const saveEdit = async (): Promise<void> => {
    if (!editingNote || !editingNote.text.trim()) return
    setSavingNote(true)
    try {
      const n = await api.notes.update(editingNote.id, editingNote.text.trim())
      setNotes((x) => x.map((y) => (y.id === n.id ? n : y)))
      setEditingNote(null)
    } catch (e) {
      toast.error('保存失败', errorMessage(e))
    } finally {
      setSavingNote(false)
    }
  }

  const removeNote = async (id: string): Promise<void> => {
    try {
      await api.notes.remove(id)
      setNotes((x) => x.filter((n) => n.id !== id))
      setProfile((p) => (p ? { ...p, notes: Math.max(0, p.notes - 1) } : p))
    } catch (e) {
      toast.error('没能删除笔记', errorMessage(e))
    }
  }

  if (error) {
    return (
      <div className="pd-page">
        <div className="pd-pp">
          <Button icon={<ArrowLeft size={14} />} onClick={back}>
            回到文献库
          </Button>
          <p className="pd-status-text pd-status-text--danger" style={{ marginTop: 16 }}>
            {error}
          </p>
        </div>
      </div>
    )
  }
  if (!paper) {
    return (
      <div className="pd-page">
        <div className="pd-pp">
          <Skeleton height={220} />
        </div>
      </div>
    )
  }

  const progress = Math.round(Math.max(0, Math.min(1, paper.read_progress)) * 100)
  const canRead = paper.status === 'ready'
  // 有的来源给的摘要开头带着 “Abstract” 字样，显示时去掉
  const abstract = (paper.abstract ?? '').replace(/^\s*abstract[\s:.—-]*/i, '').trim()
  const long = abstract.length > 700
  const startLabel = progress > 0 && paper.reading_page > 1 ? `继续阅读 · 第 ${paper.reading_page} 页` : '开始阅读'
  const folderName = paper.folder_id ? folderPath(folders, paper.folder_id) : ''
  const shownNotes = allNotes ? notes : notes.slice(0, NOTES_SHOWN)

  return (
    <div className="pd-page">
      <div className="pd-pp">
        <button type="button" className="pd-pp-back" onClick={back}>
          <ArrowLeft size={14} /> 文献库
        </button>

        <header className="pd-pp-hero">
          <Cover paper={paper} />
          <div className="pd-pp-head">
            <div className="pd-pp-chips">
              {paper.year && <span>{paper.year}</span>}
              {paper.venue && (
                <span>
                  <VenueHover name={paper.venue}>{paper.venue}</VenueHover>
                </span>
              )}
              {STATUS_ZH[paper.status] && <span>{STATUS_ZH[paper.status]}</span>}
              {(paper.tags ?? []).map((t) => (
                <span key={t.id}>{t.name}</span>
              ))}
            </div>

            {editingAlias ? (
              <div className="pd-pp-alias-edit">
                <Input value={aliasDraft} onChange={setAliasDraft} onEnter={() => void saveAlias()} placeholder="给这篇论文起个好记的名字，留空就用原标题" aria-label="备注名" autoFocus />
                <IconButton icon={<Check size={16} />} label="保存" onClick={() => void saveAlias()} />
                <IconButton icon={<X size={16} />} label="取消" onClick={() => setEditingAlias(false)} />
              </div>
            ) : (
              <h1 className="pd-pp-title">
                {paperName(paper)}
                <button
                  type="button"
                  className="pd-pp-alias-btn"
                  aria-label="改备注名"
                  title="给这篇论文起个名字"
                  onClick={() => {
                    setAliasDraft(paper.alias ?? '')
                    setEditingAlias(true)
                  }}
                >
                  <Pencil size={14} />
                </button>
              </h1>
            )}
            {paper.alias && !editingAlias && <p className="pd-pp-original">原标题：{paper.title}</p>}
            <p className="pd-pp-authors">{formatAuthors(paper.authors, 8)}</p>
            {paper.doi && <p className="pd-pp-doi">DOI {paper.doi}</p>}
            <p className="pd-pp-folder">
              <FolderQuickMenu
                currentFolderId={paper.folder_id ?? null}
                folders={folders}
                onMove={(fid) => void moveTo(fid)}
                onCreate={createAndMove}
                align="start"
                trigger={({ toggle }) => (
                  <button type="button" className="pd-pp-folder-btn" onClick={toggle}>
                    <FolderInput size={14} /> {folderName || '未分类'} · 放进文件夹
                  </button>
                )}
              />
            </p>
            <div className="pd-pp-tags">
              {(paper.tags ?? []).map((t) => (
                <span key={t.id} className="pd-ptag">
                  {t.name}
                </span>
              ))}
              <button type="button" className="pd-pp-folder-btn" onClick={() => setTagging(true)}>
                <TagIcon size={14} /> {(paper.tags ?? []).length ? '编辑标签' : '添加标签'}
              </button>
            </div>
            <VenueIntro name={paper.venue} />
          </div>
        </header>

        <div className="pd-pp-actions">
          <Button variant="primary" icon={<BookOpen size={15} />} disabled={!canRead} onClick={() => navigate(`/reader/${paper.id}`)}>
            {canRead ? startLabel : paper.status === 'metadata_only' ? '还没有全文' : '暂时不能阅读'}
          </Button>
          <Button icon={<Network size={15} />} disabled={!canRead} onClick={() => navigate(`/mindmap/${paper.id}`)}>
            思维导图
          </Button>
          <Button icon={<Code2 size={15} />} disabled={paper.status === 'parsing'} onClick={() => navigate(`/code/${paper.id}`)}>
            代码工作区
          </Button>
          <Button icon={<Quote size={15} />} onClick={() => setCiting(true)}>
            生成引用
          </Button>
        </div>
        {paper.status === 'metadata_only' && !paper.has_pdf && (
          <div className="pd-pp-getfull" role="group" aria-label="补充全文">
            <p className="pd-aux pd-muted" style={{ margin: 0 }}>
              这篇论文目前只有题录，没有 PDF 全文。可以让软件去找开放获取的版本，或者自己补上：
            </p>
            <div className="pd-row" style={{ flexWrap: 'wrap', marginTop: 8 }}>
              <Button size="sm" variant="primary" icon={<Download size={14} />} loading={fulltextBusy} onClick={() => void autoFetchFullText(paper.id)}>
                自动获取全文
              </Button>
              <Button size="sm" icon={<Globe size={14} />} disabled={fulltextBusy} onClick={() => void downloadInBrowser(paper)} title="在内置浏览器里打开：需要登录就直接登录，下载到的 PDF 会自动附加到这篇论文">
                在浏览器里下载（可登录）
              </Button>
              <Button size="sm" icon={<Paperclip size={14} />} disabled={fulltextBusy} onClick={() => void attachLocalPdf(paper.id)}>
                选择本地 PDF
              </Button>
            </div>
          </div>
        )}
        {!canRead && paper.parse_error && <p className="pd-aux pd-muted">{paper.parse_error}</p>}
        <div className="pd-pp-bar" aria-label={`读到 ${progress}%`}>
          <div style={{ width: `${progress}%` }} />
        </div>
        <p className="pd-aux pd-muted pd-pp-barline">
          {progress > 0 ? `读到 ${progress}%` : '还没开始读'}
          {paper.last_opened_at ? ` · 上次打开 ${formatRelativeTime(paper.last_opened_at)}` : ''}
          {` · 加入于 ${formatRelativeTime(paper.imported_at)}`}
        </p>

        <div className="pd-pp-stats">
          <Stat n={profile?.pages ?? paper.page_count ?? '—'} label="页" />
          <Stat n={profile ? profile.figures.length : '—'} label="图" />
          <Stat n={profile ? profile.tables.length : '—'} label="表" />
          <Stat n={profile ? profile.equations : '—'} label="公式" />
          <Stat n={profile?.references ?? '—'} label="参考文献" />
          <Stat n={profile ? profile.notes : '—'} label="笔记" />
          <Stat n={profile ? profile.annotations : '—'} label="标注" />
        </div>

        <section className="pd-pp-sec">
          <h2>我的笔记</h2>
          <div className="pd-sticky-grid">
            {composing ? (
              <StickyEditor
                value={noteDraft}
                onChange={setNoteDraft}
                onSave={() => void addNote()}
                onCancel={() => {
                  setComposing(false)
                  setNoteDraft('')
                }}
                saving={savingNote}
                saveLabel="贴上"
              />
            ) : (
              <NewStickyTile onClick={() => setComposing(true)} />
            )}
            {shownNotes.map((n) =>
              editingNote?.id === n.id ? (
                <StickyEditor
                  key={n.id}
                  value={editingNote.text}
                  onChange={(v) => setEditingNote({ id: n.id, text: v })}
                  onSave={() => void saveEdit()}
                  onCancel={() => setEditingNote(null)}
                  saving={savingNote}
                  header={n.quote ? <blockquote className="pd-pp-quote">{n.quote}</blockquote> : undefined}
                />
              ) : (
                <article key={n.id} className="pd-sticky">
                  {n.quote && (
                    <Clamp lines={3}>
                      <blockquote className="pd-pp-quote">{n.quote}</blockquote>
                    </Clamp>
                  )}
                  {n.text && (
                    <Clamp lines={8}>
                      <NoteBody text={n.text} />
                    </Clamp>
                  )}
                  <div className="pd-sticky-meta">
                    <span className="pd-muted">
                      {n.page ? `第 ${n.page} 页 · ` : ''}
                      {formatRelativeTime(n.created_at)}
                    </span>
                    <span>
                      <IconButton icon={<Pencil size={14} />} label="编辑这条笔记" onClick={() => setEditingNote({ id: n.id, text: n.text })} />
                      <IconButton icon={<Trash2 size={14} />} label="删除这条笔记" onClick={() => void removeNote(n.id)} />
                    </span>
                  </div>
                </article>
              )
            )}
          </div>
          {notes.length > NOTES_SHOWN && (
            <button type="button" className="pd-dc-more" onClick={() => setAllNotes((v) => !v)}>
              {allNotes ? '收起' : `显示全部 ${notes.length} 条`}
            </button>
          )}
        </section>

        {abstract && (
          <section className="pd-pp-sec">
            <h2>摘要</h2>
            <p className={`pd-pp-abstract${long && !fullAbstract ? ' is-clamped' : ''}`} data-translatable>
              {abstract}
            </p>
            <div className="pd-pp-abs-actions">
              {long && (
                <button type="button" className="pd-dc-more" onClick={() => setFullAbstract((v) => !v)}>
                  {fullAbstract ? '收起' : '展开全文'}
                </button>
              )}
              <button type="button" className="pd-dc-more" disabled={tr.status === 'streaming'} onClick={() => void translateAbstract(abstract)}>
                {tr.status === 'streaming' ? '正在翻译…' : tr.status === 'done' ? '重新翻译' : '翻译摘要'}
              </button>
              {tr.status !== 'idle' && tr.status !== 'streaming' && (
                <button type="button" className="pd-dc-more" onClick={() => setTr({ status: 'idle', text: '' })}>
                  收起译文
                </button>
              )}
            </div>
            {tr.status === 'error' && <p className="pd-status-text pd-status-text--danger">{tr.error}</p>}
            {(tr.text || tr.status === 'streaming') && <p className="pd-pp-abstract pd-pp-abstract--tr">{tr.text || '…'}</p>}
            <SelectTranslate paperId={null} standalone contextTitle={paper.title} />
            {profile && profile.keywords.length > 0 && <p className="pd-aux pd-muted">关键词：{profile.keywords.join('、')}</p>}
            {profile && (profile.quality_notes?.length ?? 0) > 0 && <p className="pd-aux pd-muted">解析提示：{profile.quality_notes?.join('；')}。可以对照原文，或重新解析。</p>}
          </section>
        )}

        {profile && profile.outline.length > 0 && (
          <section className="pd-pp-sec">
            <h2>目录</h2>
            <ul className="pd-pp-outline">
              {profile.outline.map((o, i) => (
                <li key={i} style={{ paddingLeft: (o.level - 1) * 16 }}>
                  <span>{o.title}</span>
                  <span className="pd-muted">{o.page}</span>
                </li>
              ))}
            </ul>
          </section>
        )}

        <CiteDialog paperId={citing ? paper.id : null} title={paper.title} onClose={() => setCiting(false)} />
        <TagDialog paper={tagging ? paper : null} onClose={() => setTagging(false)} onChange={(_id, tags) => setPaper((p) => (p ? { ...p, tags } : p))} />
      </div>
    </div>
  )
}
