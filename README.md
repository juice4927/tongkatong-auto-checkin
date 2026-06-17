# 通卡通自动打卡

这是一个运行在 Windows 上的开源桌面自动化项目，使用 `Python + PyQt6 + uiautomator2 + APScheduler` 构建。它通过 MuMu 模拟器驱动目标 App，完成定时打卡、状态守护、异常恢复和发布打包流程。

## 项目概览

- 当前版本：见 `src/version.py`
- 运行平台：Windows
- 界面类型：`PyQt6` 原生桌面 GUI
- 自动化对象：MuMu 模拟器中的目标 App
- 发布形态：开源版，无授权校验

## 当前能力

- 支持上午签到、上午签退、下午签到、下午签退四个打卡点
- 支持随机延迟，避免每天固定到秒执行
- 支持周末、节假日跳过和补签窗口判断
- 支持守护恢复、调度恢复、设备重连和会话重置
- 支持导航异常自动恢复：先重进页面，再回主界面重试，最后重开目标 App
- 支持 GPS 设置、网络检测、结果确认和失败分类
- 支持托盘常驻、仪表盘状态汇总和日志追踪
- 支持自更新清单和 PyInstaller 打包

## 快速开始

首次使用时，按下面顺序配置最稳：

1. 启动 MuMu 模拟器，并确认模拟器内目标 App 已安装且可正常登录。
2. 确保本机 ADB 可用，且能连接到模拟器。
3. 手动在目标 App 里走一遍 `工作台 -> 考勤`，确认页面路径没有变化。
4. 安装项目依赖并启动程序。
5. 在设置页填写模拟器地址、端口、ADB 路径和目标 App 包名。
6. 点击 `测试连接`，确认设备联通正常。
7. 进入时间配置页，设置四个打卡点和随机范围。
8. 根据需要开启节假日跳过、通知、守护恢复和补签窗口。
9. 回到主界面，先点 `连接设备`，再点 `启动`。

## 运行方式

安装运行依赖：

```bash
pip install -r requirements.txt
```

启动程序：

```bash
python src/main.py
```

安装打包依赖：

```bash
pip install -r requirements-build.txt
```

## 使用说明

日常主要看这四个区域：

- 仪表盘：查看今日计划、最近结果、守护状态和恢复动作。
- 时间配置：调整四个打卡点与随机延迟。
- 日志页：排查连接、导航、识别和打卡问题。
- 设置页：维护模拟器、包名、通知和恢复策略。

推荐用法：

- 每次改完配置，先执行一次手动连接检查。
- 第一次投入正式使用前，先完整跑一轮真实链路。
- 如果日志里频繁出现导航恢复，优先确认目标 App 是否改版。
- 如果近期结果和真实状态不一致，先查看最近结果详情和日志页。

## 发布打包

标准发布：

```bash
python tools/build/build.py
```

指定版本：

```bash
python tools/build/build.py 2.3.0
```

调试版：

```bash
pyinstaller tools/build/build_debug.spec
```

打包说明：

- `tools/build/build.py` 是主打包入口，会自动更新版本号、执行发布前检查，并打包开源版 exe。
- `tools/build/build.spec` 对应入口 `src/main.py`。
- `tools/build/build_debug.spec` 用于调试和问题排查。
- 标准发布完成后，`dist/releases/v版本号/` 下会生成 `version.json`，可直接作为客户端更新清单使用。

## 软件更新

- 设置页支持填写更新清单地址，并检查是否有新版本。
- 更新清单建议放在 GitHub 可直接访问的位置，例如 GitHub Raw 或 Release 附件地址。
- 客户端发现新版本后，会下载新 exe，退出当前程序，并通过外部脚本完成替换和重启。
- 更新清单格式使用 `version.json`，其中应包含版本号、下载地址和 `sha256`。
- 新版清单资产键为 `opensource`，也可使用通用兜底键 `default`。
- 推荐清单地址：`https://raw.githubusercontent.com/juice4927/tongkatong-auto-checkin/main/version.json`。

发布到 GitHub Release 时可以给打包脚本提供仓库信息：

```powershell
$env:GITHUB_OWNER = "你的 GitHub 用户名"
$env:GITHUB_REPO = "仓库名"
$env:GITHUB_RELEASE_TAG = "v2.3.0"
$env:APP_UPDATE_NOTES = "这里填写本次更新说明"
python tools/build/build.py 2.3.0 --publish-release
```

## 依赖说明

运行时依赖的核心组件如下：

```text
PyQt6
uiautomator2
APScheduler
chinesecalendar
pydantic
loguru
requests
Pillow
rapidocr_onnxruntime
```

补充说明：

- `rapidocr_onnxruntime` 会连带安装 OCR 与模板匹配所需的 `onnxruntime / opencv / numpy / shapely`。
- `PyInstaller` 已从运行依赖中拆出，避免把纯打包工具混入日常运行环境。

## 目录结构

```text
project/
├── src/
│   ├── main.py                  # 程序入口
│   ├── version.py               # 版本信息
│   ├── gui/                     # 主窗口和各页面组件
│   ├── core/                    # 自动化、调度、节假日、随机时间、配置
│   └── utils/                   # 日志、通知、更新、ADB 工具
├── hooks/
│   └── hook-PIL.Image.py        # PyInstaller 自定义 hook
├── config/
│   └── default.json             # 默认配置
├── tools/
│   ├── build/
│   │   ├── build.py             # 主打包脚本
│   │   ├── build.ps1            # Windows 快捷打包入口
│   │   ├── build.spec           # 开源版打包配置
│   │   └── build_debug.spec     # 调试版打包配置
│   └── delta/                   # 增量更新工具
├── requirements.txt             # 运行时依赖
├── requirements-build.txt       # 打包依赖
├── LICENSE                      # 开源许可证
└── README.md
```

## 常见排障

- 无法进入考勤界面：先确认目标 App 内 `工作台 -> 考勤` 手动可达，再检查日志里是否触发“回主界面重试”或“重开 App 重试”。
- 连接失败：检查 MuMu 端口、ADB 状态、设备地址和包名设置。
- 定位失败：检查设置页的 MuMu 安装目录和 GPS 坐标，并查看日志中实际使用的 `MuMuManager.exe` 路径。
- 结果误判：查看最近结果、失败类型、恢复动作和详细运行日志。
- 跨日重排异常：查看 `00:01` 附近的重排摘要日志，判断是清理、补签还是跳过。

## 许可证

本项目使用 MIT License。详见 `LICENSE`。
