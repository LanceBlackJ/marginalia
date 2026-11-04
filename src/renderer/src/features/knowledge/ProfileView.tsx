import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { X } from 'lucide-react'
import { Button, Input, Segmented, Select, Tag, toast } from '@/components/ui'
import { errorMessage } from '@/lib/api'
import { profileApi } from '@/lib/api.discovery'
import type { ConceptState, InferredInterest, Profile, ProfileConcept, ProfileSuggestion } from '@/lib/types.discovery'
import { DEPTH_OPTIONS, STATE_OPTIONS } from './kbUtils'

const STATE_SELECT = STATE_OPTIONS.map((o) => ({ value: o.value, label: o.label }))

function ConceptRow({ c, onChange, onDelete }: { c: ProfileConcept; onChange: (state: ConceptState) => void; onDelete: () => void }) {
  return (
    <li className="pd-kb-concept">
      <div style={{ minWidth: 0 }}>
        <div className="pd-kb-concept-name">{c.name}</div>
        {c.note && <div className="pd-dc-sub" style={{ marginTop: 0 }}>{c.note}</div>}
        {c.origin === 'suggestion' && <div className="pd-dc-sub" style={{ marginTop: 0 }}>来自 AI 候选，已由你确认</div>}
      </div>
      <Select size="sm" value={c.state} onChange={(v) => onChange(v as ConceptState)} options={STATE_SELECT} aria-label={`${c.name} 的掌握状态`} />
      <button type="button" className="pd-cmp-del" style={{ padding: '4px' }} aria-label="删除这个概念" onClick={onDelete}>
        <X size={14} />
      </button>
    </li>
  )
}

