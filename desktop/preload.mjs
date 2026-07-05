import { contextBridge, ipcRenderer } from 'electron'

function loadRuntime() {
  try {
    return JSON.parse(process.env.MANGA_TRANSLATOR_DESKTOP_RUNTIME || '{}')
  } catch {
    return {}
  }
}

contextBridge.exposeInMainWorld('mangaDesktop', {
  runtime: loadRuntime(),
  getRuntime: () => ipcRenderer.invoke('desktop:get-runtime'),
  revealPath: (targetPath) => ipcRenderer.invoke('desktop:reveal-path', targetPath),
  openLogs: () => ipcRenderer.invoke('desktop:open-logs'),
  openUserFonts: () => ipcRenderer.invoke('desktop:open-user-fonts'),
})
