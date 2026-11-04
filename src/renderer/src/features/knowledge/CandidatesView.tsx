import { useCallback, useEffect, useRef, useState } from 'react'
import { Button, EmptyState, Input, ProgressBar, Select, Tag, TextArea, toast } from '@/components/ui'
import { api, errorMessage } from '@/lib/api'
import { kbApi } from '@/lib/api.discovery'
import type { Job } from '@/lib/types'
import type { ItemKind, KbCandidate } from '@/lib/types.discovery'
import { useLibraryPapers } from '@/features/shared/useLibraryPapers'
import { useJobsStore } from '@/stores/jobsStore'
import { EvidenceList } from './EvidenceList'
import { useKb } from './knowledgeStore'
import { KIND_OPTIONS, kindLabel, ORIGIN_LABEL } from './kbUtils'

const NEW_TOPIC = '__new__'

function CandidateRow({ cand, onDone }: { cand: KbCandidate; onDone: () => void }) {
  const { topics, scope, bump, loadTopics } = useKb()
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(cand.title)
  const [content, setContent] = useState(cand.content)
  const [kind, setKind] = useState<ItemKind>(cand.kind)
  const [topic, setTopic] = useState<string>(scope !== 'all' && scope !== 'unassigned' ? scope : '')
  const [newTopic, setNewTopic] = useState('')
  const [showEv, setShowEv] = useState(false)
  const [busy, setBusy] = useState(false)

  const accept = async (): Promise<void> => {
    if (topic === NEW_TOPIC && !newTopic.trim()) {
      toast.info('请先填写新主题的名称')
      return
    }
    setBusy(true)
    try {
      await kbApi.acceptCandidate(cand.id, {
        topic_id: topic && topic !== NEW_TOPIC ? topic : null,
        new_topic: topic === NEW_TOPIC ? newTopic.trim() : null,
        title: editing ? title.trim() : undefined,
        content: editing ? content : undefined,
        kind: editing ? kind : undefined
      })
      await loadTopics()
      bump()
      onDone()
      toast.success('已加入知识库')
    } catch (err) {
      toast.error('无法采纳', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }
  const reject = async (): Promise<void> => {
    setBusy(true)
    try {
      await kbApi.rejectCandidate(cand.id)
      onDone()
    } catch (err) {
      toast.error('操作失败', errorMessage(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <li className="pd-kb-cand">
      <div className="pd-dc-meta" style={{ marginTop: 0 }}>
        <Tag>AI 候选 · 待你确认</Tag>
        <Tag>{kindLabel(cand.kind)}</Tag>
        <span>来源：{ORIGIN_LABEL[cand.origin_kind] ?? cand.origin_kind}</span>
        {cand.paper_title && <span>{cand.paper_title.slice(0, 60)}</span>}
      </div>
      {editing ? (
        <div className="pd-kb-cand-edit">
          <Input value={title} onChange={setTitle} aria-label="候选标题" />
          <Select size="sm" value={kind} onChange={(v) => setKind(v as ItemKind)} options={KIND_OPTIONS.map((o) => ({ value: o.value, label: o.label }))} aria-label="类型" />
          <TextArea value={content} onChange={setContent} rows={4} aria-label="候选内容" />
        </div>
      ) : (
        <>
          <h3 className="pd-kb-cand-title">{cand.title}</h3>
          <p className="pd-kb-cand-body">{cand.content}</p>
        </>
      )}
      <div className="pd-dc-actions">
        <Select
          size="sm"
          value={topic}
          onChange={setTopic}
          aria-label="归入主题"
          options={[{ value: '', label: '暂不归入主题' }, ...topics.map((t) => ({ value: t.id, label: t.name })), { value: NEW_TOPIC, label: '新建主题…' }]}
        />
        {topic === NEW_TOPIC && <Input value={newTopic} onChange={setNewTopic} placeholder="新主题名称" aria-label="新主题名称" />}
        <Button size="sm" variant="primary" loading={busy} onClick={() => void accept()}>
          采纳
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setEditing((v) => !v)}>
          {editing ? '放弃编辑' : '编辑后采纳'}
        </Button>
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => void reject()}>
          拒绝
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowEv((v) => !v)}>
          {showEv ? '收起证据' : `查看证据 ${cand.evidence.length}`}
        </Button>
      </div>
      {showEv && <EvidenceList evidence={cand.evidence} empty="没有原文证据。" />}
    </li>
  )
}

export function CandidatesView() {
  const { items: library } = useLibraryPapers()
  const finishedTick = useJobsStore((s) => s.finishedTick)
  const [paperId, setPaperId] = useState('')
  const [filterPaper, setFilterPaper] = useState('')
  const [cands, setCands] = useState<KbCandidate[]>([])
  const [loading, setLoading] = useState(true)
  const [job, setJob] = useState<Job | null>(null)
  const [starting, setStarting] = useState(false)
  const timer = useRef<number | null>(null)

  const load = useCallback(async (): Promise<void> => {
    try {
      setCands((await kbApi.candidates(filterPaper || undefined)).items)
    } catch (err) {
      toast.error('候选加载失败', errorMessage(err))
    } finally {
      setLoading(false)
    }
  }, [filterPaper])
  useEffect(() => {
    void load()
  }, [load, finishedTick])

  const poll = useCallback(
    (jobId: string): void => {
      const tick = async (): Promise<void> => {
        try {
          const j = await api.jobs.get(jobId)
          setJob(j)
          if (j.status === 'pending' || j.status === 'running') {
            timer.current = window.setTimeout(() => void tick(), 1500)
          } else {
            if (j.status === 'completed') toast.success('候选提取完成', j.message || undefined)
            else if (j.status === 'failed') toast.error('候选提取失败', j.error ?? undefined)
            void load()
          }
        } catch {
          setJob(null)
        }
      }
      void tick()
    },
    [load]
  )
  useEffect(() => () => void (timer.current && window.clearTimeout(timer.current)), [])

  const extract = async (): Promise<void> => {
    if (!paperId) return
    setStarting(true)
    try {
      const r = await kbApi.extract(paperId)
      useJobsStore.getState().track(r.job_id)
      setFilterPaper(paperId)
      poll(r.job_id)
    } catch (err) {
      toast.error('无法开始提取', errorMessage(err))
    } finally {
      setStarting(false)
    }
  }

  const running = job && (job.status === 'pending' || job.status === 'running')
  const paperOptions = library.filter((p) => p.role !== 'supplement' && p.status === 'ready')

  return (
    <div className="pd-kb-cands">
      <p className="pd-dc-hint" style={{ marginTop: 0, maxWidth: '80ch' }}>
        从一篇已解析的论文里提取“方法 / 发现 / 概念 / 公式 / 局限”候选，每条都附原文证据。候选只是建议，需要你逐条采纳、编辑后采纳或拒绝，才会进入知识库。
      </p>
      <div className="pd-dc-actions" style={{ marginTop: 'var(--sp-md)' }}>
        <Select
          value={paperId}
          onChange={setPaperId}
          aria-label="选择论文"
          options={[{ value: '', label: paperOptions.length ? '选择要提取的论文…' : '文献库中没有已解析的论文' }, ...paperOptions.map((p) => ({ value: p.id, label: p.title.slice(0, 80) }))]}
          style={{ maxWidth: 520 }}
        />
        <Button variant="primary" loading={starting || !!running} disabled={!paperId} onClick={() => void extract()}>
          提取候选
        </Button>
      </div>
      {job && (
        <div style={{ marginTop: 'var(--sp-md)', maxWidth: 520 }}>
          {running && <ProgressBar value={job.progress > 0 ? job.progress : undefined} />}
          <p className="pd-dc-hint">{job.status === 'failed' ? (job.error ?? '提取失败') : job.message || (running ? '正在读取论文并提取……' : '')}</p>
        </div>
      )}

      <div className="pd-kb-pane-head" style={{ marginTop: 'var(--sp-2xl)', paddingBottom: 'var(--sp-sm)', borderBottom: '1px solid var(--border-strong)' }}>
        <span className="pd-cmp-h">待确认候选 {cands.length}</span>
        <Select
          size="sm"
          value={filterPaper}
          onChange={setFilterPaper}
          aria-label="按论文筛选"
          options={[{ value: '', label: '全部论文' }, ...library.filter((p) => p.role !== 'supplement').map((p) => ({ value: p.id, label: p.title.slice(0, 60) }))]}
        />
      </div>
      {!loading && cands.length === 0 && <EmptyState title="没有待确认的候选" description="选择一篇论文并点“提取候选”。已经拒绝或采纳过的内容不会再次出现。" />}
      <ul className="pd-kb-cand-list">
        {cands.map((c) => (
          <CandidateRow key={c.id} cand={c} onDone={() => setCands((xs) => xs.filter((x) => x.id !== c.id))} />
        ))}
      </ul>
    </div>
  )
}
