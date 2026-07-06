# Solar-Manga-Translator 首次使用就绪度复审

- 复审日期：2026-07-05
- 补充验证：2026-07-06
- 视角：不了解项目结构、第一次从 GitHub 下载源码的 Windows 用户
- 重点环境：Windows 10/11 x64、NVIDIA GPU、RTX 50 / Blackwell
- 结论：源码版的主要首次运行阻断已经修复；正式 Windows 安装包仍需干净机器与真实 NVIDIA 硬件验收

## 分级标准

| 级别 | 定义 | 处理要求 |
| --- | --- | --- |
| P0 | 无法启动、无法完成核心流程、配置或项目数据丢失 | 必须立即修复 |
| P1 | 核心功能退化、失败后无法恢复、问题难以定位 | 发布下一个版本前修复 |
| P2 | 首次体验、提示、维护性或边缘环境问题 | 进入持续改进 |

## 本轮结论

| 编号 | 级别 | 问题 | 状态 |
| --- | --- | --- | --- |
| FR-01 | P0 | Windows 安装了 CPU 版 PyTorch，RTX 5060 Ti 被误报为没有 GPU | 已修复 |
| FR-02 | P0 | OpenAI Compatible URL、模型和引擎重启后丢失 | 已修复 |
| FR-03 | P1 | 手动画框与 OCR/翻译耦合，识别失败会导致框消失 | 已修复 |
| FR-04 | P1 | Windows 管理日志每次启动被覆盖，且没有打开/导出入口 | 已修复 |
| FR-05 | P1 | 源码启动被当成普通浏览器模式，不显示首次运行检查 | 已修复 |
| FR-06 | P1 | 依赖安装失败后脚本仍继续启动，产生半安装环境 | 已修复 |
| FR-07 | P1 | 固定使用 8000 端口，端口被占用时无法启动 | 已修复 |
| FR-08 | P1 | “保存并开始”只验证连接，不保证设置已经持久化 | 已修复 |
| FR-09 | P1 | GPU 诊断只返回可用/不可用，无法区分驱动、CPU wheel 和 CUDA 初始化 | 已修复 |
| FR-10 | P2 | 缺少正式签名安装包，用户仍需准备 Python、Node.js 和 Git | 未关闭 |
| FR-11 | P2 | 首次模型下载仍受网络、代理和模型源可用性影响 | 部分缓解 |
| FR-12 | P1 | Windows + RTX 50 的最终行为尚未在本轮 macOS 开发机上实测 | 待实机验收 |
| FR-13 | P0 | 删除 `venv` 后首次安装被不存在的 `torchaudio 2.12.1+cu130` 阻断 | 已修复 |
| FR-14 | P0 | PyTorch 官方索引临时返回空版本列表，再次阻断 Windows CUDA 安装 | 已修复 |
| FR-15 | P0 | 约 2 GB 的 CUDA wheel 静默下载，无进度、超时或中国大陆镜像回退 | 已修复 |

## 已修复问题

### FR-01：RTX 50 无法识别

**根因**

`start.bat` 曾从普通 PyPI 安装 `torch>=2.12.1`。Windows 环境可能得到 CPU
构建，后续诊断只调用 `torch.cuda.is_available()`，于是把“检测到 NVIDIA
显卡但 PyTorch 不支持 CUDA”显示成“未检测到 GPU”。

RTX 50 属于 Blackwell 架构，需要 CUDA 12.8 或更高版本。当前安全基线使用
PyTorch 2.12.1，并从 PyTorch 官方 CUDA 13.0 wheel 源安装。CUDA 13.x
要求 NVIDIA R580 或更高驱动。

**修复**

