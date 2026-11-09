/** Electron 主进程 ↔ 渲染进程桥接接口（docs/API.md §7）。main / preload / renderer 共用。 */

export type BackendStatus = 'starting' | 'ready' | 'failed'

export interface BackendInfo {
  baseUrl: string
  token: string
}

export interface AppInfo {
  version: string
  dataDir: string
  platform: string
}

/** 论文下载浏览器里截下来的一份 PDF（已存到临时目录，等待导入） */
export interface PaperDownload {
  path: string
  filename: string
  url: string
}

export interface PaperDeskBridge {
  getBackendInfo(): Promise<BackendInfo>
  getBackendStatus(): Promise<BackendStatus>
  onBackendStatus(cb: (status: BackendStatus, detail?: string) => void): () => void
  /** 契约之外的补充：后端失败后由用户点击“重试”触发主进程重新拉起。 */
  retryBackend(): Promise<void>
  pickPdfFiles(): Promise<string[]>
  /** 选择补充材料文件（PDF/表格/文档/压缩包/任意文件，可多选） */
  pickSupplementFiles(): Promise<string[]>
  pickFolder(): Promise<string | null>
  setApiKey(key: string): Promise<void>
  clearApiKey(): Promise<void>
  hasApiKey(): Promise<boolean>
  /** 可选的 Semantic Scholar API Key（免费申请）：只发给 api.semanticscholar.org */
  setS2Key(key: string): Promise<void>
  clearS2Key(): Promise<void>
  hasS2Key(): Promise<boolean>
  openExternal(url: string): Promise<void>
  /** 打开内置的论文下载浏览器（可登录账号）；页面里点到的 PDF 会被截下来，通过 onPaperDownloaded 通知导入 */
  openPaperBrowser(url: string): Promise<void>
  onPaperDownloaded(cb: (d: PaperDownload) => void): () => void
  getAppInfo(): Promise<AppInfo>
}

export const IPC = {
  backendInfo: 'paperdesk:backend:info',
  backendStatus: 'paperdesk:backend:status',
  backendStatusEvent: 'paperdesk:backend:status-event',
  backendRetry: 'paperdesk:backend:retry',
  pickPdfFiles: 'paperdesk:dialog:pick-pdf',
  pickSupplementFiles: 'paperdesk:dialog:pick-supplements',
  pickFolder: 'paperdesk:dialog:pick-folder',
  setApiKey: 'paperdesk:secret:set',
  clearApiKey: 'paperdesk:secret:clear',
  hasApiKey: 'paperdesk:secret:has',
  setS2Key: 'paperdesk:s2key:set',
  clearS2Key: 'paperdesk:s2key:clear',
  hasS2Key: 'paperdesk:s2key:has',
  openExternal: 'paperdesk:shell:open-external',
  openPaperBrowser: 'paperdesk:paper-browser:open',
  paperDownloaded: 'paperdesk:paper-browser:downloaded',
  appInfo: 'paperdesk:app:info'
} as const
