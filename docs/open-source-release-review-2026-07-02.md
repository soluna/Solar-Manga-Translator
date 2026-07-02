# Solar-Manga-Translator 开源发布复审

- 复审日期：2026-07-02
- 范围：源码、Git 历史、运行时数据边界、依赖、前后端测试、Electron
  暂存内容、许可证和 GitHub 发布配置
- 源码仓库结论：**可以公开**
- Windows 安装包结论：**暂不发布正式安装包**

本文记录 2026-07-02 实际完成的修复和验证结果，取代此前的开源准备审计。

## 分级标准

| 级别 | 含义 | 发布规则 |
| --- | --- | --- |
| P0 | 隐私、数据丢失或外部用户无法安装的发布阻断项 | 公开源码前必须关闭 |
| P1 | 安装包、供应链或高影响质量风险 | 发布正式安装包前关闭 |
| P2 | 文档、贡献体验和长期维护问题 | 可以在公开后持续改进 |

## P0 关闭情况

### P0-01 Git 作者邮箱隐私

状态：**已关闭**

- 仓库级 Git 邮箱已改为 GitHub noreply 地址。
- 所有可达提交的 author 和 committer 邮箱已重写。
- 重写前已在仓库外创建并验证完整 Git bundle 备份。
- 已删除不再使用的本地工作分支。
- 最终检查要求：`git log --all --format='%ae' | sort -u` 只能返回 noreply
  地址，GitHub 远端只能暴露重写后的 `main`。

### P0-02 项目路径穿越和数据删除

状态：**已关闭**

- `backend/engine/translator.py` 现在统一校验项目 ID 和页面 ID。
- 项目、输出、页面文档、缓存、日志、ZIP 和临时目录都经过根目录包含检查。
- 项目 ID 只允许兼容 UUID 和旧项目的安全字符集合。
- 编码后的 `..` 删除请求现在返回 HTTP 400。
- 回归测试使用应用数据哨兵文件确认非法请求不会删除或越界写入。

### P0-03 全新环境无法安装固定上游版本

状态：**已关闭**

- `backend/install_deps.py` 不再先 checkout 上游默认分支。
- 新安装会初始化空 Git checkout，只 fetch 并 detached checkout
  `backend/upstream.json` 指定的 commit。
- 已有 checkout 仍会检测本地修改，不会静默覆盖用户文件。
- 独立测试仓库覆盖“上游已经前进”和“已有 checkout 有本地修改”两种情况。
- CI 已纳入该测试。

## P1 状态

### P1-01 Windows 数据目录和迁移

状态：**已关闭**

- 项目、输出、模型、日志和字体使用
  `%LOCALAPPDATA%/Solar-Manga-Translator`。
- 旧版本曾使用的 `%APPDATA%/Solar-Manga-Translator` 同名目录已加入迁移来源。
- 迁移测试确认历史项目可以复制到当前目录。
- “打开字体文件夹”与后端使用同一应用数据根目录。

### P1-02 Python 依赖漏洞

状态：**当前已关闭，发布时需重跑**

- 最低版本已提高到 `torch>=2.12.1` 和 `torchvision>=0.27.1`。
- 当前测试环境 `pip-audit` 报告 0 个已知漏洞。
- 正式安装包必须对最终 Windows Python runtime 再次运行审计。

### P1-03 Windows 安装包发布流程

状态：**未关闭，不阻止公开源码**

正式安装包发布前仍需：

1. 在干净 Windows 10/11 x64 VM 构建。
2. 完成安装、升级、迁移、卸载和 GPU/CPU 启动测试。
3. 生成 SBOM、第三方许可证清单和 SHA-256 校验文件。
4. 决定并实施 Windows 代码签名。
5. 对最终安装包而不只是暂存目录执行恶意软件、密钥和私有内容扫描。

在此之前，README 应继续说明项目主要以源码运行，不提供正式预编译安装包。

### P1-04 Python 完全可复现构建

状态：**部分关闭**

- 上游源码 commit 已固定。
- 前端和 Electron 有 npm lockfile。
- Python requirements 仍包含兼容范围和平台相关 ML 包。

