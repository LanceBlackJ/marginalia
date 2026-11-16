import type { BackendInfo } from '@shared/bridge'
import { loadString, saveString } from './storage'

const WEB_KEY = 'pd.web.backend'
const REMOTE_URL = (import.meta.env.VITE_PAPERDESK_BACKEND_URL ?? import.meta.env.NEXT_PUBLIC_PAPERDESK_BACKEND_URL ?? '').replace(/\/+$/, '')
const REMOTE_TOKEN = import.meta.env.VITE_PAPERDESK_BACKEND_TOKEN ?? import.meta.env.NEXT_PUBLIC_PAPERDESK_BACKEND_TOKEN ?? ''

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

function isRemoteUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'https:' || u.protocol === 'http:'
  } catch {
    return false
  }
}

function normalizeUrl(url: string): string {
  const value = url.trim()
  const withScheme = /^[a-z][a-z\d+.-]*:\/\//i.test(value) ? value : `http://${value}`
  return withScheme.replace(/\/+$/, '')
}

export function saveWebBackendInfo(info: BackendInfo): void {
  saveString(WEB_KEY, JSON.stringify(info))
}

export function clearWebBackendInfo(): void {
  window.localStorage.removeItem(WEB_KEY)
  current = null
}

export function connectWebBackend(url: string): BackendInfo {
  const baseUrl = normalizeUrl(url)
  if (!isRemoteUrl(baseUrl)) throw new Error('请输入电脑的局域网地址，例如 192.168.1.20:8765')
  const info = { baseUrl, token: '' }
  saveWebBackendInfo(info)
  setBackendInfo(info)
  return info
}

/**
 * Web 调试模式（没有 window.paperdesk，例如直接在浏览器里打开 dev server）：
 * 从 URL 参数、环境变量、当前页面来源或 localStorage 读取后端地址。
 */
export function resolveWebBackendInfo(): BackendInfo | null {
  if (REMOTE_URL && isRemoteUrl(REMOTE_URL)) {
    const info = { baseUrl: REMOTE_URL, token: REMOTE_TOKEN }
    saveString(WEB_KEY, JSON.stringify(info))
    return info
  }
  const params = new URLSearchParams(window.location.search)
  const backend = params.get('backend')
  const token = params.get('token')
  if (backend && isRemoteUrl(backend)) {
    const info = { baseUrl: normalizeUrl(backend), token: token ?? '' }
    saveWebBackendInfo(info)
    return info
  }
  if (window.location.protocol === 'http:' || window.location.protocol === 'https:') {
    const info = { baseUrl: window.location.origin, token: '' }
    saveWebBackendInfo(info)
    return info
  }
  const cached = loadString(WEB_KEY)
  if (cached) {
    try {
      const info = JSON.parse(cached) as BackendInfo
      if (info.baseUrl && isRemoteUrl(info.baseUrl)) return { baseUrl: normalizeUrl(info.baseUrl), token: info.token ?? '' }
    } catch {
      /* 缓存损坏则忽略 */
    }
  }
  return null
}
