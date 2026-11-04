import { useEffect, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { Button, Skeleton } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { useBack } from '@/lib/useBack'
import { ActivityCalendar } from '@/features/stats/ActivityCalendar'
import type { CountItem, LibraryStats } from '@/lib/types'
import './stats.css'

function Big({ n, label, hint }: { n: number; label: string; hint?: string }) {
  return (
    <div className="pd-st-big">
      <div className="pd-st-big-n">{n.toLocaleString()}</div>
      <div className="pd-st-big-l">{label}</div>
      {hint && <div className="pd-st-big-h">{hint}</div>}
    </div>
  )
}

/** 一组横条：名字、条、数量。条的长度按这一组里最大的数算，只是为了比较多少，不代表占比。 */
function Bars({ items, empty }: { items: CountItem[]; empty: string }) {
  if (items.length === 0) return <p className="pd-aux pd-muted">{empty}</p>
  const max = Math.max(1, ...items.map((i) => i.count))
  return (
    <ul className="pd-st-bars">
      {items.map((i) => (
        <li key={i.name}>
          <span className="pd-st-bar-name" title={i.name}>
            {i.name}
          </span>
          <span className="pd-st-bar-track" aria-hidden="true">
            <span style={{ width: `${(i.count / max) * 100}%` }} />
          </span>
          <span className="pd-st-bar-n">{i.count}</span>
        </li>
      ))}
    </ul>
  )
}

/** 全部论文的数量统计：有多少、读到哪了、哪年的多、来自哪些期刊、放在哪些文件夹。 */
export function StatsPage() {
  const back = useBack('/library')
  const [s, setS] = useState<LibraryStats | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    api.papers
      .stats()
      .then((d) => alive && setS(d))
      .catch((e) => alive && setError(errorMessage(e)))
    return () => {
      alive = false
    }
  }, [])

  if (error) {
    return (
      <div className="pd-page">
        <div className="pd-st">
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
  if (!s) {
    return (
      <div className="pd-page">
        <div className="pd-st">
          <Skeleton height={200} />
        </div>
      </div>
    )
  }

  const months = s.imported_by_month.map((m) => ({ name: m.month, count: m.count }))
  const years = s.years.map((y) => ({ name: String(y.year), count: y.count }))
  const folders = [...s.folders, ...(s.unfiled > 0 ? [{ name: '未分类', count: s.unfiled }] : [])]

  return (
    <div className="pd-page">
      <div className="pd-st">
        <button type="button" className="pd-pp-back" onClick={back}>
          <ArrowLeft size={14} /> 文献库
        </button>
        <h1 className="pd-page-title" style={{ marginTop: 16 }}>
          我的阅读统计
        </h1>
        <p className="pd-page-subtitle">全部论文的数量。补充材料跟着主论文，不单独算。</p>

        <div className="pd-st-bigs">
          <Big n={s.total} label="论文" hint={`有全文 ${s.with_pdf}${s.metadata_only ? ` · 仅元数据 ${s.metadata_only}` : ''}`} />
          <Big n={s.reading.finished} label="读完" hint="读到 90% 以上" />
          <Big n={s.reading.reading} label="在读" />
          <Big n={s.reading.unread} label="没开始" />
          <Big n={s.favorites} label="收藏" />
        </div>

        <section className="pd-st-sec">
          <h2>最近一年的活动</h2>
          <ActivityCalendar activity={s.activity} />
        </section>

        <section className="pd-st-sec">
          <h2>读到哪了</h2>
          <Bars
            items={[
              { name: '读完', count: s.reading.finished },
              { name: '在读', count: s.reading.reading },
              { name: '没开始', count: s.reading.unread }
            ]}
            empty="还没有论文。"
          />
        </section>

        <section className="pd-st-sec">
          <h2>每月导入</h2>
          <p className="pd-aux pd-muted">最近 12 个月，每月新加进文献库的论文数。</p>
          <Bars items={months} empty="" />
        </section>

        <div className="pd-st-cols">
          <section className="pd-st-sec">
            <h2>按发表年份</h2>
            <Bars items={years} empty="论文里还没有年份信息。" />
            {s.no_year > 0 && <p className="pd-aux pd-muted">另有 {s.no_year} 篇没有年份。</p>}
          </section>
          <section className="pd-st-sec">
            <h2>来源最多的期刊和会议</h2>
            <Bars items={s.venues} empty="论文里还没有来源信息。" />
            {s.no_venue > 0 && <p className="pd-aux pd-muted">另有 {s.no_venue} 篇没有来源。</p>}
          </section>
          <section className="pd-st-sec">
            <h2>文件夹</h2>
            <Bars items={folders} empty="还没有论文。" />
          </section>
          <section className="pd-st-sec">
            <h2>标签</h2>
            <Bars items={s.tags} empty="还没有给论文打标签。" />
          </section>
        </div>

        <section className="pd-st-sec">
          <h2>我留下的内容</h2>
          <div className="pd-st-bigs">
            <Big n={s.mine.notes} label="笔记" />
            <Big n={s.mine.annotations} label="标注" />
            <Big n={s.mine.bookmarks} label="书签" />
            <Big n={s.mine.ai_cards} label="保存的 AI 问答" />
          </div>
        </section>
      </div>
    </div>
  )
}
