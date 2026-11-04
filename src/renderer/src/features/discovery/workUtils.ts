import type { Work, WorkVersion } from '@/lib/types.discovery'

export function impactText(w: Work): string | null {
  const bits: string[] = []
  if (w.citations !== null && w.citations !== undefined) bits.push(`被引 ${w.citations.toLocaleString()}`)
  if (w.percentile !== null && w.percentile !== undefined) {
    bits.push(w.percentile >= 0.99 ? '同领域同年份前 1%' : w.percentile >= 0.9 ? '同领域同年份前 10%' : `同领域同年份引用百分位 ${Math.round(w.percentile * 100)}`)
  } else if (w.fwci !== null && w.fwci !== undefined) {
    bits.push(`FWCI ${w.fwci.toFixed(1)}`)
  }
  return bits.length ? bits.join(' · ') : null
}

export function venueLine(w: Work): string {
  return [w.venue, w.year ? String(w.year) : null].filter(Boolean).join(' · ')
}

export function authorsLine(authors: string[], max = 4): string {
  if (authors.length === 0) return '作者未知'
  const shown = authors.slice(0, max).join('、')
  return authors.length > max ? `${shown} 等 ${authors.length} 人` : shown
}

/** 打开原文：优先 DOI，其次 arXiv，再次来源页面。 */
export function originalUrl(w: Work): string | null {
  if (w.doi) return `https://doi.org/${w.doi}`
  if (w.arxiv_id) return `https://arxiv.org/abs/${w.arxiv_id}`
  return w.url
}

/** 能否加入文献库：有 DOI、arXiv 号或直链 PDF 之一。 */
export function importTarget(w: Work): { kind: 'doi'; value: string } | { kind: 'url'; value: string } | null {
  if (w.doi && !w.doi.startsWith('10.48550/')) return { kind: 'doi', value: w.doi }
  if (w.arxiv_id) return { kind: 'url', value: `https://arxiv.org/abs/${w.arxiv_id}` }
  if (w.doi) return { kind: 'doi', value: w.doi }
  if (w.pdf_url) return { kind: 'url', value: w.pdf_url }
  return null
}

const KIND_LABEL: Record<string, string> = { preprint: '预印本', published: '正式发表', accepted: '已接收待出版', other: '其它版本' }

export function versionLabel(v: WorkVersion): string {
  return KIND_LABEL[v.kind] ?? v.kind
}

export const MODE_OPTIONS = [
  { value: 'comprehensive', label: '综合搜索' },
  { value: 'recent', label: '最新研究' },
  { value: 'impact', label: '高影响力论文' },
  { value: 'related', label: '相关工作' },
  { value: 'cross', label: '跨学科探索' }
] as const

export const MODE_HINTS: Record<string, string> = {
  comprehensive: '综合相关性、学术质量、影响力、时效与新颖性排序。',
  recent: '只看近两年的研究，按发表时间与相关性排序，时效权重加大。',
  impact: '在相关论文中优先领域归一化引用靠前的工作，不是单纯按引用数。',
  related: '以一篇论文为基准，查找它的参考文献、相关工作与引用它的后续研究。',
  cross: '排除主导学科，用类比概念到其它学科里找相似的方法与问题。'
}

export const DIMENSION_LABELS: Record<string, string> = {
  relevance: '相关性',
  quality: '学术质量',
  impact: '影响力',
  recency: '时效',
  novelty: '新颖性'
}
