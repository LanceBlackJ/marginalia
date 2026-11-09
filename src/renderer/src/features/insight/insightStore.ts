import { create } from 'zustand'
import { ApiError, api, errorMessage } from '@/lib/api'
import { toast } from '@/components/ui'
import type { AskIntent, AskMeta, AskRequest, Chain, Conversation, InsightCard, SelectionRef } from '@/lib/types'
import { usePrefs } from '@/stores/prefsStore'
import type { NeedsSupplementPermission } from '@/lib/types.materials'
import { useMaterialsStore } from '@/features/materials/materialsStore'
import { visionApi } from '@/lib/api.vision'
import type { EquationLatex } from '@/lib/types.vision'

/** “临时卡片”虚拟链 */
export const TEMP_CHAIN = '__temp__'

export interface PendingCard {
  tempId: string
  question: string
  intent: AskIntent
  selection: SelectionRef | null
  /** 附带的文件名（显示用） */
  attachmentNames?: string[]
  meta: AskMeta | null
  text: string
  status: 'streaming' | 'failed' | 'stopped'
  error: { code: string; message: string } | null
  escalatedTo: string | null
  /** 回答可能需要补充材料但尚未授权（SSE needs_supplement_permission） */
  supplementNeeds?: NeedsSupplementPermission | null
  /** 公式解释（F5）：被解释的公式 id、转写出的 LaTeX、当前阶段说明 */
  equationId?: number
  latex?: EquationLatex | null
  stage?: string | null
  /** 这是“修改上一次提问并重新回答”：被改写的那张卡片的 id。它在列表里让位给这张进行中的卡片；没成功时原卡片原样回来。 */
  editingCardId?: string
  /** 图解析：被解析的图 id（重试用） */
  figureId?: number
}

export interface AskInput {
  question: string
  intent: AskIntent
  selection?: SelectionRef | null
  save?: boolean | null
  /** 附件（截图、文件）：随这一次提问发出 */
  attachments?: Array<{ name: string; mime: string; data: string }>
  /** 公式解释（F5）：给出公式 id 时走 /ai/explain-equation（先识别公式 LaTeX，再讲解） */
  equationId?: number
  /** 修改并重答这张卡片（必须是当前对话里最后一张） */
  editCardId?: string
  /** 图解析：给出图的 id 时走 /ai/explain-figure（把这张图的截图发给视觉模型，回答仍带“图中看不出就明说”的防编造处理） */
  figureId?: number
}

/** 上一条还没生成完时又发出的提问：排队，等前一条结束后自动发出。 */
export interface QueuedAsk {
  id: string
  input: AskInput
}

interface InsightState {
  paperId: string | null
  queue: QueuedAsk[]
  cards: InsightCard[]
  chains: Chain[]
  /** 这篇论文的对话（互相独立）；最近用的在前 */
  conversations: Conversation[]
  /** 当前对话 id；还没有对话时为 null（第一次提问时后端建一个） */
  activeConversation: string | null
  /** null = 全部卡片；TEMP_CHAIN = 临时卡片；否则为链 id */
  activeChain: string | null
  pending: PendingCard | null
  loading: boolean
  error: string | null

  load: (paperId: string) => Promise<void>
  reset: () => void
  ask: (input: AskInput) => Promise<void>
  stop: () => void
  removeQueued: (id: string) => void
  /** 立刻发出队列里的第一条（上一条被停止或失败后，队列不会自动继续，由用户决定） */
  sendQueuedNow: () => void
  dismissPending: () => void
  setActiveChain: (id: string | null) => void
  newConversation: () => Promise<void>
  switchConversation: (id: string) => void
  renameConversation: (id: string, title: string) => Promise<void>
  removeConversation: (id: string) => Promise<void>
  setSaved: (id: string, saved: boolean) => Promise<void>
  removeCard: (id: string) => Promise<void>
  renameChain: (id: string, title: string) => Promise<void>
  removeChain: (id: string) => Promise<void>
}

let controller: AbortController | null = null
let seq = 0

export function visibleCards(cards: InsightCard[], active: string | null, conversation: string | null = null): InsightCard[] {
  const inConv = conversation ? cards.filter((c) => c.conversation_id === conversation) : cards
  if (active === null) return inConv
  if (active === TEMP_CHAIN) return inConv.filter((c) => !c.saved)
  return inConv.filter((c) => c.chain_id === active)
}

