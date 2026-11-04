import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useNavigate } from 'react-router-dom'
import { openExternal } from '@/lib/native'
import type { VenueIntro } from '@/lib/types.discovery'
import { useVenueInfo } from './venueInfo'

const OPEN_DELAY = 350
const CLOSE_DELAY = 150
const WIDTH = 360

/** 介绍的内容：名单里的简介（有才有）、只用统计数字写的中文说明、几个数字、主页。查不到的不显示，不编造影响因子或分区。 */
export function VenueCardBody({ info, loading, hideBadge = false }: { info: VenueIntro | null; loading: boolean; hideBadge?: boolean }) {
  if (loading && !info) return <p className="pd-dc-venuecard-dim">正在查询这个来源的介绍……</p>
  if (!info) return null
  const st = info.stats
  return (
    <>
      {info.listed && !hideBadge && (
        <p className="pd-dc-venuecard-badge">
          <strong>{info.listed.label}</strong> · {info.listed.category}
        </p>
      )}
      {info.listed?.intro && <p>{info.listed.intro}</p>}
      {info.listed && !info.listed.intro && (
        <p>
          收录在{info.listed.category}名单里（{info.listed.short}）。
        </p>
      )}
      {st && info.summary && <p>{info.summary}</p>}
      {st && st.mean_citedness_2y !== null && (
        <p className="pd-dc-venuecard-dim">近两年平均被引 {st.mean_citedness_2y.toFixed(2)}（和影响因子口径相近，不是官方影响因子）</p>
      )}
      {st && st.in_doaj && <p className="pd-dc-venuecard-dim">已被 DOAJ 收录（开放获取期刊目录）</p>}
      {st && st.homepage && (
        <p className="pd-dc-venuecard-dim">
          主页：
          <a
            href={st.homepage}
            onClick={(e) => {
              e.preventDefault()
              void openExternal(st.homepage as string)
            }}
          >
            {st.homepage.replace(/^https?:\/\//, '').slice(0, 48)}
          </a>
        </p>
      )}
      {!info.listed && !st && <p className="pd-dc-venuecard-dim">{info.note ?? '没有找到这个来源的介绍。'}</p>}
      {!info.listed && st && (
        <p className="pd-dc-venuecard-dim">不在收录的顶刊顶会名单里，上面只是数据库的统计数字；是否权威请结合所在领域判断。影响因子、分区、CCF 等级请到官方榜单核对。</p>
      )}
    </>
  )
}

/** 期刊/会议名：鼠标停上去（或键盘聚焦）一会儿，浮出这个来源的介绍。 */
export function VenueHover({ name, type, children }: { name: string; type?: string | null; children: ReactNode }) {
  const [open, setOpen] = useState(false)
  const [rect, setRect] = useState<DOMRect | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const { info, loading } = useVenueInfo(name, type, open)
  const navigate = useNavigate()
  const pageUrl = `/venue?name=${encodeURIComponent(name)}${type ? `&type=${encodeURIComponent(type)}` : ''}${info?.listed ? `&id=${encodeURIComponent(info.listed.id)}` : ''}`

  const clear = useCallback((): void => {
    if (openTimer.current) clearTimeout(openTimer.current)
    if (closeTimer.current) clearTimeout(closeTimer.current)
  }, [])
  const show = useCallback((): void => {
    clear()
    openTimer.current = setTimeout(() => {
      if (ref.current) setRect(ref.current.getBoundingClientRect())
      setOpen(true)
    }, OPEN_DELAY)
  }, [clear])
  const hide = useCallback((): void => {
    clear()
    closeTimer.current = setTimeout(() => setOpen(false), CLOSE_DELAY)
  }, [clear])
  const keep = (): void => {
    if (closeTimer.current) clearTimeout(closeTimer.current)
  }
  useEffect(() => clear, [clear])
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  let style: CSSProperties | undefined
  if (open && rect) {
    const left = Math.max(8, Math.min(rect.left, window.innerWidth - WIDTH - 8))
    const below = rect.bottom + 6
    style = below + 220 < window.innerHeight ? { left, top: below, width: WIDTH } : { left, bottom: window.innerHeight - rect.top + 6, width: WIDTH }
  }
  return (
    <>
      <span
        ref={ref}
        className="pd-dc-venuehover"
        tabIndex={0}
        role="link"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
        onClick={() => navigate(pageUrl)}
        onKeyDown={(e) => e.key === 'Enter' && navigate(pageUrl)}
      >
        {children}
      </span>
      {open &&
        style &&
        createPortal(
          <div className="pd-dc-venuecard" role="tooltip" style={style} onMouseEnter={keep} onMouseLeave={hide}>
            <h4>{info?.stats?.name ?? name}</h4>
            <VenueCardBody info={info} loading={loading} />
            <p className="pd-dc-venuecard-go">
              <a
                href={pageUrl}
                onClick={(e) => {
                  e.preventDefault()
                  setOpen(false)
                  navigate(pageUrl)
                }}
              >
                打开这个来源的页面 →
              </a>
            </p>
          </div>,
          document.body
        )}
    </>
  )
}
