import { app, BrowserWindow, dialog, ipcMain, shell, type IpcMainInvokeEvent } from 'electron'
import { IPC, type AppInfo, type BackendInfo, type BackendStatus } from '../shared/bridge'
import type { BackendManager } from './backend'
import type { SecretStore } from './secrets'

export const API_KEY_NAME = 'ai_api_key'
export const OPENALEX_KEY_NAME = 'openalex_api_key'
export const S2_KEY_NAME = 's2_api_key'

interface IpcContext {
  backend: BackendManager
  secrets: SecretStore
  dataDir: string
  getWindow: () => BrowserWindow | null
}

/** 只信任本应用自己加载的页面（打包后的 file:// 或开发期的 dev server）。 */
function isTrusted(e: IpcMainInvokeEvent): boolean {
  const url = e.senderFrame?.url ?? ''
  const dev = process.env.ELECTRON_RENDERER_URL
  if (dev && url.startsWith(dev)) return true
  return url.startsWith('file://')
}

function guard<T extends unknown[], R>(fn: (e: IpcMainInvokeEvent, ...args: T) => R | Promise<R>) {
  return async (e: IpcMainInvokeEvent, ...args: T): Promise<R> => {
    if (!isTrusted(e)) throw new Error('未授权的调用来源')
    return fn(e, ...args)
  }
}

/** 把已加密保存的 API Key 推送到后端内存（后端绝不落盘）。日志中不包含 Key。 */
export async function pushApiKey(backend: BackendManager, secrets: SecretStore): Promise<void> {
  const info = backend.info
  if (backend.status !== 'ready' || !info) return
  const key = secrets.get(API_KEY_NAME)
  const url = `${info.baseUrl}/api/settings/ai/secret`
  const headers = { Authorization: `Bearer ${info.token}`, 'Content-Type': 'application/json' }
  const res = key
    ? await fetch(url, { method: 'PUT', headers, body: JSON.stringify({ api_key: key }) })
    : await fetch(url, { method: 'DELETE', headers })
  if (!res.ok) throw new Error(`同步 API Key 到本地服务失败（HTTP ${res.status}）`)
}

