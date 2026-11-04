import { create } from 'zustand'
import type { BackendStatus } from '@shared/bridge'
import { api } from '@/lib/api'
import { resolveWebBackendInfo, setBackendInfo } from '@/lib/backend'

interface BackendState {
  status: BackendStatus
  detail?: string
  /** 是否运行在 Electron 中（有 window.paperdesk） */
  electron: boolean
  init: () => Promise<void>
  retry: () => Promise<void>
}

let subscribed = false

async function adoptReady(set: (p: Partial<BackendState>) => void): Promise<void> {
  const bridge = window.paperdesk
  if (!bridge) return
  try {
    const info = await bridge.getBackendInfo()
    setBackendInfo(info)
    set({ status: 'ready', detail: undefined })
  } catch (err) {
    set({ status: 'failed', detail: err instanceof Error ? err.message : String(err) })
  }
}

async function initWeb(set: (p: Partial<BackendState>) => void): Promise<void> {
  const info = resolveWebBackendInfo()
  if (!info) {
    set({
      status: 'failed',
      detail: '当前不在 Electron 中运行，且没有找到本地服务地址。请用 ?backend=http://127.0.0.1:端口&token=令牌 打开页面。'
    })
    return
  }
  setBackendInfo(info)
  try {
    await api.health()
    set({ status: 'ready', detail: undefined })
  } catch {
    set({ status: 'failed', detail: `无法连接到 ${info.baseUrl}，请确认后端正在运行。` })
  }
}

export const useBackendStore = create<BackendState>((set, get) => ({
  status: 'starting',
  detail: undefined,
  electron: typeof window !== 'undefined' && !!window.paperdesk,

  init: async () => {
    const bridge = window.paperdesk
    if (!bridge) {
      set({ status: 'starting' })
      await initWeb(set)
      return
    }
    if (!subscribed) {
      subscribed = true
      bridge.onBackendStatus((status, detail) => {
        if (status === 'ready') void adoptReady(set)
        else set({ status, detail })
      })
    }
    const status = await bridge.getBackendStatus()
    if (status === 'ready') await adoptReady(set)
    else set({ status })
  },

  retry: async () => {
    const bridge = window.paperdesk
    set({ status: 'starting', detail: undefined })
    if (bridge) {
      try {
        await bridge.retryBackend()
      } catch (err) {
        set({ status: 'failed', detail: err instanceof Error ? err.message : String(err) })
      }
      // 之后的状态变化由 onBackendStatus 回调驱动；兜底再读一次
      const status = await bridge.getBackendStatus()
      if (status === 'ready' && get().status !== 'ready') await adoptReady(set)
    } else {
      await initWeb(set)
    }
  }
}))
