import { describe, expect, it } from 'vitest'
import { firstUnanswered, isRight, scoreOf, type QuizQuestion, type QuizSet } from './quizStore'

const quiz = (answers: QuizSet['answers']): QuizSet => ({
  id: 1,
  paper_id: 'p',
  created_at: '',
  answers,
  questions: [0, 1, 2].map((i) => ({ question: `题 ${i}`, options: ['a', 'b', 'c', 'd'], answer: i, explanation: '', evidence: null }))
})

describe('自测', () => {
  it('统计对了几题、答了几题', () => {
    expect(scoreOf(quiz({ '0': 0, '1': 3 }))).toEqual({ right: 1, answered: 2, total: 3 })
    expect(scoreOf(quiz({}))).toEqual({ right: 0, answered: 0, total: 3 })
  })

  it('继续做时回到第一道没答的题；都答完停在最后一题', () => {
    expect(firstUnanswered(quiz({ '0': 0 }))).toBe(1)
    expect(firstUnanswered(quiz({ '0': 0, '1': 1, '2': 2 }))).toBe(2)
  })
})

describe('多选题判分', () => {
  const q: QuizQuestion = { type: 'multi', question: '多选', options: ['a', 'b', 'c', 'd', 'e'], answer: [0, 3], explanation: '', evidence: null }
  it('必须选得和正确答案完全一致，多选少选都不对', () => {
    expect(isRight(q, [3, 0])).toBe(true)
    expect(isRight(q, [0])).toBe(false)
    expect(isRight(q, [0, 1, 3])).toBe(false)
    expect(isRight(q, undefined)).toBe(false)
  })
})
