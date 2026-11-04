import { useEffect, useState } from 'react'
import { useTextStore } from '@/features/text/textStore'

export interface HoverTarget {
  ord: number
  x: number
  y: number
}

/**
 * PDF 上悬停译文气泡：鼠标在 PDF 段落上停留约 0.5 秒后出现；已有译文就显示，没有就提示并可一键翻译这一段。
 * 只读文字栏的译文缓存，不会自动调用模型。
 */
export function HoverTranslate({ target, onClose }: { target: HoverTarget | null; onClose: () => void }) {
  const text = useTextStore((s) => (target ? s.trans.get(target.ord) : undefined))
  const retranslate = useTextStore((s) => s.retranslate)
  const [busy, setBusy] = useState(false)

  useEffect(() => setBusy(false), [target?.ord])
  if (!target) return null
  const left = Math.min(target.x + 14, window.innerWidth - 360)
  const top = Math.min(target.y + 18, window.innerHeight - 160)
  return (
    <div className="pd-hovertr" style={{ left, top }} role="tooltip" onMouseLeave={onClose}>
      {text ? (
        <>
          <div className="pd-hovertr-tag">译文</div>
          <p>{text}</p>
        </>
      ) : (
        <>
          <div className="pd-hovertr-tag">尚未翻译</div>
          <button
            type="button"
            className="pd-tp-link"
            disabled={busy}
            onClick={() => {
              setBusy(true)
              void retranslate(target.ord).finally(() => setBusy(false))
            }}
          >
            {busy ? '正在翻译这一段…' : '点此翻译这一段'}
          </button>
        </>
      )}
    </div>
  )
}
