# Manga Translator Desktop

Windows 本地分享版的桌面壳在这个目录里。

## 目标

- 双击安装后即可启动，不要求用户自己装 Python / Node / Git
- 用户项目、输出、日志、模型和配置全部落在用户数据目录
- Electron 只负责窗口与进程编排，翻译主逻辑仍由本地 Python 后端提供

## 目录说明

- `main.mjs`
  - Electron 主进程
  - 启动本地后端
  - 注入动态 API 地址与运行时信息
- `preload.mjs`
  - 向前端暴露 `window.mangaDesktop`
- `scripts/dev.mjs`
  - 本地开发：启动 Vite dev server 后再启动 Electron
- `scripts/stage-runtime.mjs`
  - 把前端构建产物、后端源码、字体、Python runtime 复制到 `resources-staging/`
- `scripts/package-win.mjs`
  - 执行前端构建、资源 staging，并调用 `electron-builder`

## 本地开发

```bash
cd <repo>/desktop
npm install
npm run dev
```

默认会：

1. 启动 `frontend` 的 Vite dev server
2. 启动 Electron 窗口
3. 由 Electron 自动寻找可用 Python 并拉起 `backend/desktop_server.py`

## Windows 打包

建议在 Windows 机器上执行。

前提：

- 已经准备好 `backend/venv`
- 已经安装 `desktop` 目录下的 npm 依赖

命令：

```powershell
cd \path\to\manga-translator\desktop
npm install
npm run dist:win
```

产物默认输出到：

- `desktop/dist/`

## 运行时目录

桌面版会把可写数据统一放到：

- Windows: `%LOCALAPPDATA%/MangaTranslator/`

其中包含：

- `projects/`
- `output/`
- `models/`
- `logs/`
- `cache/`
- `config/`