公开源码可以接受这一状态；正式安装包应从干净 Windows 构建生成约束文件或带 hash
的锁定清单，并与 SBOM 一起归档。

### P1-05 GitHub 仓库保护

状态：**公开后立即配置**

仓库改为 public 后应：

1. 启用 Dependabot alerts 和 security updates。
2. 启用 secret scanning、push protection 和 private vulnerability reporting。
3. 为 `main` 添加 ruleset 或 branch protection，要求 CI 通过。
4. 禁止普通 force push，仅保留经审计的管理员应急流程。

## 已完成的清理

### 从 Git 删除或移动

- 删除失去配套评估文档的 `scripts/create_eval_record.py`。
- 删除已经失真的旧开源审计文档。
- 删除只做转发的旧 E2E 包装脚本。
- 把 canvas smoke HTML 从 `frontend/public/` 移到
  `frontend/test-fixtures/`，生产构建不再复制它。
- CI 会拒绝把 dev、test 或 smoke 页面重新放入 `frontend/public/`。
- changelog 已改为准确描述 3 个 OFL 许可的预置字体。
- npm lockfile 已统一使用官方 npm registry。
- GitHub Actions 已固定到完整 commit SHA。

### 本地敏感文件

- 曾位于项目工作目录中的忽略私钥已迁到用户 SSH 目录并设置为 `0600`。
- 该文件从未进入 Git 历史、源码归档或 Electron 暂存目录。

### 不应清理的用户数据

以下目录不是“无用缓存”，不得在通用清理命令中删除：

- `fonts/custom/`：用户自定义字体。
- 旧 `backend/temp_uploads/`、`backend/output_images/`：可能包含尚未迁移的项目和输出。
- 上游 `models/`：可重新下载但体积大，删除前应确认下载能力。
- 上游 `result/`：可能包含用户图片和翻译结果。

## 许可证和分发内容

- 主项目许可证：GPL-3.0。
- 固定上游 `manga-image-translator`：GPL-3.0-only。
- 3 个 Source Han Sans SC 预置字体：SIL Open Font License 1.1。
- 可选 AnimeMangaInpainting 模型：上游模型仓库声明 MIT；项目记录了来源和预期
  SHA-256。
- Git 跟踪内容和桌面暂存内容不包含漫画原图、翻译成品、用户字体或模型权重。

## 验证结果

### 功能和回归

- 后端：95 项 `unittest` 全部通过。
- 前端：Vite production build 通过。
- 前端：canvas smoke 和完整 V2 工作区 Playwright E2E 通过。
- 当前界面卡片按钮布局改动包含在上述构建和 E2E 中。
- Desktop：所有 Node 入口语法检查和 runtime path 测试通过。
- Electron 暂存：允许列表生成成功。

### 安全和依赖

- 编码后的父目录删除请求返回 HTTP 400。
- `frontend` npm audit：0 个已知漏洞。
- `desktop` npm audit：0 个已知漏洞。
- Python runtime pip-audit：0 个已知漏洞。
- Electron 暂存目录 Gitleaks：无密钥命中。
- 高风险前端模式只出现在隔离测试夹具，不进入 production build。

### 发行内容

- Vite `dist/` 不包含开发 smoke 页面。
- Electron 暂存只包含 3 个允许的系统预置字体，`fonts/custom/` 为空。
- 暂存内容不包含 `.git`、模型、结果、临时上传、`.env` 或绝对开发机路径。

## 最终发布判断

源码仓库满足公开条件，但应按以下顺序操作：

1. 对重写后的 Git 历史和最终源码归档执行最后一次 Gitleaks、个人路径、媒体和大文件扫描。
2. 使用带旧远端 commit 保护的 `force-with-lease` 推送重写后的 `main`。
3. 等待 GitHub Actions 在远端通过。
4. 将仓库改为 public。
5. 立即启用 P1-05 所列 GitHub 安全功能和分支保护。

Windows 安装包继续保持未正式发布状态，直到 P1-03 和 P1-04 的安装包门禁完成。
