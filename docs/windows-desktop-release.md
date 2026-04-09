# Windows 本地分享版发布说明

## 当前方案

第一版分享方案采用：

- Electron 作为 Windows 桌面壳
- 本地 Python 后端作为实际翻译服务
- 用户数据写入 `%LOCALAPPDATA%/MangaTranslator/`

这套方案的目标是让普通用户安装后即可运行，而不是再手工装 Python / Node / Git。

## 数据目录

桌面版启动后，所有可写数据都不再落到仓库目录，而是统一放到：

- `%LOCALAPPDATA%/MangaTranslator/`

目录结构：

- `projects/`
- `output/`
- `models/`
- `logs/`
- `cache/`
- `config/settings.json`

旧版仓库目录中的 `backend/temp_uploads`、`backend/output_images` 会在首次启动时提示迁移。

## 支持矩阵

第一版建议按下面的范围发布：

- 系统：Windows 10 / 11 x64
- GPU：NVIDIA + CUDA 推荐
- CPU：允许运行，但不承诺体验

建议在对外说明里明确：

- 推荐显卡与显存
- 首次模型下载大小
- 无 GPU 时会变慢

## 发布前检查

### 安装与启动

- 目标机器无需安装 Python / Node / Git
- 安装后可直接双击启动
- 关闭桌面窗口时，本地后端会一起退出

### 数据隔离

- 升级后历史项目保留
- 卸载默认不删除用户数据
- 应用安装目录不承担用户写入

### 运行时健康

- 后端仅监听 `127.0.0.1`
- 端口为动态分配
- `config/settings.json` 可正确保存翻译设置与 API Key
- 日志写入 `logs/backend.log`

### 模型下载

当前策略是不把所有模型直接塞进安装包，而是在首次使用时下载到：

- `%LOCALAPPDATA%/MangaTranslator/models/`

对外发布前，建议至少验证：

- 国内网络环境下首次下载是否可完成
- 下载失败后的重试体验
- 模型目录空间不足时的错误提示

## 打包流程

### 1. 准备 Windows 后端运行时

先在 Windows 上确保 `backend/venv` 可正常运行。

### 2. 构建前端

```powershell
cd \path\to\manga-translator\frontend
npm install
npm run build
```

### 3. 构建桌面壳

```powershell
cd \path\to\manga-translator\desktop
npm install
npm run dist:win
```

这个命令会自动：

1. 重新构建前端
2. 把前端 dist、后端源码、字体和 Python runtime 复制到 `desktop/resources-staging/`
3. 调用 `electron-builder` 输出 Windows 安装包

## 已实现的产品态能力

- 用户数据目录与应用目录分离
- API 地址不再写死为固定端口
- 桌面模式优先使用配置文件持久化，而不是只依赖浏览器 localStorage
- 首次启动引导
- 旧数据迁移提示
- 后端运行时诊断接口
- 日志尾部读取接口

## 还需要继续验证的地方

- Windows 真机打包与安装流程
- 便携式 Python runtime 在不同机器上的兼容性
- 模型下载与字体合法分发
- 桌面版自动更新策略
