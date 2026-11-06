import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertCircle, ChevronDown, ChevronRight, Square, X } from 'lucide-react'
import { Button, Tag } from '@/components/ui'
import { Markdown } from '@/components/Markdown'
import { formatNumber, taskTypeLabel } from '@/lib/format'
import { parseAnswerSections } from '@/lib/sections'
import { useReaderStore } from '@/features/reader/readerStore'
import { Tex } from '@/features/formula/Tex'
import '@/features/formula/formula.css'
import { useInsightStore, type PendingCard } from './insightStore'
import { questionLine, shortModelName } from './insightModel'
import { QuestionBubble } from './QuestionBubble'

const CONFIG_ERRORS = new Set(['no_api_key', 'not_configured'])

/** 流式生成中的卡片：先显示 meta（模型、发送内容摘要），再逐步显示回答。 */
export function PendingCardView({ pending }: { pending: PendingCard }) {
  const navigate = useNavigate()
  const stop = useInsightStore((s) => s.stop)
  const dismiss = useInsightStore((s) => s.dismissPending)
  const ask = useInsightStore((s) => s.ask)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  const [showSent, setShowSent] = useState(false)

  const sections = useMemo(() => parseAnswerSections(pending.text), [pending.text])
  const { meta } = pending
  const streaming = pending.status === 'streaming'
  const title = taskTypeLabel(meta?.task_type ?? pending.intent)
  const qline = questionLine(pending.question || (pending.selection ? `${title}选中内容` : ''), pending.selection?.text)

  const retry = (): void => {
    dismiss()
    void ask({
      question: pending.question,
      intent: pending.intent,
      selection: pending.selection,
      equationId: pending.equationId,
      editCardId: pending.editingCardId,
      figureId: pending.figureId
    })
  }

  return (
    <article className="pd-card pd-card--pending" aria-busy={streaming}>
      <div className="pd-card-meta">
        <span className="pd-card-meta-text" title={meta?.model}>
          {[title, pending.selection ? `第 ${pending.selection.page} 页` : '', shortModelName(meta?.model)].filter(Boolean).join(' · ')}
          {streaming && ' · 生成中'}
          {pending.status === 'stopped' && ' · 已停止'}
          {pending.status === 'failed' && ' · 失败'}
        </span>
      </div>

      <QuestionBubble line={qline} attachments={pending.attachmentNames} />

      {pending.equationId != null && pending.latex?.latex && (
        <div className="pd-eq">
          <div className="pd-eq-view">
            <Tex latex={pending.latex.latex} display />
          </div>
        </div>
      )}
      {pending.equationId != null && streaming && !meta && pending.stage && <p className="pd-aux pd-muted">{pending.stage}</p>}

      {pending.escalatedTo && (
        <p className="pd-card-hint" role="status">
          快速模型的结果不够可靠，已升级到强模型 {shortModelName(pending.escalatedTo)} 重新生成。
        </p>
      )}

      {pending.supplementNeeds && (
        <p className="pd-card-hint" role="note">
          {pending.supplementNeeds.reason}。你还没有允许使用补充材料，这次只基于正文回答。
        </p>
      )}

      {meta && (
        <div className="pd-sent">
          <button type="button" className="pd-sent-toggle" onClick={() => setShowSent((v) => !v)} aria-expanded={showSent}>
            {showSent ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            发送了 {meta.context.passage_count} 段原文，约 {formatNumber(meta.context.chars_sent)} 字，给云端模型
          </button>
          {showSent && (
            <div className="pd-sent-body">
              <div className="pd-row" style={{ gap: 4, flexWrap: 'wrap' }}>
                {meta.context.includes.question && <Tag>你的问题</Tag>}
                {meta.context.includes.selection && <Tag>选中文字</Tag>}
                {meta.context.includes.notes && <Tag tone="warning">个人笔记</Tag>}
                {meta.context.includes.supplements && <Tag tone="warning">补充材料</Tag>}
                {meta.context.includes.code && <Tag tone="warning">代码</Tag>}
                {!meta.context.includes.notes && !meta.context.includes.supplements && !meta.context.includes.code && <span className="pd-aux pd-weak">未包含笔记、补充材料和代码</span>}
              </div>
              <div className="pd-aux pd-weak" style={{ margin: '6px 0 4px' }}>
                原文段落：
              </div>
              <div className="pd-row" style={{ gap: 4, flexWrap: 'wrap' }}>
                {meta.context.passage_ords.map((o) => (
                  <button key={o} type="button" className="pd-cite" onClick={() => void jumpToOrd(o)}>
                    P{o}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {pending.status === 'failed' && pending.error && pending.text && pending.equationId == null && (
        <div className="pd-card-direct" data-translatable>
          <Markdown text={sections.direct} />
          {sections.detail && <Markdown text={sections.detail} />}
        </div>
      )}
      {pending.status === 'failed' && pending.error ? (
        <div className="pd-card-error">
          <AlertCircle size={15} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 500 }}>{CONFIG_ERRORS.has(pending.error.code) ? '还没有配置 AI 模型' : '这次生成没有成功'}</div>
            <div className="pd-aux" style={{ marginTop: 2 }}>
              {pending.error.message}
            </div>
            {pending.editingCardId && (
              <div className="pd-aux pd-muted" style={{ marginTop: 2 }}>
                原来的提问和回答没有改动。
              </div>
            )}
          </div>
        </div>
      ) : pending.text ? (
        <div className="pd-card-direct" data-translatable>
          {pending.equationId != null ? (
            <Markdown text={pending.text} />
          ) : (
            <>
              <Markdown text={sections.direct} />
              {sections.detail && <Markdown text={sections.detail} />}
              {sections.outside && (
                <section className="pd-card-outside">
                  <h4 className="pd-card-sec">
                    论文之外 <span className="pd-card-outside-tag">不是论文原文</span>
                  </h4>
                  <Markdown text={sections.outside} />
                </section>
              )}
            </>
          )}
          {streaming && <span className="pd-cursor" aria-hidden />}
        </div>
      ) : (
        streaming && (
          <div className="pd-card-direct">
            <span className="pd-cursor" aria-hidden />
          </div>
        )
      )}

      {streaming && pending.equationId == null && pending.stage && <p className="pd-aux pd-muted">{pending.stage}</p>}

      <footer className="pd-card-foot">
        {streaming ? (
          <Button size="sm" icon={<Square size={12} />} onClick={stop}>
            停止生成
          </Button>
        ) : (
          <>
            {pending.status === 'failed' && pending.error && CONFIG_ERRORS.has(pending.error.code) ? (
              <Button size="sm" variant="primary" onClick={() => navigate('/settings/ai')}>
                去设置 AI 模型
              </Button>
            ) : (
              <Button size="sm" onClick={retry}>
                重试
              </Button>
            )}
            <span className="pd-spacer" />
            <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={dismiss}>
              {pending.editingCardId ? '保留原来的' : '关闭'}
            </Button>
          </>
        )}
      </footer>
    </article>
  )
}