- 新增 `backend/runtime_bootstrap.py`。
- 启动时通过 `nvidia-smi` 读取显卡名称、驱动版本和计算能力。
- Windows/Linux 检测到现代 NVIDIA GPU 时，明确使用官方 `cu130` wheel。
- 旧架构 NVIDIA GPU 使用兼容的 `cu126` wheel。
- 没有 NVIDIA GPU 时明确使用 CPU wheel；macOS 继续使用 MPS。
- 已安装正确运行时后不会重复下载 PyTorch。
- RTX 50 驱动低于 R580 时，在下载前停止并给出更新驱动的明确原因。
- 打包 Windows runtime 前也执行相同检查。
- 运行时只安装项目实际使用的 `torch` 和 `torchvision`，不再要求未使用且
  没有对应 CUDA 13 wheel 的 `torchaudio 2.12.1`。
- Windows CUDA 安装直接使用与 Python 3.10/3.11、x64 和 CUDA 版本匹配的
  PyTorch 官方固定 wheel，不再依赖 pip 从索引页枚举版本。
- bootstrap 日志记录 Python 版本、系统、CPU 架构和 wheel 标签，后续可直接
  区分网络问题与不受支持的平台。
- Windows 会分别读取官方源与阿里云镜像的 256 KiB 样本并按实测速率排序。
- pip 下载进度直接显示在终端，同时通过 pip 原生日志写入 `bootstrap.log`。
- 连接连续 30 秒无数据时重试或切换；只要仍在接收数据就不设总下载时限，
  避免约 2 GB 的 CUDA wheel 在慢速网络下被误判超时。
- 官方源和镜像使用同一组 PyTorch 官方 SHA-256，镜像文件校验不一致时拒绝安装。

**诊断改进**

设置页现在区分：

- CUDA 已就绪；
- 检测到 NVIDIA，但安装了 CPU 版 PyTorch；
- CUDA 版 PyTorch 已安装，但驱动/CUDA 初始化失败；
- 未安装 PyTorch；
- 未检测到 NVIDIA，将使用 CPU；
- Apple Metal (MPS) 已就绪。

参考：

