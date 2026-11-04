import { Check, ExternalLink, GitCompare, Info, Star } from 'lucide-react'
import { Button, IconButton, Tag } from '@/components/ui'
import type { Work } from '@/lib/types.discovery'
import { useWorkActions } from './useWorkActions'
import { VenueHover } from './VenueHover'
import { authorsLine, impactText, venueLine } from './workUtils'
import { useWorkZh } from './workZh'

function StatusTag({ work }: { work: Work }) {
  return <Tag title={work.status_evidence || undefined}>{work.status_label}</Tag>
}

/**
 * 论文卡片：发现、推荐、随机探索、收藏共用，同一结构、同一尺寸网格。
 * 标题（两行截断）→ 作者 → 来源与年份 + 标记 → 摘要（三行截断）→ 理由（一行）→ 操作。
 * 完整摘要、DOI、各来源与版本在“详情”抽屉里看，卡片上不再展开，保证网格里每张卡片等高对齐。
 */
export function WorkItem({ work, onOpen, showAbstract = true }: { work: Work; onOpen: (w: Work) => void; showAbstract?: boolean }) {
  const a = useWorkActions(work)
  const zh = useWorkZh(work, { abstract: showAbstract })
  const impact = impactText(work)
  const top = work.top_venue

  return (
    <article className="pd-dc-item">
      <h3 className="pd-dc-title">
        <button type="button" onClick={() => onOpen(work)} title="查看详情">
          {work.title}
        </button>
      </h3>
      {zh.title && (
        <p className="pd-dc-title-zh" title={zh.title}>
          {zh.title}
        </p>
      )}
      <p className="pd-dc-byline" title={work.authors.join('、')}>
        {authorsLine(work.authors)}
      </p>
      <div className="pd-dc-meta">
        {venueLine(work) && (
          <span className="pd-dc-venue">
            {work.venue ? (
              <VenueHover name={work.venue} type={work.venue_type}>
                {work.venue}
              </VenueHover>
            ) : null}
            {work.venue && work.year ? ' · ' : ''}
            {work.year ? String(work.year) : ''}
          </span>
        )}
        {top && <Tag title={`${top.name} · ${top.category}`}>{top.label}</Tag>}
        {work.kind_label && (
          <Tag tone={work.kind === 'review' ? 'success' : undefined} title={`类型判断依据：${work.kind_evidence}`}>
            {work.kind_label}
          </Tag>
        )}
        <StatusTag work={work} />
        {work.versions.some((v) => v.kind === 'preprint') && work.status === 'published' && <Tag title="同一研究另有预印本版本">含预印本版本</Tag>}
        {work.in_library && <Tag tone="success">已在文献库</Tag>}
      </div>
      {impact && <p className="pd-dc-sub">{impact}</p>}
      {showAbstract && work.abstract && (
        <p className="pd-dc-abstract" title={zh.abstract ? `英文原文：${work.abstract}` : undefined}>
          {zh.abstract ?? work.abstract}
        </p>
      )}
      {work.reason ? (
        <p className="pd-dc-reason" title={work.reason}>
          <span className="pd-dc-reason-label">{work.reason_source === 'ai' ? '推荐理由 · AI' : '入选依据'}</span>
          {work.reason}
        </p>
      ) : work.related_library ? (
        <p className="pd-dc-reason" title={work.related_library.title}>
          <span className="pd-dc-reason-label">相关</span>与文献库《{work.related_library.title.slice(0, 40)}》相关
        </p>
      ) : null}
      {!work.in_library && !a.canImport && <p className="pd-dc-noimport">{a.importHint}</p>}
      <div className="pd-dc-actions">
        {work.in_library ? (
          <Button size="sm" variant="secondary" icon={<Check size={14} />} onClick={a.openLibrary}>
            在文献库中打开
          </Button>
        ) : (
          <Button size="sm" variant="primary" loading={a.importing} disabled={!a.canImport} title={a.importHint || undefined} onClick={() => void a.addToLibrary()}>
            {a.importing ? '导入中…' : '加入文献库'}
          </Button>
        )}
        {!work.in_library && a.canImport && (
          <Button size="sm" variant="ghost" disabled={a.importing} title="只保存题录，不下载全文；之后可以在论文页面“附加 PDF”" onClick={() => void a.addMetadataOnly()}>
            仅保存元数据
          </Button>
        )}
        <Button size="sm" variant="ghost" icon={<ExternalLink size={14} />} disabled={!a.hasOriginal} onClick={a.openOriginal}>
          打开原文
        </Button>
        <span className="pd-dc-actions-end">
          <IconButton icon={<GitCompare size={15} />} label={a.inBasket ? '已在比较篮' : '加入比较篮'} size="sm" selected={a.inBasket} onClick={a.toggleBasket} />
          <IconButton icon={<Info size={15} />} label="详情 / 查找代码" size="sm" onClick={() => onOpen(work)} />
          <IconButton
            icon={<Star size={15} fill={a.saved ? 'currentColor' : 'none'} />}
            label={a.saved ? '取消收藏' : '收藏'}
            size="sm"
            selected={a.saved}
            onClick={() => void a.toggleSave()}
          />
        </span>
      </div>
    </article>
  )
}
