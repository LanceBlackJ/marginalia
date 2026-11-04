import type { BackendInfo } from '@shared/bridge'
import { loadString, saveString } from './storage'

const WEB_KEY = 'pd.web.backend'

let current: BackendInfo | null = null

export function setBackendInfo(info: BackendInfo | null): void {
  current = info
}

export function getBackendInfo(): BackendInfo {
  if (!current) throw new Error('本地服务尚未就绪')
  return current
}

export function hasBridge(): boolean {
  return typeof window !== 'undefined' && !!window.paperdesk
}

function isLocalUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'http:' && (u.hostname === '127.0.0.1' || u.hostname === 'localhost')
  } catch {
    return false
  }
}

/**
 * Web 调试模式（没有 window.paperdesk，例如直接在浏览器里打开 dev server）：
 * 从 URL 参数 ?backend=http://127.0.0.1:PORT&token=XXX 读取，并缓存到 localStorage。
 */
export function resolveWebBackendInfo(): BackendInfo | null {
  const params = new URLSearchParams(window.location.search)
  const backend = params.get('backend')
  const token = params.get('token')
  if (backend && token && isLocalUrl(backend)) {
    const info = { baseUrl: backend.replace(/\/+$/, ''), token }
    saveString(WEB_KEY, JSON.stringify(info))
    return info
  }
  const cached = loadString(WEB_KEY)
  if (cached) {
    try {
      const info = JSON.parse(cached) as BackendInfo
      if (info.baseUrl && info.token && isLocalUrl(info.baseUrl)) return info
    } catch {
      /* 缓存损坏则忽略 */
    }
  }
  return null
}
