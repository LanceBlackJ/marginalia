import { useEffect, useState } from 'react'
import { Merge, Plus } from 'lucide-react'
import { Button, Dialog, Field, Input, Select, TextArea, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import { kbGraphApi } from '@/lib/api.export'
import { useKb } from './knowledgeStore'

/** 左栏：主题列表 + AI 建议的主题（候选，需你确认后才会成为主题）。 */
export function TopicPane() {
  const { topics, suggestions, scope, setScope, loadTopics, loadSuggestions, bump } = useKb()
  const [creating, setCreating] = useState(false)
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState(false)
  const [suggesting, setSuggesting] = useState(false)
  const [merging, setMerging] = useState<{ target: string; sources: string[] } | null>(null)

  useEffect(() => {
    void loadSuggestions()
  }, [loadSuggestions])

  const create = async (): Promise<void> => {
    if (!name.trim()) return
    setBusy(true)
    try {
      const t = await kbApi.createTopic(name.trim(), desc.trim())
      await loadTopics()
      setScope(t.id)
      setCreating(false)
      setName('')
      setDesc('')
    } catch (err) {
      toast.error('无法创建主题', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const suggest = async (): Promise<void> => {
    setSuggesting(true)
    try {
      const r = await kbApi.suggestTopics()
      await loadSuggestions()
      if (r.items.length === 0) toast.info('没有新的主题建议', '文献库里的论文已经都归好类，或还不足以聚成主题。')
    } catch (err) {
      toast.error('AI 建议主题失败', errorMessage(err))
    } finally {
      setSuggesting(false)
    }
  }

  const resolve = async (id: string, accept: boolean): Promise<void> => {
    try {
      if (accept) {
        const t = await kbApi.acceptTopicSuggestion(id)
        await loadTopics()
        setScope(t.id)
      } else {
        await kbApi.rejectTopicSuggestion(id)
      }
      await loadSuggestions()
      bump()
    } catch (err) {
      toast.error(accept ? '无法采纳' : '操作失败', errorMessage(err))
    }
  }

  const merge = async (): Promise<void> => {
    if (!merging || !merging.target || merging.sources.length === 0) return
    setBusy(true)
    try {
      const r = await kbGraphApi.mergeTopics({ source_ids: merging.sources, target_id: merging.target })
      toast.success('已合并主题', `${r.merged} 个主题并入目标，移入 ${r.moved_items} 个条目、${r.moved_papers} 篇论文`)
      setMerging(null)
      setScope(merging.target)
      await loadTopics()
      bump()
    } catch (err) {
      toast.error('无法合并主题', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  const rowCls = (active: boolean): string => `pd-kb-topic${active ? ' is-active' : ''}`

  return (
    <div className="pd-kb-topics">
      <div className="pd-kb-pane-head">
        <span className="pd-cmp-h">主题</span>
        <span style={{ display: 'inline-flex', gap: 4 }}>
          {topics.length >= 2 && (
            <Button size="sm" variant="ghost" icon={<Merge size={14} />} onClick={() => setMerging({ target: topics[0].id, sources: [] })} title="把几个主题合并成一个">
              合并
            </Button>
          )}
          <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={() => setCreating(true)}>
            新建
          </Button>
        </span>
      </div>
      <ul className="pd-kb-topic-list">
        <li>
          <button type="button" className={rowCls(scope === 'all')} onClick={() => setScope('all')}>
            <span>全部条目</span>
          </button>
        </li>
        <li>
          <button type="button" className={rowCls(scope === 'unassigned')} onClick={() => setScope('unassigned')}>
            <span>未归类</span>
          </button>
        </li>
        {topics.map((t) => (
          <li key={t.id}>
            <button type="button" className={rowCls(scope === t.id)} onClick={() => setScope(t.id)} title={t.description || undefined}>
              <span className="pd-kb-topic-name">{t.name}</span>
              <span className="pd-kb-count">{t.item_count}</span>
            </button>
          </li>
        ))}
      </ul>
      {topics.length === 0 && <p className="pd-dc-hint">还没有主题，可手动新建。</p>}

      <details className="pd-kb-extra pd-kb-sugbox" open={suggestions.length > 0}>
        <summary>AI 建议的主题{suggestions.length > 0 ? ` · ${suggestions.length}` : ''}</summary>
        <div className="pd-kb-sugtools">
          <span className="pd-dc-sub" style={{ margin: 0 }}>按标题与摘要聚类，只是候选，确认后才创建。</span>
          <Button size="sm" variant="ghost" loading={suggesting} onClick={() => void suggest()}>
            生成建议
          </Button>
        </div>
      <ul className="pd-kb-sugs">
        {suggestions.map((s) => (
          <li key={s.id}>
            <div className="pd-kb-sug-name">{s.name}</div>
            {s.description && <div className="pd-dc-sub" style={{ marginTop: 2 }}>{s.description}</div>}
            <div className="pd-dc-sub">
              {s.papers.slice(0, 3).map((p) => (
                <span key={p.id}>· {p.title.slice(0, 40)}</span>
              ))}
              {s.papers.length > 3 && <span>等 {s.papers.length} 篇</span>}
            </div>
            <div className="pd-dc-actions" style={{ marginTop: 6 }}>
              <Button size="sm" variant="secondary" onClick={() => void resolve(s.id, true)}>
                {s.existing ? '并入已有主题' : '采纳'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => void resolve(s.id, false)}>
                忽略
              </Button>
            </div>
          </li>
        ))}
      </ul>
      </details>

      <Dialog
        open={!!merging}
        onClose={() => setMerging(null)}
        title="合并主题"
        footer={
          <>
            <Button variant="ghost" onClick={() => setMerging(null)}>
              取消
            </Button>
            <Button variant="primary" loading={busy} disabled={!merging || merging.sources.length === 0} onClick={() => void merge()}>
              合并{merging && merging.sources.length > 0 ? ` · ${merging.sources.length} 个并入` : ''}
            </Button>
          </>
        }
      >
        {merging && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-lg)' }}>
            <Field label="合并到" hint="保留这个主题">
              {(id) => (
                <Select
                  id={id}
                  value={merging.target}
                  onChange={(v) => setMerging({ target: v, sources: merging.sources.filter((x) => x !== v) })}
                  options={topics.map((t) => ({ value: t.id, label: `${t.name} · ${t.item_count} 条` }))}
                />
              )}
            </Field>
            <Field label="并入的主题" hint="合并后这些主题会被删除，里面的条目与论文都移到目标主题。">
              {() => (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 220, overflowY: 'auto' }}>
                  {topics
                    .filter((t) => t.id !== merging.target)
                    .map((t) => (
                      <label key={t.id} className="pd-export-check">
                        <input
                          type="checkbox"
                          checked={merging.sources.includes(t.id)}
                          onChange={(e) =>
                            setMerging({ ...merging, sources: e.target.checked ? [...merging.sources, t.id] : merging.sources.filter((x) => x !== t.id) })
                          }
                        />
                        {t.name} · {t.item_count} 条
                      </label>
                    ))}
                </div>
              )}
            </Field>
          </div>
        )}
      </Dialog>

      <Dialog
        open={creating}
        onClose={() => setCreating(false)}
        title="新建主题"
        footer={
          <>
            <Button variant="ghost" onClick={() => setCreating(false)}>
              取消
            </Button>
            <Button variant="primary" loading={busy} disabled={!name.trim()} onClick={() => void create()}>
              创建
            </Button>
          </>
        }
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--sp-lg)' }}>
          <Field label="主题名称">{(id) => <Input id={id} value={name} onChange={setName} onEnter={() => void create()} placeholder="例如：复杂网络关键节点识别" autoFocus />}</Field>
          <Field label="说明">{(id) => <TextArea id={id} value={desc} onChange={setDesc} rows={3} placeholder="这个主题关心什么问题" />}</Field>
        </div>
      </Dialog>
    </div>
  )
}
