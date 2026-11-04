import { app, BrowserWindow, Menu, shell } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { BackendManager } from './backend'
import { SecretStore } from './secrets'
import { registerIpc, pushApiKey, pushOpenAlexKey, pushS2Key, API_KEY_NAME, OPENALEX_KEY_NAME } from './ipc'
import { appRoot, resolveDataDir } from './paths'
import { IPC } from '../shared/bridge'

// 项目更名前（PaperDesk）的数据（文献库、设置、加密保存的 API Key）在 %APPDATA%\PaperDesk：还在的话继续用它，不让已有用户“丢库”。
const legacyUserData = join(app.getPath('appData'), 'PaperDesk')
const useLegacyUserData = existsSync(legacyUserData) && !existsSync(join(app.getPath('appData'), 'Marginalia'))
app.setName('Marginalia')
if (useLegacyUserData) app.setPath('userData', legacyUserData)
// Windows 上窗口被其它应用遮挡时 Chromium 会把页面判为 hidden，requestAnimationFrame 停摆，
// pdf.js 渲染与流式回答的界面更新都会卡住。阅读器需要在后台继续工作，所以关闭遮挡检测。
app.commandLine.appendSwitch('disable-features', 'CalculateNativeWinOcclusion')

const isDev = !app.isPackaged
let mainWindow: BrowserWindow | null = null

const windowIcon = join(appRoot(), 'build', 'icon.png')

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1100,
    minHeight: 680,
    show: false,
    backgroundColor: '#F7F8FA',
    title: 'Marginalia',
    // 开发期窗口和任务栏默认是 Electron 的图标：显式指定。打包后 exe 自带 build/icon.ico，这里不存在就不设。
    ...(existsSync(windowIcon) ? { icon: windowIcon } : {}),
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      backgroundThrottling: false
    }
  })

  win.once('ready-to-show', () => win.show())

  // 外部链接一律交给系统浏览器，且只放行 http/https
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    return { action: 'deny' }
  })
  win.webContents.on('will-navigate', (event, url) => {
    const dev = process.env.ELECTRON_RENDERER_URL
    const internal = url.startsWith('file://') || (dev !== undefined && url.startsWith(dev))
    if (!internal) {
      event.preventDefault()
      if (/^https?:\/\//i.test(url)) void shell.openExternal(url)
    }
  })

  const devUrl = process.env.ELECTRON_RENDERER_URL
  if (isDev && devUrl) void win.loadURL(devUrl)
  else void win.loadFile(join(__dirname, '../renderer/index.html'))

  win.on('closed', () => {
    if (mainWindow === win) mainWindow = null
  })
  return win
}

if (!app.requestSingleInstanceLock()) {
  app.quit()
} else {
  const dataDir = resolveDataDir()
  const backend = new BackendManager({
    appRoot: appRoot(),
    dataDir,
    isPackaged: app.isPackaged,
    resourcesPath: process.resourcesPath
  })
  const secrets = new SecretStore(join(app.getPath('userData'), 'secrets.bin'))

  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore()
      mainWindow.focus()
    }
  })

  backend.on('status', (status: string, detail?: string) => {
    for (const w of BrowserWindow.getAllWindows()) {
      w.webContents.send(IPC.backendStatusEvent, status, detail)
    }
    if (status === 'ready') {
      pushApiKey(backend, secrets).catch((err: unknown) => {
        console.error('同步 API Key 失败：', err instanceof Error ? err.message : 'unknown error')
      })
      pushS2Key(backend, secrets).catch((err: unknown) => {
        console.error('同步 Semantic Scholar Key 失败：', err instanceof Error ? err.message : 'unknown error')
      })
      pushOpenAlexKey(backend, secrets).catch((err: unknown) => {
        console.error('同步 OpenAlex Key 失败：', err instanceof Error ? err.message : 'unknown error')
      })
    }
  })

  void app.whenReady().then(() => {
    // 本机预置的默认 API Key（见 SecretStore.importSeed）：用户不必在设置里手动填写。
    // 必须在 app ready 之后：系统加密（DPAPI）在此之前不可用。
    secrets.importSeed(join(appRoot(), '.local', 'default-api.json'), {
      api_key: API_KEY_NAME,
      openalex_api_key: OPENALEX_KEY_NAME
    })
    Menu.setApplicationMenu(null)
    registerIpc({ backend, secrets, dataDir, getWindow: () => mainWindow })
    mainWindow = createWindow()
    void backend.start()

    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) mainWindow = createWindow()
    })
  })

  app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') app.quit()
  })

  let quitting = false
  app.on('before-quit', (e) => {
    if (quitting) return
    quitting = true
    e.preventDefault()
    backend.stop().finally(() => app.quit())
    // 兜底：即使 stop 卡住也强制结束子进程
    setTimeout(() => backend.killNow(), 4000).unref()
  })
}
