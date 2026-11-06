import { useEffect, useRef, useState } from 'react'
import { ArrowUp, AtSign, Globe, Paperclip, Square, X } from 'lucide-react'
import { IconButton, Tooltip, toast } from '@/components/ui'
import { ACCEPT, readAttachments, type Attach } from './attach'
import { truncate } from '@/lib/format'
import type { InsightCard } from '@/lib/types'
import { useReaderStore } from '@/features/reader/readerStore'
import { askQuick, type QuickIntent } from '@/features/reader/selectionActions'
import type { SelectionInfo } from '@/features/reader/pdf/selection'
import { usePrefs } from '@/stores/prefsStore'
import { getDraft, setDraft } from '@/lib/drafts'
import { isFigureCard } from '@/lib/types.vision'
import { lastCardId, useInsightStore } from './insightStore'

const QUICK: Array<{ intent: QuickIntent; label: string; hint: string }> = [
  { intent: 'explain', label: '解释', hint: '解释选中的内容' },
  { intent: 'summarize', label: '总结', hint: '总结选中的内容' },
  { intent: 'term', label: '术语', hint: '解释选中内容里的术语' },
  { intent: 'motivation', label: '设计动机', hint: '分析作者这样设计的动机' }
]

/** 输入框最多长到 6 行（14px 字号、1.6 行高）。 */
const MAX_INPUT_PX = 136

/** 把被引用的卡片以文字形式附在问题后面（契约里 /ask 没有单独的“引用卡片”字段）。 */
export function composeQuestion(text: string, refs: InsightCard[]): string {
  if (refs.length === 0) return text.trim()
  const lines = refs.map((c) => `- 问题：${truncate(c.question || c.object_label, 120)}\n  结论：${truncate(c.answer.direct, 360)}`)
  return `${text.trim()}\n\n（参考此前的研究卡片）\n${lines.join('\n')}`
}

