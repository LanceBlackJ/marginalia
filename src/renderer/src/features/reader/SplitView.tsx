import { useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { ArrowLeftRight } from 'lucide-react'
import { Button, Segmented, Switch } from '@/components/ui'
import { TextReader } from '@/features/text/TextReader'
import type { TextView } from '@/features/text/textModel'
import { usePrefs } from '@/stores/prefsStore'
import { useReaderStore } from './readerStore'

/** 并排视图：一栏是提取出的文字，一栏是 PDF 原文；中间的分隔条可拖动，左右可交换，宽度记忆在偏好里。 */
export function SplitView({ pdf, note }: { pdf: ReactNode; note?: ReactNode }) {
  const ratio = usePrefs((s) => s.splitRatio)
  const swap = usePrefs((s) => s.splitSwap)
  const sync = usePrefs((s) => s.syncScroll)
  const setPrefs = usePrefs((s) => s.set)
  const textView = useReaderStore((s) => s.splitTextView)
  const setTextView = useReaderStore((s) => s.setSplitTextView)
  const boxRef = useRef<HTMLDivElement>(null)
  const [dragging, setDragging] = useState(false)

  const onDown = (e: ReactPointerEvent<HTMLDivElement>): void => {
    e.currentTarget.setPointerCapture(e.pointerId)
    setDragging(true)
  }
  const onMove = (e: ReactPointerEvent<HTMLDivElement>): void => {
    if (!dragging || !boxRef.current) return
    const r = boxRef.current.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width
    // 分隔条位置是“左栏占比”；交换后左栏是 PDF，占比含义不变
    setPrefs({ splitRatio: Math.min(0.7, Math.max(0.3, x)) })
  }
  const onUp = (e: ReactPointerEvent<HTMLDivElement>): void => {
    setDragging(false)
    e.currentTarget.releasePointerCapture(e.pointerId)
  }

  const textPane = (
    <div className="pd-split-pane pd-split-pane--text">
      <TextReader />
    </div>
  )
  const pdfPane = <div className="pd-split-pane pd-split-pane--pdf">{pdf}</div>

  return (
    <div className="pd-split">
      {note}
      <div className="pd-split-bar">
        <Segmented<TextView>
          aria-label="文字栏的呈现"
          value={textView}
          onChange={setTextView}
          options={[
            { value: 'text', label: '文字' },
            { value: 'both', label: '对照' },
            { value: 'target', label: '译文' }
          ]}
        />
        <span className="pd-spacer" />
        <Switch checked={sync} onChange={(v) => setPrefs({ syncScroll: v })} label="同步滚动" />
        <Button size="sm" variant="ghost" icon={<ArrowLeftRight size={13} />} onClick={() => setPrefs({ splitSwap: !swap })} title="交换左右两栏">
          交换
        </Button>
      </div>
      <div className="pd-split-body" ref={boxRef} data-dragging={dragging}>
        <div className="pd-split-col" style={{ flexBasis: `${ratio * 100}%` }}>
          {swap ? pdfPane : textPane}
        </div>
        <div
          className="pd-split-handle"
          role="separator"
          aria-orientation="vertical"
          aria-label="拖动调整两栏宽度"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          onPointerCancel={onUp}
        />
        <div className="pd-split-col" style={{ flexBasis: `${(1 - ratio) * 100}%` }}>
          {swap ? textPane : pdfPane}
        </div>
      </div>
    </div>
  )
}
