import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Copy, NotebookPen, RefreshCw, Trash2, X } from 'lucide-react'
import { Button, IconButton, Spinner, Tag, toast } from '@/components/ui'
import { useNotesStore } from '@/features/notes/notesStore'
import { useMarksStore } from './marksStore'
import { useReaderStore } from './readerStore'
import { useTranslateStore } from './translateStore'

/** 选区附近的翻译浮层：流式显示译文，可复制、加入笔记、重新翻译；不创建研究卡片。 */
export function TranslatePopover() {
  const navigate = useNavigate()
  const t = useTranslateStore()
  const [showSource, setShowSource] = useState(false)
  const createNote = useNotesStore((s) => s.create)

  useEffect(() => {
    if (!t.open) return
    setShowSource(false)
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') t.close()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t.open])

  if (!t.open) return null

  const configError = t.error && (t.error.code === 'no_api_key' || t.error.code === 'not_configured')
  const streaming = t.status === 'streaming'

  const copy = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(t.translation)
      toast.success('已复制译文')
    } catch {
      toast.error('复制失败')
    }
  }
  const addNote = async (): Promise<void> => {
    const note = await createNote({ text: t.translation, quote: t.source, page: t.page })
    if (note) {
      toast.success('已加入笔记')
      t.close()
    }
  }

  return (
    <div className="pd-float pd-translate" style={{ left: t.anchor.left, top: t.anchor.top, bottom: t.anchor.bottom, transform: 'translateX(-50%)' }} role="dialog" aria-label="翻译">
      <div className="pd-translate-head">
        <span style={{ fontWeight: 600 }}>翻译</span>
        {streaming && <Spinner size={12} />}
        {t.model && <span className="pd-aux pd-weak pd-ellipsis">{t.model}</span>}
        {t.cached && <Tag>缓存</Tag>}
        <span className="pd-spacer" />
        <IconButton icon={<X size={15} />} label="关闭" size="sm" onClick={t.close} />
      </div>
      <div className="pd-translate-body">
        <div className="pd-translate-src" data-open={showSource} onClick={() => setShowSource((v) => !v)} title="点击展开 / 收起原文" role="button">
          {t.source}
        </div>
        {t.truncated && <div className="pd-aux pd-weak">原文较长，只翻译了前 6000 个字符。</div>}
        {t.error ? (
          <div className="pd-field-error" style={{ fontSize: 'var(--fs-body)' }}>
            {configError ? '还没有配置 AI 模型，无法翻译。' : t.error.message}
            {configError && (
              <div style={{ marginTop: 8 }}>
                <Button size="sm" variant="primary" onClick={() => navigate('/settings/ai')}>
                  去设置 AI 模型
                </Button>
              </div>
            )}
          </div>
        ) : (
          <div className="pd-translate-text">{t.translation || (streaming ? '翻译中…' : '')}</div>
        )}
      </div>
      <div className="pd-translate-foot">
        <Button size="sm" variant="ghost" icon={<Copy size={13} />} disabled={!t.translation} onClick={() => void copy()}>
          复制
        </Button>
        <Button size="sm" variant="ghost" icon={<NotebookPen size={13} />} disabled={!t.translation || streaming} onClick={() => void addNote()}>
          加入笔记
        </Button>
        <span className="pd-spacer" />
        <Button size="sm" variant="ghost" icon={<RefreshCw size={13} />} disabled={streaming} onClick={() => void t.retranslate()}>
          重新翻译
        </Button>
      </div>
    </div>
  )
}

/** 点击已有高亮时出现的小浮层：复制文字、删除高亮。 */
export function AnnotationPopover() {
  const active = useMarksStore((s) => s.activeAnnotation)
  const annotations = useMarksStore((s) => s.annotations)
  const setActive = useMarksStore((s) => s.setActiveAnnotation)
  const remove = useMarksStore((s) => s.removeAnnotation)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setActive(null)
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [active, setActive])

  const ann = active ? annotations.find((a) => a.id === active.id) : null
  if (!active || !ann) return null
  const left = Math.min(Math.max(active.x, 100), window.innerWidth - 100)
  const top = Math.min(active.y + 12, window.innerHeight - 52)

  return (
    <div className="pd-float" style={{ left, top, transform: 'translateX(-50%)', padding: 3, display: 'flex', gap: 2 }} role="toolbar" aria-label="高亮操作" onMouseDown={(e) => e.preventDefault()}>
      <Button
        size="sm"
        variant="ghost"
        icon={<Copy size={13} />}
        onClick={() => {
          void navigator.clipboard.writeText(ann.text).then(
            () => toast.success('已复制'),
            () => toast.error('复制失败')
          )
          setActive(null)
        }}
      >
        复制文字
      </Button>
      <Button size="sm" variant="ghost" onClick={() => (jumpToPage(ann.page), setActive(null))}>
        第 {ann.page} 页
      </Button>
      <Button size="sm" variant="danger" icon={<Trash2 size={13} />} onClick={() => void remove(ann.id)}>
        删除高亮
      </Button>
    </div>
  )
}
