# Agent Hub 0.3.0 接入统一网关

Agent Hub 0.3.0 的内置节点连接器使用 protocol 3。桌面应用继续在当前 Windows 用户会话运行，本机统一 API 为 `http://127.0.0.1:8788`；团队客户端仍访问 `https://api.435038537.xyz`。团队 API Key 与 Agent Hub 本机 Key 分开保存。

## 节点工作方式

- `NodeController` 保留原节点令牌，启动 WorkBuddy 桥后连接本机统一 API。若 WorkBuddy 桥单独不可用，连接器仍可上报可用的小浣熊来源。
- WorkBuddy 后端版本从实际监听桥端口的进程路径识别，并经实时模型目录确认；桥不可用时，hello 不声明 WorkBuddy 来源。连接器每 5 秒检查一次，桥恢复、退出或版本改变时重连并发送新的 hello，让网关重新计算验收指纹。控制台仍在而桥子进程退出时，通过控制台写接口恢复桥。未知进程占用端口不会被认作已知 WorkBuddy 版本。
- 两个来源分别在当前用户的 Windows 凭据管理器中维护随机 `credential_epoch`。WorkBuddy 从本机桥 `/v1/identity`、小浣熊从当前登录文件的 `office_identity` 安全识别账号变化；仅在对应来源变化时轮换 epoch。身份值和本机指纹不会发送到网关。若身份不可读取，用户在对应 Agent 页面点击“账号已更换，重新验收”；epoch 更新后连接器重连发送新 hello。
- 本机统一 API 对成功 JSON 与每个包含 `model` 的 SSE 事件核对上游实际模型，静默换模返回 502 或中断流并发送协议错误事件；只有验证通过的模型 ID 才改写为来源 ID。
- 连接器从 `:8788/v1/models` 每 60 秒读取动态目录，发送 `原生模型ID@workbuddy`、`原生模型ID@raccoon` 的 protocol 3 offer。目录读取失败时撤销 offer，5 秒后重试。它不使用小浣熊静态模型快照。
- 本机调用使用 Windows 凭据管理器中已有的 `unified-local-key`。该 Key、WorkBuddy 桥令牌、登录资料和小浣熊令牌不会写进 WebSocket 的 hello、models 或请求报文。
- 服务器按公开模型到来源模型的显式映射派发请求。连接器直接把选中的来源模型发送到 `:8788`。协议探针仍覆盖 Chat、Responses、Messages 的文本、SSE、工具往返和读图 12 项；服务器对小浣熊来源指定 512 token 预算，旧 WorkBuddy 探针保持 80。
- 同一节点令牌只保留一个团队网关连接。服务器在旧版和新版同时重连时优先保留 protocol 3；升级安装时仍建议先退出旧版节点程序。
- 服务器的 Responses 历史会保留节点与来源模型固定信息，不依赖 Agent Hub 本机一小时的 `previous_response_id` 缓存。

## 构建与升级

在 Windows 开发机安装 Python 3.11、Node.js LTS、Inno Setup 6，运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\packaging\build-agent-hub.ps1
```

生成 `dist\AgentHub-Setup.exe`。安装到当前 Windows 用户后，确认 Agent Hub 标题与安装器版本为 0.3.0，并在“团队网关”页查看“已连接 · protocol 3”、WorkBuddy 和小浣熊上报数量。原节点令牌沿用，无需重新配对；本机 API Key 自动沿用 `unified-local-key`。

如账号变化未被本机自动识别，在对应 Agent 页面点击“账号已更换，重新验收”。该操作只轮换对应来源的随机 epoch，并让网关重新计算该来源的验收指纹。不要将账号名称、令牌或可逆身份信息作为 epoch。

## 服务器灰度

服务器的旧 WorkBuddy 映射和兼容模式保持启用。安装 0.3.0 后，先确认管理台节点显示 P3、两个来源的 offer 独立出现；随后只建立 `sn-glm-5-3-flash@raccoon` 的显式公开模型映射。映射初始关闭，12 项通过后才可启用。为测试人员创建明确列出该公开模型的团队 Key；现有通配符 Key 不会自动取得小浣熊权限。最后验证三协议的普通与流式调用、工具、红图、Responses 续写、断线和回退。小浣熊目录中的其他模型不得因为首个模型通过就直接开放。

服务器实现与状态见 [protocol 3 合同](https://github.com/sensenforever-debug/agent-gateway/blob/master/docs/agent-hub-protocol3.md) 和 [灰度记录](https://github.com/sensenforever-debug/agent-gateway/blob/master/docs/agent-hub-rollout-2026-11-13.md)。本仓库的源代码测试可运行：

本机统一 API 成功响应的 `model` 字段必须是请求中的来源 ID（例如 `sn-glm-5-3-flash@raccoon`），包括 Chat、Responses、Messages 及对应 SSE 事件。网关随后将该字段映射为团队使用的公开 ID。来源回显和上游静默换模由本仓库测试；公开映射与调度由服务器仓库测试。跨项目缺陷按根目录 `AGENTS.md` 中的归属规则处理。

```powershell
py -3.11 -m pytest -q desktop
```