/** 同样把可选的 OpenAlex API Key 推送到后端内存（只会发给 api.openalex.org）。没有保存时什么也不做。 */
export async function pushOpenAlexKey(backend: BackendManager, secrets: SecretStore): Promise<void> {
  const info = backend.info
  if (backend.status !== 'ready' || !info) return
  const key = secrets.get(OPENALEX_KEY_NAME)
  if (!key) return
  const res = await fetch(`${info.baseUrl}/api/settings/openalex-key`, {
    method: 'PUT',
    headers: { Authorization: `Bearer ${info.token}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: key })
  })
  if (!res.ok) throw new Error(`同步 OpenAlex Key 到本地服务失败（HTTP ${res.status}）`)
}

/** 可选的 Semantic Scholar API Key（免费申请）：推送到后端内存，只会发给 api.semanticscholar.org。没有保存时清掉后端那份。 */
export async function pushS2Key(backend: BackendManager, secrets: SecretStore): Promise<void> {
  const info = backend.info
  if (backend.status !== 'ready' || !info) return
  const key = secrets.get(S2_KEY_NAME)
  const res = await fetch(`${info.baseUrl}/api/settings/s2-key`, {
    method: key ? 'PUT' : 'DELETE',
    headers: { Authorization: `Bearer ${info.token}`, 'Content-Type': 'application/json' },
    ...(key ? { body: JSON.stringify({ api_key: key }) } : {})
  })
  if (!res.ok) throw new Error(`同步 Semantic Scholar Key 到本地服务失败（HTTP ${res.status}）`)
}

export function registerIpc(ctx: IpcContext): void {
  const { backend, secrets } = ctx

  ipcMain.handle(
    IPC.backendInfo,
    guard((): BackendInfo => {
      if (!backend.info || backend.status !== 'ready') throw new Error('本地服务尚未就绪')
      return backend.info
    })
  )
  ipcMain.handle(
    IPC.backendStatus,
    guard((): BackendStatus => backend.status)
  )
  ipcMain.handle(
    IPC.backendRetry,
    guard(async () => {
      await backend.restart()
    })
  )

  ipcMain.handle(
    IPC.pickPdfFiles,
    guard(async (): Promise<string[]> => {
      const win = ctx.getWindow()
      const opts = {
        title: '选择 PDF 论文',
        properties: ['openFile', 'multiSelections'] as Array<'openFile' | 'multiSelections'>,
        filters: [{ name: 'PDF 文档', extensions: ['pdf'] }]
      }
      const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts)
      return r.canceled ? [] : r.filePaths
    })
  )
  ipcMain.handle(
    IPC.pickSupplementFiles,
    guard(async (): Promise<string[]> => {
      const win = ctx.getWindow()
      const opts = {
        title: '选择补充材料文件',
        properties: ['openFile', 'multiSelections'] as Array<'openFile' | 'multiSelections'>,
        filters: [
          { name: '常见补充材料', extensions: ['pdf', 'docx', 'xlsx', 'xlsm', 'csv', 'tsv', 'json', 'zip', 'txt', 'md'] },
          { name: '所有文件', extensions: ['*'] }
        ]
      }
      const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts)
      return r.canceled ? [] : r.filePaths
    })
  )
  ipcMain.handle(
    IPC.pickFolder,
    guard(async (): Promise<string | null> => {
      const win = ctx.getWindow()
      const opts = { title: '选择包含 PDF 的文件夹', properties: ['openDirectory'] as Array<'openDirectory'> }
      const r = win ? await dialog.showOpenDialog(win, opts) : await dialog.showOpenDialog(opts)
      return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0]
    })
  )

  ipcMain.handle(
    IPC.setApiKey,
    guard(async (_e, key: unknown) => {
      if (typeof key !== 'string' || !key.trim() || key.length > 4096) throw new Error('API Key 格式不正确')
      secrets.set(API_KEY_NAME, key.trim())
      await pushApiKey(backend, secrets)
    })
  )
  ipcMain.handle(
    IPC.clearApiKey,
    guard(async () => {
      secrets.clear(API_KEY_NAME)
      await pushApiKey(backend, secrets)
    })
  )
  ipcMain.handle(
    IPC.hasApiKey,
    guard((): boolean => secrets.has(API_KEY_NAME))
  )

  ipcMain.handle(
    IPC.setS2Key,
    guard(async (_e, key: unknown) => {
      if (typeof key !== 'string' || !key.trim() || key.length > 512) throw new Error('Semantic Scholar API Key 格式不正确')
      secrets.set(S2_KEY_NAME, key.trim())
      await pushS2Key(backend, secrets)
    })
  )
  ipcMain.handle(
    IPC.clearS2Key,
    guard(async () => {
      secrets.clear(S2_KEY_NAME)
      await pushS2Key(backend, secrets)
    })
  )
  ipcMain.handle(
    IPC.hasS2Key,
    guard((): boolean => secrets.has(S2_KEY_NAME))
  )

  ipcMain.handle(
    IPC.openExternal,
    guard(async (_e, url: unknown) => {
      if (typeof url !== 'string') throw new Error('链接无效')
      let parsed: URL
      try {
        parsed = new URL(url)
      } catch {
        throw new Error('链接无效')
      }
      if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:' && parsed.protocol !== 'mailto:') throw new Error('仅允许打开 http、https 和 mailto 链接')
      await shell.openExternal(parsed.toString())
    })
  )

  ipcMain.handle(
    IPC.appInfo,
    guard(
      (): AppInfo => ({
        version: app.getVersion(),
        dataDir: ctx.dataDir,
        platform: process.platform
      })
    )
  )
}