export function Composer() {
  const contextSelection = useReaderStore((s) => s.contextSelection)
  const setContextSelection = useReaderStore((s) => s.setContextSelection)
  const selectionLive = useReaderStore((s) => s.selection)
  const focusTick = useReaderStore((s) => s.composerFocusTick)
  const cards = useInsightStore((s) => s.cards)
  const conversation = useInsightStore((s) => s.activeConversation)
  const pending = useInsightStore((s) => s.pending)
  const ask = useInsightStore((s) => s.ask)
  const stop = useInsightStore((s) => s.stop)
  const askWeb = usePrefs((s) => s.askWeb)
  const setPrefs = usePrefs((s) => s.set)

  const paperId = useReaderStore((s) => s.paperId)
  // 没发出去的话存在本机：切到别的页面、换论文再回来、重启应用都还在
  const [text, setText] = useState(() => getDraft<string>('composer', useReaderStore.getState().paperId) ?? '')
  const draftOf = useRef<string | null>(paperId)
  const skipWrite = useRef(false)
  useEffect(() => {
    if (draftOf.current !== paperId) {
      draftOf.current = paperId
      skipWrite.current = true // 这一轮的 text 还是上一篇论文的，不能写到新论文名下
      setText(getDraft<string>('composer', paperId) ?? '')
    }
  }, [paperId])
  useEffect(() => {
    if (skipWrite.current) {
      skipWrite.current = false
      return
    }
    setDraft('composer', paperId, text)
  }, [text, paperId])
  const [refs, setRefs] = useState<InsightCard[]>([])
  // 对话里最后一张是“图解析”卡片：接着问默认还是在问这张图，会再把这张图发给视觉模型（可以点 × 取消，改成普通提问）
  const lastId = lastCardId(cards, conversation)
  const lastCard = lastId ? cards.find((c) => c.id === lastId) : undefined
  const figureCtx = lastCard && isFigureCard(lastCard) ? { cardId: lastCard.id, id: lastCard.answer.figure.id, label: lastCard.answer.figure.label ?? '这张图' } : null
  const [figureOff, setFigureOff] = useState<string | null>(null)
  const figureOn = figureCtx !== null && figureOff !== figureCtx.cardId
  const [mention, setMention] = useState(false)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const streaming = pending?.status === 'streaming'

  useEffect(() => {
    if (focusTick > 0) taRef.current?.focus()
  }, [focusTick])

  // 自动增高：默认 2 行，最多 6 行，超出后在输入框内滚动
  useEffect(() => {
    const ta = taRef.current
    if (!ta) return
    ta.style.height = 'auto'
    ta.style.height = `${Math.min(ta.scrollHeight, MAX_INPUT_PX)}px`
  }, [text])

  const [files, setFiles] = useState<Attach[]>([])
  const [dragging, setDragging] = useState(false)
  const pickRef = useRef<HTMLInputElement>(null)
  const canSend = (text.trim().length > 0 || files.length > 0) && !streaming

  const addFiles = async (list: File[]): Promise<void> => {
    if (list.length === 0) return
    const { added, errors } = await readAttachments(list, files.length)
    if (added.length > 0) setFiles((f) => [...f, ...added])
    if (errors.length > 0) toast.error('有文件没能加上', errors.join('；'))
  }

  const send = (): void => {
    if (!canSend) return
    const question = composeQuestion(text, refs) || '请看看我附上的文件，结合这篇论文回答。'
    // 提问针对选中的内容：已加入上下文的，或论文里刚选中（并保留着）的那段话
    const sel = contextSelection ?? (selectionLive ? { text: selectionLive.text, page: selectionLive.page } : null)
    void ask({
      question,
      intent: 'free',
      selection: sel,
      attachments: files.length > 0 ? files.map(({ name, mime, data }) => ({ name, mime, data })) : undefined,
      figureId: figureOn ? figureCtx.id : undefined
    })
    setText('')
    setFiles([])
    setRefs([])
    setMention(false)
  }

  const onChange = (v: string): void => {
    setText(v)
    setMention(/(^|\s)@$/.test(v))
  }

  const removeAt = (): void => setText((t) => t.replace(/@$/, ''))

  const recent = [...cards].reverse().slice(0, 8)
  // 选中的内容只在这里出现一次：已加入上下文的选区，或论文里刚选中的文字
  const target = contextSelection ?? (selectionLive ? { text: selectionLive.text, page: selectionLive.page } : null)
  const runQuick = (intent: QuickIntent): void => {
    if (!target) return
    askQuick({ text: target.text, page: target.page, rects: [], clientRect: { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 } } as unknown as SelectionInfo, intent)
  }

  return (
    <div className="pd-composer">
      {mention && (
        <div className="pd-mention" role="listbox" aria-label="引用">
          <div className="pd-mention-title">引用</div>
          <button
            type="button"
            className="pd-mention-item"
            disabled={!contextSelection && !selectionLive}
            onClick={() => {
              if (!contextSelection && selectionLive) setContextSelection({ text: selectionLive.text, page: selectionLive.page })
              removeAt()
              setMention(false)
            }}
          >
            当前选中内容{!contextSelection && !selectionLive ? ' · 没有选中' : ''}
          </button>
          {recent.length === 0 ? (
            <div className="pd-mention-empty">还没有研究卡片可引用</div>
          ) : (
            recent.map((c) => (
              <button
                key={c.id}
                type="button"
                className="pd-mention-item"
                onClick={() => {
                  setRefs((r) => (r.some((x) => x.id === c.id) ? r : [...r, c]))
                  removeAt()
                  setMention(false)
                }}
              >
                <span className="pd-chip-kind">卡片</span>
                {truncate(c.question || c.object_label, 34)}
              </button>
            ))
          )}
          <button type="button" className="pd-mention-item" disabled>
            其他论文 · 公式 · 代码文件 · 笔记 · 即将支持
          </button>
        </div>
      )}

      {(target || refs.length > 0 || figureOn) && (
        <div className="pd-composer-ctx">
          {figureOn && (
            <span className="pd-chip pd-chip--fig" title="接着问会再把这张图发给视觉模型；点 × 改成普通提问">
              <span className="pd-chip-kind">看图</span>
              <span className="pd-ellipsis">{figureCtx.label}</span>
              <button type="button" aria-label="不再带着这张图提问" onClick={() => setFigureOff(figureCtx.cardId)}>
                <X size={12} />
              </button>
            </span>
          )}
          {target && (
            <span className="pd-chip pd-chip--sel" title={target.text}>
              <span className="pd-chip-kind">第 {target.page} 页</span>
              <span className="pd-ellipsis">{truncate(target.text, 60)}</span>
              <button
                type="button"
                aria-label="移除选中内容"
                onClick={() => {
                  setContextSelection(null)
                  window.getSelection()?.removeAllRanges()
                  useReaderStore.getState().setSelection(null)
                }}
              >
                <X size={12} />
              </button>
            </span>
          )}
          {target && (
            <span className="pd-quick" role="group" aria-label="对选中内容的快捷操作">
              {QUICK.map((q) => (
                <button key={q.intent} type="button" className="pd-quick-btn" disabled={streaming} title={q.hint} onClick={() => runQuick(q.intent)}>
                  {q.label}
                </button>
              ))}
            </span>
          )}
          {refs.map((c) => (
            <span key={c.id} className="pd-chip" title={c.question}>
              <span className="pd-chip-kind">卡片</span>
              <span className="pd-ellipsis">{truncate(c.question || c.object_label, 24)}</span>
              <button type="button" aria-label="移除引用" onClick={() => setRefs((r) => r.filter((x) => x.id !== c.id))}>
                <X size={12} />
              </button>
            </span>
          ))}
        </div>
      )}

      <div
        className={`pd-composer-box${dragging ? ' is-drop' : ''}`}
        onDragOver={(e) => {
          if (e.dataTransfer.types.includes('Files')) {
            e.preventDefault()
            setDragging(true)
          }
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          setDragging(false)
          if (e.dataTransfer.files.length > 0) {
            e.preventDefault()
            void addFiles([...e.dataTransfer.files])
          }
        }}
      >
        {files.length > 0 && (
          <div className="pd-attach-row">
            {files.map((f) => (
              <span key={f.id} className="pd-attach" title={f.name}>
                {f.preview ? <img src={f.preview} alt="" /> : <Paperclip size={12} />}
                <span className="pd-ellipsis">{f.name}</span>
                <button type="button" aria-label={`移除 ${f.name}`} onClick={() => setFiles((x) => x.filter((y) => y.id !== f.id))}>
                  <X size={11} />
                </button>
              </span>
            ))}
          </div>
        )}
        <textarea
          ref={taRef}
          className="pd-composer-input"
          rows={2}
          value={text}
          placeholder="向这篇论文提问，输入 @ 引用"
          aria-label="向论文提问"
          onChange={(e) => onChange(e.target.value)}
          onPaste={(e) => {
            // 粘贴截图/文件：直接加成附件（粘贴普通文字照常）
            const fs = [...e.clipboardData.files]
            if (fs.length > 0) {
              e.preventDefault()
              void addFiles(fs)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault()
              send()
            } else if (e.key === 'Escape' && mention) {
              e.stopPropagation()
              setMention(false)
            }
          }}
        />
        <div className="pd-composer-bar">
          <IconButton icon={<AtSign size={14} />} label="引用" size="sm" onClick={() => setMention((m) => !m)} />
          <IconButton
            icon={<Globe size={14} />}
            label={askWeb ? '联网搜索：已开' : '联网搜索：已关'}
            size="sm"
            selected={askWeb}
            onClick={() => setPrefs({ askWeb: !askWeb })}
          />
          <IconButton icon={<Paperclip size={14} />} label="附件：截图、PDF、文本文件（也可以直接粘贴、拖进来）" size="sm" onClick={() => pickRef.current?.click()} />
          <input
            ref={pickRef}
            type="file"
            multiple
            hidden
            accept={ACCEPT}
            onChange={(e) => {
              void addFiles([...(e.target.files ?? [])])
              e.target.value = ''
            }}
          />
          <span className="pd-spacer" />
          {streaming ? (
            <Tooltip content="停止生成">
              <button type="button" className="pd-send pd-send--stop" onClick={stop} aria-label="停止生成">
                <Square size={12} />
              </button>
            </Tooltip>
          ) : (
            <Tooltip content={canSend ? '发送 · Enter' : '输入问题后发送'}>
              <button type="button" className="pd-send" disabled={!canSend} onClick={send} aria-label="发送">
                <ArrowUp size={14} />
              </button>
            </Tooltip>
          )}
        </div>
      </div>
    </div>
  )
}
