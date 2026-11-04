import { create } from 'zustand'
import { apiRequest, errorMessage } from '@/lib/api'

export type QuizKind = 'mixed' | 'choice' | 'judge'

export interface QuizQuestion {
  /** choice 选择题（4 选 1）| judge 判断题（0 正确、1 错误） */
  type?: 'choice' | 'judge'
  question: string
  options: string[]
  /** 正确选项的下标 */
  answer: number
  explanation: string
  /** 原文里逐字摘的一句依据（后端核对过确实在论文里）；核对不上就是 null */
  evidence: { quote: string; page: number; ord: number } | null
}

export interface QuizSet {
  id: number
  paper_id: string
  created_at: string
  questions: QuizQuestion[]
  /** 题号 → 选了哪个选项 */
  answers: Record<string, number>
}

export type QuizView = 'home' | 'taking' | 'result'

interface QuizState {
  paperId: string | null
  sets: QuizSet[]
  current: QuizSet | null
  view: QuizView
  idx: number
  creating: boolean
  error: string | null
  load: (paperId: string) => Promise<void>
  reset: () => void
  create: (count: number, kind?: QuizKind) => Promise<void>
  open: (id: number) => void
  choose: (qi: number, option: number) => Promise<void>
  next: () => void
  prev: () => void
  home: () => void
  remove: (id: number) => Promise<void>
}

/** 做到哪一题：第一个还没答的；都答完了就是最后一题。 */
export function firstUnanswered(q: QuizSet): number {
  const i = q.questions.findIndex((_x, k) => q.answers[String(k)] === undefined)
  return i < 0 ? q.questions.length - 1 : i
}

export function scoreOf(q: QuizSet): { right: number; answered: number; total: number } {
  let right = 0
  let answered = 0
  q.questions.forEach((x, k) => {
    const a = q.answers[String(k)]
    if (a !== undefined) {
      answered += 1
      if (a === x.answer) right += 1
    }
  })
  return { right, answered, total: q.questions.length }
}

export const useQuizStore = create<QuizState>((set, get) => ({
  paperId: null,
  sets: [],
  current: null,
  view: 'home',
  idx: 0,
  creating: false,
  error: null,

  load: async (paperId) => {
    if (get().paperId === paperId) return
    set({ paperId, sets: [], current: null, view: 'home', idx: 0, error: null })
    try {
      const r = await apiRequest<{ items: QuizSet[] }>('GET', `/api/papers/${paperId}/quizzes`)
      if (get().paperId === paperId) set({ sets: r.items })
    } catch {
      /* 读不到历史不影响出新题 */
    }
  },

  reset: () => set({ paperId: null, sets: [], current: null, view: 'home', idx: 0, creating: false, error: null }),

  create: async (count, kind = 'mixed') => {
    const paperId = get().paperId
    if (!paperId || get().creating) return
    set({ creating: true, error: null })
    try {
      const q = await apiRequest<QuizSet>('POST', `/api/papers/${paperId}/quizzes`, { body: { count, kind } })
      if (get().paperId !== paperId) return
      set((s) => ({ sets: [q, ...s.sets], current: q, view: 'taking', idx: 0 }))
    } catch (e) {
      set({ error: errorMessage(e) })
    } finally {
      set({ creating: false })
    }
  },

  open: (id) => {
    const q = get().sets.find((x) => x.id === id)
    if (!q) return
    const done = Object.keys(q.answers).length >= q.questions.length
    set({ current: q, view: done ? 'result' : 'taking', idx: done ? 0 : firstUnanswered(q) })
  },

  choose: async (qi, option) => {
    const cur = get().current
    if (!cur || cur.answers[String(qi)] !== undefined) return // 答过的不能改
    const next: QuizSet = { ...cur, answers: { ...cur.answers, [String(qi)]: option } }
    set((s) => ({ current: next, sets: s.sets.map((x) => (x.id === next.id ? next : x)) }))
    try {
      await apiRequest('PUT', `/api/quizzes/${cur.id}/answers`, { body: { answers: next.answers } })
    } catch {
      /* 保存不上也不影响继续做，只是下次打开看不到这道的作答 */
    }
  },

  next: () => {
    const cur = get().current
    if (!cur) return
    if (get().idx >= cur.questions.length - 1) set({ view: 'result' })
    else set((s) => ({ idx: s.idx + 1 }))
  },
  prev: () => set((s) => ({ idx: Math.max(0, s.idx - 1) })),
  home: () => set({ view: 'home', current: null, idx: 0 }),

  remove: async (id) => {
    set((s) => ({ sets: s.sets.filter((x) => x.id !== id), ...(s.current?.id === id ? { current: null, view: 'home' as QuizView } : {}) }))
    try {
      await apiRequest('DELETE', `/api/quizzes/${id}`)
    } catch {
      /* 删除失败下次打开还会在 */
    }
  }
}))
