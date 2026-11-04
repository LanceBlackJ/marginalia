import { useEffect, useRef, useState } from 'react'
import { Dialog, Skeleton } from '@/components/ui'
import { Markdown } from '@/components/Markdown'
import { errorMessage } from '@/lib/api'
import { visionApi } from '@/lib/api.vision'
import type { FigureAnswer } from '@/lib/types.vision'
import type { FigureRegion } from '@/lib/api.text'

const DEFAULT_Q = '请解析这张图：它画的是什么、关键的数据或趋势是什么、想说明什么结论。'

interface Turn {
  q: string
  loading: boolean
  error: string | null
  res: FigureAnswer | null
}

/** 让 AI 看图并解析：用视觉模型直接看这张图（不是只读图注），能接着追问。图上读不出的会明说，估读的数字单独标出。 */
export function FigAiDialog({ paperId, fig, onClose }: { paperId: string; fig: FigureRegion; onClose: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [q, setQ] = useState('')
  const started = useRef(false)
  const busy = turns.some((t) => t.loading)

  const ask = (question: string): void => {
    setTurns((ts) => [...ts, { q: question, loading: true, error: null, res: null }])
    const idx = turns.length
    visionApi
      .askFigure(paperId, fig.id, question)
      .then((res) => setTurns((ts) => ts.map((t, i) => (i === idx ? { ...t, loading: false, res } : t))))
      .catch((e) => setTurns((ts) => ts.map((t, i) => (i === idx ? { ...t, loading: false, error: errorMessage(e) } : t))))
  }

  useEffect(() => {
    if (started.current) return
    started.current = true
    ask(DEFAULT_Q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Dialog open onClose={onClose} title={`AI 解析 · ${fig.label ?? '图'}`} width={720}>
      <div className="pd-figai">
        {turns.map((t, i) => (
          <section key={i} className="pd-figai-turn">
            {i > 0 && <p className="pd-figai-q">{t.q}</p>}
            {t.loading ? (
              <Skeleton height={64} />
            ) : t.error ? (
              <p className="pd-field-error">{t.error}</p>
            ) : t.res ? (
              <>
                <Markdown text={t.res.answer || '（没有得到回答）'} />
                {t.res.seen && (
                  <p className="pd-aux pd-muted">
                    AI 看到的：{t.res.seen}
                  </p>
                )}
                {t.res.numbers.length > 0 && (
                  <ul className="pd-figai-nums">
                    {t.res.numbers.map((n, j) => (
                      <li key={j}>
                        {n.value} <span className="pd-aux pd-muted">{n.how === 'printed' ? '图上印的' : '估读的'}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {t.res.warnings.map((w) => (
                  <p key={w} className="pd-aux pd-muted">
                    {w}
                  </p>
                ))}
              </>
            ) : null}
          </section>
        ))}
        <form
          className="pd-figai-form"
          onSubmit={(e) => {
            e.preventDefault()
            const v = q.trim()
            if (!v || busy) return
            setQ('')
            ask(v)
          }}
        >
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="接着问这张图…" aria-label="追问这张图" />
          <button type="submit" disabled={!q.trim() || busy}>
            发送
          </button>
        </form>
      </div>
    </Dialog>
  )
}
