import { describe, expect, it } from 'vitest'
import type { InsightCard } from '@/lib/types'
import { TEMP_CHAIN, visibleCards } from './insightStore'

const c = (id: string, conv: string | null, saved = true, chain = 'k1'): InsightCard =>
  ({ id, conversation_id: conv, saved, chain_id: chain }) as unknown as InsightCard

describe('按对话显示卡片', () => {
  const cards = [c('a', 'c1'), c('b', 'c2'), c('d', 'c1', false), c('e', '')]
  it('选了对话只显示这个对话的卡片，别的对话和不属于任何对话的卡片都不混进来', () => {
    expect(visibleCards(cards, null, 'c1').map((x) => x.id)).toEqual(['a', 'd'])
    expect(visibleCards(cards, null, 'c2').map((x) => x.id)).toEqual(['b'])
  })
  it('没有对话（还没开始聊）时显示全部；再叠加分析链和临时卡片筛选', () => {
    expect(visibleCards(cards, null).length).toBe(4)
    expect(visibleCards(cards, TEMP_CHAIN, 'c1').map((x) => x.id)).toEqual(['d'])
  })
})
