import { contextBridge, ipcRenderer, webUtils } from 'electron'
import { IPC, type BackendStatus, type PaperDeskBridge, type PaperDownload } from '../shared/bridge'

const bridge: PaperDeskBridge = {
  getBackendInfo: () => ipcRenderer.invoke(IPC.backendInfo),
  getBackendStatus: () => ipcRenderer.invoke(IPC.backendStatus),
  onBackendStatus(cb) {
    const handler = (_e: unknown, status: BackendStatus, detail?: string): void => cb(status, detail)
    ipcRenderer.on(IPC.backendStatusEvent, handler)
    return () => {
      ipcRenderer.removeListener(IPC.backendStatusEvent, handler)
    }
  },
  retryBackend: () => ipcRenderer.invoke(IPC.backendRetry),
  pickPdfFiles: () => ipcRenderer.invoke(IPC.pickPdfFiles),
  pathsForFiles: (files) => files.map((f) => webUtils.getPathForFile(f)).filter(Boolean),
  pickSupplementFiles: () => ipcRenderer.invoke(IPC.pickSupplementFiles),
  pickFolder: () => ipcRenderer.invoke(IPC.pickFolder),
  setApiKey: (key) => ipcRenderer.invoke(IPC.setApiKey, key),
  clearApiKey: () => ipcRenderer.invoke(IPC.clearApiKey),
  hasApiKey: () => ipcRenderer.invoke(IPC.hasApiKey),
  setS2Key: (key) => ipcRenderer.invoke(IPC.setS2Key, key),
  clearS2Key: () => ipcRenderer.invoke(IPC.clearS2Key),
  hasS2Key: () => ipcRenderer.invoke(IPC.hasS2Key),
  openExternal: (url) => ipcRenderer.invoke(IPC.openExternal, url),
  openPaperBrowser: (url) => ipcRenderer.invoke(IPC.openPaperBrowser, url),
  onPaperDownloaded(cb) {
    const handler = (_e: unknown, d: PaperDownload): void => cb(d)
    ipcRenderer.on(IPC.paperDownloaded, handler)
    return () => {
      ipcRenderer.removeListener(IPC.paperDownloaded, handler)
    }
  },
  getAppInfo: () => ipcRenderer.invoke(IPC.appInfo)
}

contextBridge.exposeInMainWorld('paperdesk', bridge)
