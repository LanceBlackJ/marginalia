import { app, BrowserWindow, Menu, clipboard, dialog, session, shell, type DownloadItem, type Session } from 'electron'
import { mkdirSync } from 'node:fs'
import { basename, join } from 'node:path'
import { IPC, type PaperDownload } from '../shared/bridge'

/**
 * 内置的“论文下载浏览器”：一个独立的小窗口，用来打开出版社 / arXiv / 机构库的页面。
 * - 用固定的持久分区（persist:paper-browser）：在这里登录过的账号（机构登录、出版社账号）下次还在，且和应用本身的页面、系统浏览器都隔离；
 * - 页面里点到 PDF（不管是下载链接还是浏览器内嵌显示）都会被截下来存到临时目录，再通知主窗口导入——不用先存到哪里再手动选；
 * - 窗口里不放任何应用的接口（没有 preload、开沙箱），页面只当普通网页对待；账号密码由用户在网页里自己输入，应用不读取也不保存。
 */
export const PAPER_PARTITION = 'persist:paper-browser'

let win: BrowserWindow | null = null
let wired = false

export function isHttpUrl(url: string): boolean {
  try {
    const u = new URL(url)
    return u.protocol === 'http:' || u.protocol === 'https:'
  } catch {
    return false
  }
}

/** 不含路径分隔符、不含奇怪字符的文件名，且一定以 .pdf 结尾。 */
export function safePdfName(name: string): string {
  const base = basename(name || 'paper.pdf').replace(/[^\w.\-()一-鿿 ]+/g, '_').slice(0, 120) || 'paper.pdf'
  return /\.pdf$/i.test(base) ? base : `${base}.pdf`
}

export function looksLikePdf(item: Pick<DownloadItem, 'getMimeType' | 'getFilename'>): boolean {
  return /pdf/i.test(item.getMimeType() || '') || /\.pdf$/i.test(item.getFilename() || '')
}

function wireSession(ses: Session, notify: (d: PaperDownload) => void): void {
  if (wired) return
  wired = true
  ses.setPermissionRequestHandler((_wc, _perm, cb) => cb(false)) // 摄像头、定位、通知等一律不给
  // 窗口里直接打开的 PDF（出版社常把 PDF 内嵌显示）：改成下载，这样才能被截下来导入
  ses.webRequest.onHeadersReceived((details, cb) => {
    const headers = details.responseHeaders ?? {}
    const type = Object.entries(headers).find(([k]) => k.toLowerCase() === 'content-type')?.[1]?.[0] ?? ''
    if (details.resourceType === 'mainFrame' && /application\/pdf/i.test(type)) {
      const next = { ...headers }
      for (const k of Object.keys(next)) if (k.toLowerCase() === 'content-disposition') delete next[k]
      next['Content-Disposition'] = ['attachment']
      return cb({ responseHeaders: next })
    }
    cb({ responseHeaders: headers })
  })
  ses.on('will-download', (_e, item) => {
    if (!looksLikePdf(item)) return // 其它文件（补充材料压缩包等）走系统默认的“另存为”
    const dir = join(app.getPath('temp'), 'marginalia-downloads')
    mkdirSync(dir, { recursive: true })
    const name = safePdfName(item.getFilename())
    const path = join(dir, `${Date.now()}-${name}`)
    item.setSavePath(path)
    item.once('done', (_ev, state) => {
      if (state === 'completed') notify({ path, filename: name, url: item.getURL() })
      else if (state === 'interrupted') {
        void dialog.showMessageBox({ type: 'warning', message: '下载没有完成', detail: '网络中断，或者需要先登录/有访问权限。可以重新点一次下载。' })
      }
    })
  })
}

export function openPaperBrowser(url: string, getMainWindow: () => BrowserWindow | null): void {
  const target = isHttpUrl(url) ? url : 'about:blank'
  const ses = session.fromPartition(PAPER_PARTITION)
  wireSession(ses, (d) => {
    const main = getMainWindow()
    if (main && !main.isDestroyed()) {
      main.webContents.send(IPC.paperDownloaded, d)
      if (main.isMinimized()) main.restore()
    }
    if (win && !win.isDestroyed()) win.setTitle(`已下载：${d.filename} — 论文下载浏览器`)
  })
  if (win && !win.isDestroyed()) {
    void win.loadURL(target)
    win.show()
    win.focus()
    return
  }
  win = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 720,
    minHeight: 520,
    title: '论文下载浏览器',
    autoHideMenuBar: false,
    webPreferences: { partition: PAPER_PARTITION, contextIsolation: true, nodeIntegration: false, sandbox: true, webSecurity: true }
  })
  const wc = win.webContents
  wc.setWindowOpenHandler(({ url: u }) => {
    if (isHttpUrl(u)) void wc.loadURL(u) // 新窗口的链接在同一个窗口里打开，不弹出一堆窗口
    return { action: 'deny' }
  })
  wc.on('will-navigate', (e, u) => {
    if (!isHttpUrl(u) && u !== 'about:blank') e.preventDefault()
  })
  wc.on('page-title-updated', () => {
    // 标题里带上域名，让用户看清自己在哪个网站上输入账号密码
    try {
      const host = new URL(wc.getURL()).host
      win?.setTitle(`${wc.getTitle() || host} — ${host}`)
    } catch {
      /* 空白页 */
    }
  })
  win.setMenu(
    Menu.buildFromTemplate([
      { label: '后退', accelerator: 'Alt+Left', click: () => wc.canGoBack() && wc.goBack() },
      { label: '前进', accelerator: 'Alt+Right', click: () => wc.canGoForward() && wc.goForward() },
      { label: '刷新', accelerator: 'F5', click: () => wc.reload() },
      { label: '复制网址', click: () => clipboard.writeText(wc.getURL()) },
      { label: '用系统浏览器打开', click: () => void shell.openExternal(wc.getURL()) },
      {
        label: '清除登录状态',
        click: () => {
          void dialog
            .showMessageBox(win!, { type: 'question', buttons: ['清除', '取消'], defaultId: 1, cancelId: 1, message: '清除这个窗口里保存的所有网站登录状态？', detail: '只影响“论文下载浏览器”，不影响应用本身和系统浏览器。' })
            .then(async (r) => {
              if (r.response === 0) await ses.clearStorageData()
            })
        }
      }
    ])
  )
  win.on('closed', () => {
    win = null
  })
  void win.loadURL(target)
}
