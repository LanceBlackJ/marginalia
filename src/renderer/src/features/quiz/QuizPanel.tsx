import { useEffect, useState } from 'react'
import { ArrowLeft, ArrowRight, Check, Trash2, X } from 'lucide-react'
import { Button, IconButton, Segmented } from '@/components/ui'
import { formatRelativeTime } from '@/lib/format'
import { useReaderStore } from '@/features/reader/readerStore'
import { firstUnanswered, scoreOf, useQuizStore, type QuizKind, type QuizQuestion, type QuizSet } from './quizStore'
import './quiz.css'

const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H']
const COUNT_MIN = 3
const COUNT_MAX = 20

/** 一个选项的标记：选择题是 A–D，判断题是 对/错。 */
const tag = (q: QuizQuestion, i: number): string => (q.type === 'judge' ? (i === 0 ? '对' : '错') : LETTERS[i])
const answerText = (q: QuizQuestion, i: number): string => (q.type === 'judge' ? q.options[i] : `${LETTERS[i]}. ${q.options[i]}`)

function Home() {
  const sets = useQuizStore((s) => s.sets)
  const creating = useQuizStore((s) => s.creating)
  const error = useQuizStore((s) => s.error)
  const create = useQuizStore((s) => s.create)
  const open = useQuizStore((s) => s.open)
  const remove = useQuizStore((s) => s.remove)
  const [count, setCount] = useState<number>(8)
  const [kind, setKind] = useState<QuizKind>('mixed')
  const n = Math.max(COUNT_MIN, Math.min(COUNT_MAX, Math.round(count) || 8))

  return (
    <div className="pd-quiz-home">
      <h3 className="pd-quiz-title">读完了，自测一下</h3>
      <div className="pd-quiz-count">
        <span>题数</span>
        <input
          type="number"
          className="pd-quiz-num"
          min={COUNT_MIN}
          max={COUNT_MAX}
          value={count}
          onChange={(e) => setCount(Number(e.target.value))}
          onBlur={() => setCount(n)}
          aria-label="题数"
        />
        <span className="pd-muted">
          {COUNT_MIN}–{COUNT_MAX}
        </span>
      </div>
      <div className="pd-quiz-count">
        <span>题型</span>
        <Segmented
          value={kind}
          onChange={(v) => setKind(v as QuizKind)}
          aria-label="题型"
          options={[
            { value: 'mixed', label: '混合' },
            { value: 'choice', label: '选择题' },
            { value: 'judge', label: '判断题' }
          ]}
        />
      </div>
      <Button variant="primary" loading={creating} onClick={() => void create(n, kind)}>
        {creating ? '正在出题…' : '让 AI 出题'}
      </Button>
      {error && <p className="pd-field-error">{error}</p>}
      {sets.length > 0 && (
        <div className="pd-quiz-history">
          <h4>做过的</h4>
          <ul>
            {sets.map((q) => {
              const sc = scoreOf(q)
              return (
                <li key={q.id}>
                  <button type="button" onClick={() => open(q.id)}>
                    <span className="pd-quiz-hscore">
                      {sc.answered === sc.total ? `${sc.right}/${sc.total}` : `${sc.answered}/${sc.total} 已答`}
                    </span>
                    <span className="pd-muted">{formatRelativeTime(q.created_at)}</span>
                  </button>
                  <IconButton icon={<Trash2 size={13} />} label="删除这套题" size="sm" onClick={() => void remove(q.id)} />
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}

function Taking({ quiz }: { quiz: QuizSet }) {
  const idx = useQuizStore((s) => s.idx)
  const choose = useQuizStore((s) => s.choose)
  const next = useQuizStore((s) => s.next)
  const prev = useQuizStore((s) => s.prev)
  const home = useQuizStore((s) => s.home)
  const jumpToOrd = useReaderStore((s) => s.jumpToOrd)
  const jumpToPage = useReaderStore((s) => s.jumpToPage)
  const q = quiz.questions[idx]
  const picked = quiz.answers[String(idx)]
  const done = picked !== undefined
  const last = idx >= quiz.questions.length - 1
  const sc = scoreOf(quiz)

  // 键盘：1–4 / A–D 选择，Enter 下一题
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      const t = e.target as HTMLElement
      if (t.closest('input, textarea, [contenteditable="true"]')) return
      const n = '1234'.indexOf(e.key) >= 0 ? Number(e.key) - 1 : 'abcd'.indexOf(e.key.toLowerCase())
      if (e.key.length === 1 && n >= 0 && n < q.options.length && !done) void choose(idx, n)
      else if (e.key === 'Enter' && done) next()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [idx, done, choose, next, q.options.length])

  return (
    <div className="pd-quiz-take">
      <div className="pd-quiz-top">
        <button type="button" className="pd-quiz-back" onClick={home}>
          <ArrowLeft size={13} /> 返回
        </button>
        <span className="pd-muted">
          第 {idx + 1} / {quiz.questions.length} 题 · 对 {sc.right}
        </span>
      </div>
      <div className="pd-quiz-bar" aria-hidden="true">
        <div style={{ width: `${((idx + (done ? 1 : 0)) / quiz.questions.length) * 100}%` }} />
      </div>

      <p className="pd-quiz-q">
        {q.type === 'judge' && <span className="pd-quiz-kind">判断</span>}
        {q.question}
      </p>
      <ul className="pd-quiz-opts">
        {q.options.map((o, i) => {
          const state = !done ? '' : i === q.answer ? 'right' : i === picked ? 'wrong' : 'rest'
          return (
            <li key={i}>
              <button type="button" className="pd-quiz-opt" data-state={state} disabled={done} onClick={() => void choose(idx, i)}>
                <span className="pd-quiz-letter">{state === 'right' ? <Check size={13} /> : state === 'wrong' ? <X size={13} /> : tag(q, i)}</span>
                <span>{o}</span>
              </button>
            </li>
          )
        })}
      </ul>

      {done && (
        <div className="pd-quiz-explain">
          <p className="pd-quiz-verdict">{picked === q.answer ? '答对了' : `答错了，正确答案是 ${tag(q, q.answer)}`}</p>
          {q.explanation && <p>{q.explanation}</p>}
          {q.evidence ? (
            <blockquote className="pd-quiz-ev">
              {q.evidence.quote}
              <button type="button" onClick={() => void jumpToOrd(q.evidence!.ord).catch(() => jumpToPage(q.evidence!.page))}>
                看原文 · 第 {q.evidence.page} 页
              </button>
            </blockquote>
          ) : (
            <p className="pd-muted pd-quiz-noev">这道题没有核对到原文依据，答案仅供参考。</p>
          )}
        </div>
      )}

      <div className="pd-quiz-nav">
        <Button size="sm" variant="ghost" icon={<ArrowLeft size={13} />} disabled={idx === 0} onClick={prev}>
          上一题
        </Button>
        <Button size="sm" variant="primary" disabled={!done} onClick={next}>
          {last ? '看结果' : '下一题'} <ArrowRight size={13} />
        </Button>
      </div>
    </div>
  )
}

function Result({ quiz }: { quiz: QuizSet }) {
  const home = useQuizStore((s) => s.home)
  const create = useQuizStore((s) => s.create)
  const creating = useQuizStore((s) => s.creating)
  const sc = scoreOf(quiz)
  const [open, setOpen] = useState<number | null>(null)

  return (
    <div className="pd-quiz-result">
      <div className="pd-quiz-top">
        <button type="button" className="pd-quiz-back" onClick={home}>
          <ArrowLeft size={13} /> 返回
        </button>
      </div>
      <p className="pd-quiz-big">
        {sc.right}
        <span> / {sc.total}</span>
      </p>
      <ul className="pd-quiz-review">
        {quiz.questions.map((q, i) => {
          const a = quiz.answers[String(i)]
          const ok = a === q.answer
          return (
            <li key={i}>
              <button type="button" onClick={() => setOpen(open === i ? null : i)} aria-expanded={open === i}>
                <span className="pd-quiz-mark" data-ok={a === undefined ? 'none' : ok}>
                  {a === undefined ? '–' : ok ? <Check size={12} /> : <X size={12} />}
                </span>
                <span className="pd-quiz-rq">{q.question}</span>
              </button>
              {open === i && (
                <div className="pd-quiz-explain">
                  <p>
                    正确答案：{answerText(q, q.answer)}
                    {a !== undefined && !ok ? `；你选的是 ${answerText(q, a)}` : ''}
                  </p>
                  {q.explanation && <p>{q.explanation}</p>}
                </div>
              )}
            </li>
          )
        })}
      </ul>
      <Button variant="primary" loading={creating} onClick={() => void create(quiz.questions.length)}>
        再出一套
      </Button>
    </div>
  )
}

/** 读完之后的自测：只有点“让 AI 出题”才会发请求；选择题，选完马上看对错、解析和原文依据。 */
export function QuizPanel() {
  const paperId = useReaderStore((s) => s.paperId)
  const view = useQuizStore((s) => s.view)
  const current = useQuizStore((s) => s.current)
  const load = useQuizStore((s) => s.load)

  useEffect(() => {
    if (paperId) void load(paperId)
  }, [paperId, load])

  return (
    <div className="pd-rpanel-scroll pd-quiz">
      {view === 'home' || !current ? <Home /> : view === 'taking' ? <Taking quiz={current} key={`${current.id}`} /> : <Result quiz={current} />}
    </div>
  )
}

export { firstUnanswered }
