import type { ConceptState, ItemKind } from '@/lib/types.discovery'

export const KIND_OPTIONS: Array<{ value: ItemKind; label: string }> = [
  { value: 'method', label: '方法' },
  { value: 'finding', label: '发现' },
  { value: 'concept', label: '概念' },
  { value: 'formula', label: '公式' },
  { value: 'limitation', label: '局限' },
  { value: 'other', label: '其它' }
]

export const kindLabel = (k: string): string => KIND_OPTIONS.find((o) => o.value === k)?.label ?? k

export const STATE_OPTIONS: Array<{ value: ConceptState; label: string; hint: string }> = [
  { value: 'mastered', label: '已掌握', hint: '你能不看资料讲清楚' },
  { value: 'learning', label: '学习中', hint: '正在学，还没完全弄明白' },
  { value: 'unsure', label: '不确定', hint: '似乎懂一点，但没有把握' },
  { value: 'unknown', label: '不了解', hint: '还没接触过或完全没概念' }
]

export const DEPTH_OPTIONS = [
  { value: '', label: '未设置' },
  { value: 'brief', label: '简明' },
  { value: 'standard', label: '标准' },
  { value: 'deep', label: '深入' }
] as const

export const SOURCE_LABEL: Record<string, string> = { manual: '手动创建', ai: 'AI 建议，已确认', auto: '自动' }
export const ORIGIN_LABEL: Record<string, string> = { analysis: '论文分析', card: '学习卡片', note: '我的笔记' }
