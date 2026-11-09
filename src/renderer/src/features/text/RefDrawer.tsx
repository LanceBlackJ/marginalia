import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { Button, Skeleton } from '@/components/ui'
import { Markdown } from '@/components/Markdown'
import { shouldOfferTranslate } from '@/components/selectTranslateModel'
import { useReaderStore } from '@/features/reader/readerStore'
import { openPaperBrowser } from '@/lib/native'
import { addToLibrary, copyText, linkFor } from './RefMenu'
import { bibtex, matchLabel, plainCitation } from './refModel'
import { useRefStore } from './refStore'

/** 参考文献详情抽屉：原文、题录、匹配可信度、摘要全文、AI 概括、本文引用处。 */
export function RefDrawer() {
  const index = useRefStore((s) => s.drawer)
  const item = useRefStore((s) => (s.drawer !== null ? s.byIndex.get(s.drawer) : undefined))
  const resState = useRefStore((s) => (s.drawer !== null ? s.resolutions[s.drawer] : undefined))
  const sum = useRefStore((s) => (s.drawer !== null ? s.summaries[s.drawer] : undefined))
  const ctxs = useRefStore((s) => (s.drawer !== null ? s.contexts[s.drawer] : undefined))
  const tr = useRefStore((s) => (s.drawer !== null ? s.abstractTr[s.drawer] : undefined))
  const translateAbstract = useRefStore((s) => s.translateAbstract)
  const resolve = useRefStore((s) => s.resolve)
  const summarize = useRefStore((s) => s.summarize)
  const navigate = useNavigate()

  useEffect(() => {
    if (index === null) return
    const key = (e: KeyboardEvent): void => {
      // 划词翻译的浮条/浮层开着时，Esc 先关它们，不关抽屉
      if (e.key === 'Escape' && !document.querySelector('[data-selecttranslate-ui]')) useRefStore.setState({ drawer: null })
    }
    document.addEventListener('keydown', key)
    return () => document.removeEventListener('keydown', key)
  }, [index])

  if (index === null || !item) return null
  const res = resState?.data
  const match = res?.match
  const doi = (res?.found && res.doi) || item.doi
  const link = linkFor(item, res)
  const abstract = res?.found ? res.abstract : undefined
  const close = (): void => useRefStore.setState({ drawer: null })

  return (
    <aside className="pd-refdrawer" role="complementary" aria-label={`参考文献 ${index} 详情`}>
      <header className="pd-refdrawer-head">
        <strong>参考文献 [{index}]</strong>
        <button type="button" className="pd-refdrawer-x" aria-label="关闭" onClick={close}>
          <X size={16} />
        </button>
      </header>
      <div className="pd-refdrawer-body">
        <section>
          <h4>原文</h4>
          <p className="pd-refdrawer-raw" data-translatable>{item.raw}</p>
          {(item.messy || item.merged) && <p className="pd-refpop-warn">这条文字被双栏排版弄乱了，解析出的题录可能不准，请以原文为准。</p>}
        </section>

        <section>
          <h4>题录</h4>
          {res?.found ? (
            <dl className="pd-refpop-meta" data-translatable>
              <dt>标题</dt>
              <dd>{res.title}</dd>
              {(res.authors?.length ?? 0) > 0 && (
                <>
                  <dt>作者</dt>
                  <dd>{res.authors!.slice(0, 10).join('; ')}</dd>
                </>
              )}
              <dt>出处</dt>
              <dd>{[res.venue, res.year].filter(Boolean).join(' · ') || '—'}</dd>
              {res.doi && (
                <>
                  <dt>DOI</dt>
                  <dd>{res.doi}</dd>
                </>
              )}
              {res.citations != null && (
                <>
                  <dt>被引</dt>
                  <dd>{res.citations}</dd>
                </>
              )}
            </dl>
          ) : (
            <p className="pd-refdrawer-dim">{item.title ? `解析自原文：${item.title}` : '没有从原文里解析出标题。'}</p>
          )}
          {match && (
            <p className={`pd-refmatch pd-refmatch--${match.level}`}>
              {matchLabel(match.level)} · {match.reason}
            </p>
          )}
        </section>

        <section>
          <h4>摘要</h4>
          {!resState || resState.status === 'loading' ? (
            <div aria-busy>
              <p className="pd-refdrawer-dim">正在查询 OpenAlex / Crossref，只发送这条参考文献的标题、作者或 DOI，不发送论文正文</p>
              <Skeleton height={12} />
              <Skeleton height={12} style={{ marginTop: 6 }} />
              <Skeleton height={12} width="70%" style={{ marginTop: 6 }} />
            </div>
          ) : resState.status === 'error' ? (
            <p className="pd-refdrawer-err">
              查询失败：{resState.error}{' '}
              <button type="button" className="pd-tp-link" onClick={() => void resolve(index, true)}>
                重试
              </button>
            </p>
          ) : !res?.found ? (
            <p className="pd-refdrawer-dim">
              {res?.reason || '没有查到这篇参考文献。'}{' '}
              <button type="button" className="pd-tp-link" onClick={() => void resolve(index, true)}>
                重新查询
              </button>
            </p>
          ) : abstract ? (
            <>
              <p className="pd-refdrawer-abs" data-translatable>{abstract}</p>
              <p className="pd-refdrawer-dim">摘要来源：{res.abstract_source}</p>
              {shouldOfferTranslate(abstract) &&
                (!tr ? (
                  <Button size="sm" onClick={() => void translateAbstract(index)} title="把摘要翻译成中文（英文在上、译文在下）">
                    翻译摘要
                  </Button>
                ) : (
                  <div className="pd-refdrawer-tr" aria-live="polite">
                    {tr.status === 'streaming' && !tr.text && <Skeleton height={12} />}
                    {tr.text && (
                      <p className="pd-refdrawer-trtext" data-translatable>
                        {tr.text}
                      </p>
                    )}
                    {tr.status === 'error' && (
                      <p className="pd-refdrawer-err">
                        {tr.error}{' '}
                        {!tr.error?.includes('API Key') && (
                          <button type="button" className="pd-tp-link" onClick={() => void translateAbstract(index, true)}>
                            重试
                          </button>
                        )}
                      </p>
                    )}
                    {tr.status === 'done' && (
                      <p className="pd-refdrawer-dim">
                        AI 翻译{tr.model ? ` · ${tr.model}` : ''}
                        {tr.cached ? ' · 已缓存' : ''}
                      </p>
                    )}
                  </div>
                ))}
              {(res.abstract_notes ?? []).map((n) => (
                <p key={n} className="pd-refdrawer-dim pd-refdrawer-note">
                  {n}
                </p>
              ))}
            </>
          ) : (
            <>
              <p className="pd-refdrawer-dim">{res.abstract_reason || '没有可用摘要。'}</p>
              {res.abstract_mismatch && (res.abstract_notes ?? []).map((n) => (
                <p key={n} className="pd-refdrawer-dim pd-refdrawer-note">
                  {n}
                </p>
              ))}
            </>
          )}
        </section>

        <section>
          <h4>AI 概括</h4>
          {!sum ? (
            <>
              <p className="pd-refdrawer-dim">只依据上面的摘要和本文引用它的句子，不使用别的知识；没有摘要就不会概括。</p>
              <Button size="sm" variant="primary" disabled={!!res?.found && !abstract} onClick={() => void summarize(index)}>
                AI 概括它做了什么
              </Button>
            </>
          ) : (
            <>
              {sum.notes.map((n) => (
                <p key={n} className="pd-refmatch pd-refmatch--low">
                  {n}
                </p>
              ))}
              {sum.stage && <p className="pd-refdrawer-dim">{sum.stage}</p>}
              {sum.status === 'streaming' && !sum.text && <Skeleton height={12} />}
              {sum.text && (
                <div className="pd-refdrawer-sum" data-translatable>
                  <Markdown text={sum.text} />
                </div>
              )}
              {sum.status === 'error' && (
                <p className="pd-refdrawer-err">
                  {sum.error}{' '}
                  <button type="button" className="pd-tp-link" onClick={() => void summarize(index)}>
                    重试
                  </button>
                </p>
              )}
              {sum.status === 'done' && sum.done && !sum.done.summary && (
                <p className="pd-refdrawer-dim">
                  {sum.done.message}{' '}
                  {sum.done.link && (
                    <button type="button" className="pd-tp-link" onClick={() => void openPaperBrowser(sum.done!.link!)}>
                      去查看原文
                    </button>
                  )}
                </p>
              )}
              {sum.status === 'done' && sum.done?.summary && (
                <p className="pd-refdrawer-dim">AI 生成 · {sum.done.label ?? '依据：摘要 + 本文引用处'}{sum.done.model ? ` · ${sum.done.model}` : ''}</p>
              )}
            </>
          )}
        </section>

        <section>
          <h4>本文引用处</h4>
          {!ctxs ? (
            <Skeleton height={12} />
          ) : ctxs.length === 0 ? (
            <p className="pd-refdrawer-dim">没有找到本文引用它的句子。</p>
          ) : (
            <ul className="pd-refdrawer-ctx">
              {ctxs.map((c) => (
                <li key={c.ord}>
                  <button type="button" className="pd-tp-link" onClick={() => void useReaderStore.getState().jumpToOrd(c.ord)}>
                    第 {c.page} 页
                  </button>
                  <span data-translatable>{c.sentence}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      <footer className="pd-refdrawer-foot">
        <Button size="sm" disabled={!link} onClick={() => void openPaperBrowser(link)}>
          打开链接（可登录下载）
        </Button>
        {res?.in_library ? (
          <Button size="sm" onClick={() => navigate(`/paper/${res.in_library!.paper_id}`)}>
            在文库中打开
          </Button>
        ) : (
          <Button size="sm" disabled={!doi} title={doi ? '只保存元数据，不下载 PDF' : '没有 DOI，无法加入文库'} onClick={() => void addToLibrary(doi)}>
            加入文库
          </Button>
        )}
        <Button size="sm" onClick={() => void copyText(plainCitation(item, res), '引文已复制')}>
          复制引文
        </Button>
        <Button size="sm" onClick={() => void copyText(bibtex(item, res), 'BibTeX 已复制')}>
          BibTeX
        </Button>
      </footer>
    </aside>
  )
}