function SuggestionRow({ s, onResolved }: { s: ProfileSuggestion; onResolved: () => void }) {
  const [state, setState] = useState<ConceptState>(s.state)
  const [busy, setBusy] = useState(false)
  const act = async (accept: boolean): Promise<void> => {
    setBusy(true)
    try {
      if (accept) await profileApi.acceptSuggestion(s.id, state)
      else await profileApi.dismissSuggestion(s.id)
      onResolved()
    } catch (err) {
      toast.error('操作失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <li className="pd-kb-concept">
      <div style={{ minWidth: 0 }}>
        <div className="pd-kb-concept-name">
          {s.name} <Tag>AI 候选</Tag>
        </div>
        <div className="pd-dc-sub" style={{ marginTop: 0 }}>依据：{s.basis}</div>
      </div>
      <Select size="sm" value={state} onChange={(v) => setState(v as ConceptState)} options={STATE_SELECT} aria-label={`${s.name} 的状态`} />
      <div className="pd-dc-actions" style={{ marginTop: 0 }}>
        <Button size="sm" variant="secondary" loading={busy} onClick={() => void act(true)}>
          确认
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void act(false)}>
          忽略
        </Button>
      </div>
    </li>
  )
}

/** 个人知识画像：概念掌握状态由你自己决定；AI 只能提出候选，绝不会自动标记为“已掌握”。 */
export function ProfileView() {
  const navigate = useNavigate()
  const [profile, setProfile] = useState<Profile | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [interest, setInterest] = useState('')
  const [name, setName] = useState('')
  const [state, setState] = useState<ConceptState>('learning')
  const [note, setNote] = useState('')
  const [suggesting, setSuggesting] = useState(false)
  const [inferred, setInferred] = useState<InferredInterest[]>([])

  const load = useCallback(async (): Promise<void> => {
    try {
      setProfile(await profileApi.get())
      setError(null)
      // 兴趣推测随你的使用自动更新；取不到时只是不显示这一段
      setInferred(await profileApi.interests().then((r) => r.items).catch(() => []))
    } catch (err) {
      setError(errorMessage(err))
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  if (error) return <p className="pd-status-text pd-status-text--danger">{error}</p>
  if (!profile) return <p className="pd-dc-hint">加载中…</p>

  const meta = profile.meta
  const saveMeta = async (patch: Partial<typeof meta>): Promise<void> => {
    try {
      await profileApi.putMeta(patch)
      await load()
    } catch (err) {
      toast.error('保存失败', errorMessage(err))
    }
  }
  const addInterest = async (): Promise<void> => {
    const v = interest.trim()
    if (!v || meta.interests.includes(v)) return
    setInterest('')
    await saveMeta({ interests: [...meta.interests, v] })
  }
  const addConcept = async (): Promise<void> => {
    if (!name.trim()) return
    try {
      await profileApi.addConcept(name.trim(), state, note.trim())
      setName('')
      setNote('')
      await load()
    } catch (err) {
      toast.error('无法添加', errorMessage(err))
    }
  }
  const suggest = async (): Promise<void> => {
    setSuggesting(true)
    try {
      const r = await profileApi.suggest()
      await load()
      if (r.items.length === 0) toast.info('没有新的候选', '学习卡片太少，或模型没有找到有依据的概念。')
    } catch (err) {
      toast.error('AI 提出候选失败', errorMessage(err))
    } finally {
      setSuggesting(false)
    }
  }

  return (
    <div className="pd-kb-profile">
      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">兴趣方向</h2>
        <div className="pd-kb-chips" style={{ marginTop: 'var(--sp-sm)' }}>
          {meta.interests.map((t) => (
            <span key={t} className="pd-kb-chip is-on">
              {t}
              <button type="button" aria-label={`移除 ${t}`} onClick={() => void saveMeta({ interests: meta.interests.filter((x) => x !== t) })}>
                <X size={12} />
              </button>
            </span>
          ))}
          {meta.interests.length === 0 && <span className="pd-dc-hint" style={{ margin: 0 }}>还没有设置。个性化推荐会优先使用这里的方向。</span>}
        </div>
        <div className="pd-dc-actions" style={{ marginTop: 'var(--sp-sm)' }}>
          <div style={{ width: 280 }}>
            <Input value={interest} onChange={setInterest} onEnter={() => void addInterest()} placeholder="添加一个研究兴趣" aria-label="添加兴趣" />
          </div>
          <Button size="sm" variant="secondary" disabled={!interest.trim()} onClick={() => void addInterest()}>
            添加
          </Button>
        </div>
      </section>

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">根据阅读和笔记推测的兴趣</h2>
        <p className="pd-dc-hint">
          随你的使用自动更新。只读过一篇不算数：要写过笔记、划过重点、收藏或向 AI 提过问，才会被当作兴趣的依据。这只是推测，你可以随时去掉。
        </p>
        <ul className="pd-kb-concepts" style={{ marginTop: 'var(--sp-sm)' }}>
          {inferred.map((it) => (
            <li key={it.topic_id} className="pd-kb-concept">
              <div style={{ minWidth: 0 }}>
                <div className="pd-kb-concept-name">
                  {it.name} <Tag>{it.level_label}</Tag>
                </div>
                <div className="pd-dc-sub" style={{ marginTop: 0 }}>
                  {it.papers} 篇论文，{it.basis}
                </div>
              </div>
              <div className="pd-dc-actions" style={{ marginTop: 0 }}>
                {!meta.interests.includes(it.name) && (
                  <Button size="sm" variant="secondary" onClick={() => void saveMeta({ interests: [...meta.interests, it.name] })}>
                    加入兴趣方向
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() =>
                    void profileApi
                      .muteInterest(it.topic_id)
                      .then(load)
                      .catch((err) => toast.error('操作失败', errorMessage(err)))
                  }
                >
                  不是我的兴趣
                </Button>
              </div>
            </li>
          ))}
          {inferred.length === 0 && <li className="pd-dc-hint">暂时推测不出。多写几条笔记、划些重点或收藏几篇，这里就会出现。</li>}
        </ul>
      </section>

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">解释详略偏好</h2>
        <div style={{ marginTop: 'var(--sp-sm)' }}>
          <Segmented
            variant="text"
            aria-label="解释详略"
            value={meta.explanation_depth ?? ''}
            options={DEPTH_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
            onChange={(v) => void saveMeta({ explanation_depth: v === '' ? null : (v as 'brief' | 'standard' | 'deep') })}
          />
        </div>
        <p className="pd-dc-hint">
          画像是否影响阅读时的 AI 解读，由“设置 → 知识画像”中的开关决定，目前
          <strong>{meta.use_in_prompts ? '已开启' : '关闭'}</strong>。
          {meta.use_in_prompts && profile.prompt_hint ? `开启后每次 AI 解读会附带这一句：“${profile.prompt_hint}”。` : '关闭时不会向模型发送任何画像信息。'}{' '}
          <button type="button" className="pd-dc-more" style={{ color: 'var(--text)' }} onClick={() => navigate('/settings/profile')}>
            前往设置
          </button>
        </p>
      </section>

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">概念掌握状态</h2>
        <div className="pd-kb-states">
          {STATE_OPTIONS.map((st) => {
            const list = profile.concepts.filter((c) => c.state === st.value)
            return (
              <div key={st.value} className="pd-kb-state-col">
                <div className="pd-kb-state-head">
                  {st.label} {list.length}
                </div>
                <div className="pd-dc-sub" style={{ marginTop: 0 }}>{st.hint}</div>
                <ul className="pd-kb-concepts">
                  {list.map((c) => (
                    <ConceptRow
                      key={c.id}
                      c={c}
                      onChange={(s) => void profileApi.updateConcept(c.id, { state: s }).then(load).catch((err) => toast.error('保存失败', errorMessage(err)))}
                      onDelete={() => void profileApi.deleteConcept(c.id).then(load).catch((err) => toast.error('删除失败', errorMessage(err)))}
                    />
                  ))}
                </ul>
              </div>
            )
          })}
        </div>
        <div className="pd-dc-actions" style={{ marginTop: 'var(--sp-lg)' }}>
          <div style={{ width: 220 }}>
            <Input value={name} onChange={setName} onEnter={() => void addConcept()} placeholder="概念，例如：图注意力网络" aria-label="概念名称" />
          </div>
          <Select size="sm" value={state} onChange={(v) => setState(v as ConceptState)} options={STATE_SELECT} aria-label="掌握状态" />
          <div style={{ width: 260 }}>
            <Input value={note} onChange={setNote} onEnter={() => void addConcept()} placeholder="备注" aria-label="备注" />
          </div>
          <Button size="sm" variant="secondary" disabled={!name.trim()} onClick={() => void addConcept()}>
            添加概念
          </Button>
        </div>
      </section>

      <section className="pd-cmp-sec">
        <h2 className="pd-cmp-h">AI 候选 · 待你确认</h2>
        <p className="pd-dc-hint">
          AI 会根据你的学习卡片提出“你可能已经了解 / 还在学 / 有疑问”的概念，并说明依据。它只能提出候选，不会自动写入，更不会替你标记“已掌握”。
        </p>
        <div className="pd-dc-actions" style={{ marginTop: 'var(--sp-sm)' }}>
          <Button size="sm" variant="secondary" loading={suggesting} onClick={() => void suggest()}>
            让 AI 提出候选
          </Button>
        </div>
        <ul className="pd-kb-concepts" style={{ marginTop: 'var(--sp-md)' }}>
          {profile.suggestions.map((s) => (
            <SuggestionRow key={s.id} s={s} onResolved={() => void load()} />
          ))}
          {profile.suggestions.length === 0 && <li className="pd-dc-hint">暂时没有候选。</li>}
        </ul>
      </section>
    </div>
  )
}