- [PyTorch 2.7 起支持 Blackwell 和 CUDA 12.8](https://pytorch.org/blog/pytorch-2-7/)
- [NVIDIA CUDA 兼容性与最低驱动版本](https://docs.nvidia.com/deploy/cuda-compatibility/minor-version-compatibility.html)

### FR-02：OpenAI Compatible 设置重启后丢失

**根因**

这里实际有两个相互叠加的问题：

1. 前端把带空默认值的浏览器配置覆盖到后端已保存设置上，导致 URL 和模型在界面中变空。
2. 后端保存时把 OpenAI Compatible 归一为内部的 `custom_openai`，重载时又把它错误解释成豆包。

**修复**

- 后端保存内部执行引擎的同时保留 `selected_translator`。
- 返回前端时使用用户实际选择的翻译服务名称。
- 浏览器偏好只补充后端未提供的 UI 字段，不再覆盖持久设置。
- “测试连接”和“保存并开始”会先等待设置保存成功；保存失败时不再继续显示验证成功。
- 新增后端保存/重载回归测试和真实浏览器 E2E。

### FR-03：手动画框失败后消失

**根因**

旧流程是：

```text
画框 -> OCR -> 翻译 -> 保存框
```

OCR 导入、模型加载、GPU 或翻译 API 任一环节失败，保存框的代码就不会执行。

**修复**

新流程改为：

```text
画框 -> 立即保存空白可编辑框 -> 单独 OCR -> 单独翻译
```

- OCR 前先把框、坐标和默认样式写入项目。
- OCR 失败时框保留，并记录 `recognition_status` 与脱敏错误。
- 翻译失败不影响 OCR 结果或框。
- 右侧手动框提供“识别此框/重新识别”操作。
- 用户可以跳过识别，直接填写译文。
- 失败场景有回归测试。

### FR-04：日志难读且重启后消失

**根因**

- `backend-managed.log` 和 `frontend-managed.log` 每次启动使用覆盖写入。
- Python 日志无大小上限。
- 设置页只显示日志路径，没有打开或导出能力。
- GPU、路径、设置状态和日志分散，用户不知道提交什么。

**修复**

- Python `backend.log` 改为 5 MB × 5 个备份的轮转日志。
- Electron stdout/stderr 和 Windows managed 日志改为追加并轮转。
- 日志格式统一为“时间 | 级别 | 模块 | 消息”。
- 设置页新增“打开日志目录”。
- 新增“导出诊断包”，包含运行时、GPU、路径、脱敏设置和最近日志。
- 诊断包会再次清理 Authorization、API Key、token、password 和 secret。
- API Key 不进入诊断 JSON。

日志目录在设置页显示。Windows 源码版默认为：

```text
%LOCALAPPDATA%\Solar-Manga-Translator\logs
```

`start.bat` 的启动时间改为 PowerShell 生成的纯数字格式，不再把本地化的中文
星期名称写入日志，避免重定向后出现乱码。

### FR-05 至 FR-09：首次启动可靠性

- Windows managed 源码模式现在设置 `APP_DESKTOP_MODE=1`，会显示首次设置。
- 首次设置会检查本地目录、磁盘、GPU 和预置字体。
- OpenAI Compatible 也被纳入“缺少 API Key”检查。
- Python、核心引擎、后端依赖和前端依赖任一步失败都会立即停止。
- 后端端口从 8000 开始自动寻找空闲端口。
- 前端会自动使用实际后端端口。
- 运行环境异常时可直接导出诊断包。

## 仍需处理或验证

### FR-10：没有正式 Windows 安装包

源码版仍要求用户安装 Git、Python 3.10/3.11 和 Node.js 22.12 以上版本。
这不会阻止公开源码，但会显著提高非开发用户的门槛。

正式安装包发布前仍需：

1. 在干净 Windows 10/11 x64 VM 构建。
2. 完成代码签名。
3. 自动生成 SBOM 和 SHA-256。
4. 测试安装、升级、卸载、数据保留和旧数据迁移。
5. 对最终 runtime 运行依赖漏洞与恶意软件扫描。

### FR-11：模型下载与中国大陆网络

本地 OCR、检测、擦除模型仍可能在首次使用时下载。启动预检能够确认磁盘和基础运行时，
但无法保证所有外部模型源在用户网络中可达。

后续建议：

- 为所有按需模型提供统一下载状态和重试入口；
- 显示模型来源、目标路径、文件大小和校验值；
- 支持用户配置 Hugging Face 镜像；
- 对部分下载保留 `.part` 文件并支持续传；
- 在翻译任务开始前集中显示缺少的模型。

### FR-12：Windows GPU 实机验收

本轮已完成自动测试和官方兼容矩阵核对，但开发环境不是 Windows/NVIDIA。合并发布前必须在
RTX 5060 Ti 或同代 RTX 50 机器上记录以下结果：

```text
nvidia-smi
backend\venv\Scripts\python.exe -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

期望结果：

- `torch` 带 CUDA 构建；
- `torch.version.cuda` 为 13.0；
- `torch.cuda.is_available()` 为 `True`；
- 设置页显示真实显卡名称；
- 完成一张合成测试图的检测、OCR、擦除和嵌字。

## 验证记录

本轮自动验证：

- 后端 unittest：110 项通过；
- OpenAI Compatible 保存/重载回归测试通过；
- RTX 50 CUDA 安装计划与 CPU wheel 诊断测试通过；
- 手动画框 OCR 失败保留测试通过；
- 日志轮转与诊断包脱敏测试通过；
- Vue/Vite 生产构建通过；
- Canvas 工作台真实浏览器 E2E 通过；
- E2E 覆盖后端设置保存、页面重载、项目恢复和设置界面；
- Electron 与桌面脚本静态语法检查通过。

## 发布判断

可以继续公开源码并让技术用户试用。对外说明应保持“源码版/测试阶段”，不要把当前状态描述为
已经完成 Windows 正式发行。下一步最高优先级不是继续增加功能，而是在干净 Windows +
RTX 50 环境按 FR-12 完成实机闭环，并收集导出的诊断包确认日志和错误提示对外部用户足够清楚。
