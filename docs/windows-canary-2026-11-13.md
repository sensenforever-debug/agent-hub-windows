# Agent Hub 0.3.0 Windows 验证记录（2026-11-13）

## 构建与安装

- 在当前已登录的 Windows 用户会话运行 `packaging/build-agent-hub.ps1`，Inno Setup 编译成功；安装器及桌面窗口均显示 0.3.0。
- `dist/AgentHub-Setup.exe` SHA-256：`86AE08B3A8EB74F85B59714AD931CC8A3F741F326F892AFF067E55557B733996`。
- 构建脚本校验 WorkBuddy 固定归档 SHA-256 `D0B288F413A1524225E3E793EB6E4E9BF38204C0BCC5D332D4220056AA0B20AA`，小浣熊固定归档 SHA-256 `B8C1960D24060EE2C611362CC205142DFA0C2AA8277687D0D1740B6ACE2C9637`。
- 退出旧 Agent Hub 进程后覆盖安装；原节点令牌和本机 Key 沿用。新进程显示 protocol 3 已连接，动态上报 WorkBuddy 2 个、小浣熊 9 个来源模型。两个 Agent 页面均显示“账号已更换，重新验收”操作。

## 源码与真实推理验收

- `pytest -q desktop`：26 passed。回归测试覆盖错误模型 JSON 的 502、SSE 中途换模的协议错误与停止完成事件、三协议来源模型回显、两个来源 epoch 的独立轮换。
- 本机统一 API `glm-5.3@workbuddy`：Chat、Responses、Messages 各自的文本、SSE、工具往返和红色图片，共 12/12 通过，探针预算 80 token。
- 本机统一 API `sn-glm-5-3-flash@raccoon`：同样 12/12 通过，探针预算 512 token。
- 小浣熊 Responses 的 `previous_response_id` 两轮调用均返回 200，第二轮有正文且回显原来源模型 ID。
- Windows 凭据管理器中两个来源的随机 epoch 与本机身份指纹分别存在；没有在此记录具体值。网关通信不包含账号标识或本机指纹。

## 网关灰度边界

本机实测不能替代网关保存的来源验收记录。当前小浣熊公开映射仍未开启；需网关管理员确认节点只有一条 protocol 3 连接，为目标模型建立关闭状态的显式映射，并在网关记录该来源 12/12 通过后，使用受限测试团队 Key 完成公网三协议、SSE、工具、读图、续写及关闭映射后的 WorkBuddy 回归。网关待办见 [agent-gateway issue #1](https://github.com/sensenforever-debug/agent-gateway/issues/1)。

本记录仅含脱敏状态和结果，不含令牌、账号、图片或提示词原文。