/**
 * 当前对话里最后一张卡片的 id：只有它的提问可以修改后重答（改中间的会让后面基于旧回答的追问失效）。
 * 公式解释、思维导图节点等不属于对话的卡片不算。
 */
export function lastCardId(cards: InsightCard[], conversation: string | null): string | null {
  if (!conversation) return null
  const inConv = cards.filter((c) => c.conversation_id === conversation)
  return inConv.length > 0 ? inConv[inConv.length - 1].id : null
}

export const useInsightStore = create<InsightState>((set, get) => {
  const refreshChains = async (paperId: string): Promise<void> => {
    try {
      const { items } = await api.chains.list(paperId)
      if (get().paperId === paperId) set({ chains: items })
    } catch {
      /* 链列表只是辅助导航，失败时保持现状 */
    }
  }

  const refreshConversations = async (paperId: string, pick?: string): Promise<void> => {
    try {
      const { items } = await api.conversations.list(paperId)
      if (get().paperId !== paperId) return
      set((s) => ({ conversations: items, activeConversation: pick ?? s.activeConversation ?? items[0]?.id ?? null }))
    } catch {
      /* 对话列表只是导航，失败时保持现状 */
    }
  }

  /** 点了“停止生成”：后端会把已经输出的部分存成一张卡片，这里取回来替换掉临时卡片，已写出的内容不会消失。 */
  const keepStopped = async (paperId: string, tempId: string): Promise<void> => {
    const cardId = get().pending?.meta?.card_id
    if (!cardId) return
    for (let i = 0; i < 4; i++) {
      await new Promise((r) => setTimeout(r, 350))
      if (get().paperId !== paperId || get().pending?.tempId !== tempId) return
      try {
        const { items } = await api.cards.list(paperId)
        const card = items.find((c) => c.id === cardId)
        if (card) {
          set((s) => ({
            cards: s.cards.some((c) => c.id === card.id) ? s.cards.map((c) => (c.id === card.id ? card : c)) : [...s.cards, card],
            pending: s.pending?.tempId === tempId ? null : s.pending
          }))
          void refreshChains(paperId)
          return
        }
      } catch {
        /* 取不到就保留屏幕上的临时卡片，文字不会丢 */
      }
    }
  }

  const patchPending = (tempId: string, patch: Partial<PendingCard>): void => {
    set((s) => (s.pending && s.pending.tempId === tempId ? { pending: { ...s.pending, ...patch } } : s))
  }

  return {
    paperId: null,
    queue: [],
    cards: [],
    chains: [],
    conversations: [],
    activeConversation: null,
    activeChain: null,
    pending: null,
    loading: false,
    error: null,

    load: async (paperId) => {
      controller?.abort()
      set({ paperId, queue: [], cards: [], chains: [], conversations: [], activeConversation: null, activeChain: null, pending: null, loading: true, error: null })
      try {
        const [cards, chains, convs] = await Promise.all([api.cards.list(paperId), api.chains.list(paperId), api.conversations.list(paperId)])
        if (get().paperId !== paperId) return
        set({ cards: cards.items, chains: chains.items, conversations: convs.items, activeConversation: convs.items[0]?.id ?? null, loading: false })
      } catch (err) {
        if (get().paperId === paperId) set({ loading: false, error: errorMessage(err) })
      }
    },

    reset: () => {
      controller?.abort()
      set({ paperId: null, queue: [], cards: [], chains: [], conversations: [], activeConversation: null, activeChain: null, pending: null, loading: false, error: null })
    },

    ask: async (input) => {
      const { paperId, activeChain, activeConversation, pending } = get()
      if (!paperId) return
      if (pending?.status === 'streaming') {
        // 修改重答要对着当前最后一张卡片，排队后它可能已不是最后一张：不排队
        if (input.editCardId) return
        set((s) => ({ queue: [...s.queue, { id: `queued-${++seq}`, input }] }))
        return
      }
      const tempId = `pending-${++seq}`
      const ctrl = new AbortController()
      controller = ctrl
      set({
        pending: {
          tempId,
          question: input.question,
          intent: input.intent,
          selection: input.selection ?? null,
          attachmentNames: input.attachments?.map((a) => a.name),
          meta: null,
          text: '',
          status: 'streaming',
          error: null,
          escalatedTo: null,
          equationId: input.equationId,
          latex: null,
          stage: input.equationId != null ? '正在识别公式…' : input.figureId != null ? '正在看图…' : null,
          editingCardId: input.editCardId,
          figureId: input.figureId
        }
      })

      const body: AskRequest = {
        paper_id: paperId,
        question: input.question,
        intent: input.intent,
        selection: input.selection ?? null,
        chain_id: activeChain && activeChain !== TEMP_CHAIN ? activeChain : null,
        save: input.save ?? null,
        conversation_id: activeConversation,
        web: usePrefs.getState().askWeb,
        attachments: input.attachments,
        edit_card_id: input.editCardId ?? null
      }

      const stream = (cb: (event: string, data: unknown) => void, signal: AbortSignal): Promise<void> =>
        input.equationId != null
          ? visionApi.explainEquation(
              { paper_id: paperId, equation_id: input.equationId, question: input.question, chain_id: body.chain_id, conversation_id: activeConversation, save: input.save ?? null },
              cb,
              signal
            )
          : input.figureId != null
            ? visionApi.explainFigure(
                { paper_id: paperId, figure_id: input.figureId, question: input.question, chain_id: body.chain_id, conversation_id: activeConversation, save: input.save ?? null },
                cb,
                signal
              )
            : api.ai.ask(body, cb, signal)

      let finished = false
      try {
        await stream(
          (event, data) => {
            const d = data as Record<string, unknown>
            switch (event) {
              case 'meta': {
                const meta = data as AskMeta
                patchPending(tempId, { meta, escalatedTo: meta.escalated ? meta.model : null })
                const cid = (data as { conversation_id?: string }).conversation_id
                if (cid && cid !== get().activeConversation) void refreshConversations(paperId, cid)
                break
              }
              case 'needs_supplement_permission': {
                const needs = data as NeedsSupplementPermission
                useMaterialsStore.getState().noteNeeds(needs)
                patchPending(tempId, { supplementNeeds: needs })
                break
              }
              case 'stage':
                patchPending(tempId, { stage: typeof d.message === 'string' ? d.message : null })
                break
              case 'latex':
                patchPending(tempId, { latex: data as EquationLatex })
                break
              case 'delta': {
                const text = typeof d.text === 'string' ? d.text : ''
                set((s) => (s.pending && s.pending.tempId === tempId ? { pending: { ...s.pending, text: s.pending.text + text } } : s))
                break
              }
              case 'escalate':
                patchPending(tempId, { text: '', escalatedTo: typeof d.model === 'string' ? d.model : '强模型' })
                break
              case 'done': {
                finished = true
                const card = d.card as InsightCard
                set((s) => {
                  const cards = s.cards.some((c) => c.id === card.id) ? s.cards.map((c) => (c.id === card.id ? card : c)) : [...s.cards, card]
                  // 答完的卡片必须还看得见：它属于别的对话、或当前停在“临时卡片”而它是已保存的，换成能显示它的视图，别让刚生成完的回答凭空消失
                  const conv = card.conversation_id && s.activeConversation !== card.conversation_id ? card.conversation_id : s.activeConversation
                  const chain = s.activeChain === TEMP_CHAIN && card.saved ? null : s.activeChain !== null && s.activeChain !== TEMP_CHAIN && card.chain_id && card.chain_id !== s.activeChain ? null : s.activeChain
                  return { cards, activeConversation: conv, activeChain: chain, pending: s.pending?.tempId === tempId ? null : s.pending }
                })
                void refreshChains(paperId)
                void refreshConversations(paperId)
                // 这一条正常答完：队列里等着的下一条自动发出
                queueMicrotask(() => get().sendQueuedNow())
                break
              }
              case 'error': {
                finished = true
                patchPending(tempId, {
                  status: 'failed',
                  error: { code: String(d.code ?? 'internal'), message: String(d.message ?? '生成失败') }
                })
                break
              }
              default:
                break
            }
          },
          ctrl.signal
        )
        if (!finished) {
          if (ctrl.signal.aborted) {
            // 点了“停止”：流是正常结束的，不是出错。已经输出的内容留着（后端也把它存成了卡片）
            patchPending(tempId, { status: 'stopped' })
            void keepStopped(paperId, tempId)
          } else {
            patchPending(tempId, { status: 'failed', error: { code: 'network', message: '连接意外中断，没有收到完整的回答。' } })
          }
        }
      } catch (err) {
        if (ctrl.signal.aborted) {
          patchPending(tempId, { status: 'stopped' })
          void keepStopped(paperId, tempId)
        } else {
          const code = err instanceof ApiError ? err.code : 'internal'
          patchPending(tempId, { status: 'failed', error: { code, message: errorMessage(err) } })
        }
      }
    },

    stop: () => controller?.abort(),
    removeQueued: (id) => set((s) => ({ queue: s.queue.filter((q) => q.id !== id) })),
    sendQueuedNow: () => {
      const { queue, pending, paperId } = get()
      if (!paperId || queue.length === 0 || pending?.status === 'streaming') return
      const [next, ...rest] = queue
      set({ queue: rest })
      void get().ask(next.input)
    },
    dismissPending: () => set({ pending: null }),
    setActiveChain: (activeChain) => set({ activeChain }),

    newConversation: async () => {
      const { paperId, conversations, activeConversation, cards, pending } = get()
      if (!paperId || pending?.status === 'streaming') return
      // 当前对话还是空的：不用再开，直接接着用
      const cur = conversations.find((c) => c.id === activeConversation)
      if (cur && !cards.some((c) => c.conversation_id === cur.id)) return
      try {
        const conv = await api.conversations.create(paperId)
        if (get().paperId !== paperId) return
        set((s) => ({ conversations: [conv, ...s.conversations], activeConversation: conv.id, activeChain: null, pending: null, queue: [] }))
      } catch (err) {
        toast.error('新建对话失败', errorMessage(err))
      }
    },

    switchConversation: (id) => {
      if (get().pending?.status === 'streaming') return
      set({ activeConversation: id, activeChain: null, pending: null, queue: [] })
    },

    renameConversation: async (id, title) => {
      try {
        const conv = await api.conversations.rename(id, title)
        set((s) => ({ conversations: s.conversations.map((c) => (c.id === id ? conv : c)) }))
      } catch (err) {
        toast.error('重命名失败', errorMessage(err))
      }
    },

    removeConversation: async (id) => {
      try {
        await api.conversations.remove(id)
        const paperId = get().paperId
        if (!paperId) return
        const [cards, convs] = await Promise.all([api.cards.list(paperId), api.conversations.list(paperId)])
        set((s) => ({
          cards: cards.items,
          conversations: convs.items,
          activeConversation: s.activeConversation === id ? (convs.items[0]?.id ?? null) : s.activeConversation
        }))
      } catch (err) {
        toast.error('删除对话失败', errorMessage(err))
      }
    },

    setSaved: async (id, saved) => {
      const prev = get().cards
      set({ cards: prev.map((c) => (c.id === id ? { ...c, saved } : c)) })
      try {
        const card = await api.cards.patch(id, { saved })
        set((s) => ({ cards: s.cards.map((c) => (c.id === id ? card : c)) }))
        const paperId = get().paperId
        if (paperId) void refreshChains(paperId)
        toast.success(saved ? '已保存到分析链' : '已取消保存')
      } catch (err) {
        set({ cards: prev })
        toast.error('操作失败', errorMessage(err))
      }
    },

    removeCard: async (id) => {
      const prev = get().cards
      set({ cards: prev.filter((c) => c.id !== id) })
      try {
        await api.cards.remove(id)
        const paperId = get().paperId
        if (paperId) void refreshChains(paperId)
      } catch (err) {
        set({ cards: prev })
        toast.error('删除失败', errorMessage(err))
      }
    },

    renameChain: async (id, title) => {
      try {
        const chain = await api.chains.rename(id, title)
        set((s) => ({ chains: s.chains.map((c) => (c.id === id ? chain : c)) }))
      } catch (err) {
        toast.error('重命名失败', errorMessage(err))
      }
    },

    removeChain: async (id) => {
      try {
        await api.chains.remove(id)
        set((s) => ({
          cards: s.cards.filter((c) => c.chain_id !== id),
          chains: s.chains.filter((c) => c.id !== id),
          activeChain: s.activeChain === id ? null : s.activeChain
        }))
      } catch (err) {
        toast.error('删除分析链失败', errorMessage(err))
      }
    }
  }
})
