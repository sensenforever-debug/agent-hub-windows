# Agent Hub Windows 统一代理

Agent Hub 将 WorkBuddy 与商汤小浣熊放进同一个 Windows 桌面应用。本机统一接口为 `http://127.0.0.1:8788/v1`，模型使用 `原模型ID@workbuddy` 或 `原模型ID@raccoon`；API Key 保存在当前用户的 Windows 凭据管理器中。桌面应用提供总览、两个 Agent、模型、本机 API、网关、日志和设置页面。关闭窗口时驻留托盘，选择“退出并断开节点”才停止。

WorkBuddy 桥沿用 [workbuddy-to-dsh](https://github.com/Ianzhyh/workbuddy-to-dsh) 的固定版本 `2f3c556d38896164bf138627b484662aa6f4d345`。小浣熊推理引擎采用 [Alphaxiaoteng/raccoon2api](https://github.com/Alphaxiaoteng/raccoon2api) 的固定版本 `5f1b8e6d86b1178ef2fba7596c5869846beed0aa`，源码和 MIT 许可均独立封装。管理层从已登录桌面客户端的 `%USERPROFILE%\.office-raccoon\agent\config\auth.json` 自动读取会话令牌；其旧路径仍可兼容。令牌不写入日志或发送给团队网关。

小浣熊模型来自账号实时模型目录；只在目录成功读取时公布。上游对不存在的模型会静默回退，因此本机代理在发送前校验目录，并对返回的模型 ID 再校验。小浣熊原始引擎只监听 `127.0.0.1:8791`，且需要进程内随机内部令牌；统一 API 则需要单独的本机 Key。

## 统一网关协议

本机接口中的 WorkBuddy 和小浣熊均提供 Chat Completions、Responses 与 Anthropic Messages，包含 SSE、函数调用和图片输入。小浣熊的 Responses 与 Messages 由独立适配层转换到固定源码桥的 Chat Completions；`previous_response_id` 在当前进程内保留最多 128 条、一小时，重启后失效。Anthropic SDK 可用 `x-api-key`，OpenAI SDK 可用 Bearer。0.3.0 内置节点连接器已改用 protocol 3，通过本机统一 API 动态上报两个来源的模型。服务器已有显式来源映射与独立验收能力；小浣熊仍需 Windows 已登录会话的真实 12 项验收和受限灰度，详见 [protocol 3 接入说明](docs/gateway-protocol3.md)。

## 安装与构建

从源码构建得到 `dist\AgentHub-Setup.exe`，在同一 Windows 用户会话中安装。首次启动停止旧 WorkBuddy 节点程序，迁移登录后启动项，沿用原节点配置、桥令牌和节点令牌；无需重新配对。商汤小浣熊官方客户端须单独安装并登录。

0.3.0 安装器尚需在 Windows 开发机实际构建和验收。构建需 Python 3.11、Node.js LTS、Inno Setup 6。运行 `packaging\build-agent-hub.ps1`；脚本校验固定源码归档 SHA-256。旧版 WorkBuddy 安装器说明保存在 `docs/windows-desktop.md`。

