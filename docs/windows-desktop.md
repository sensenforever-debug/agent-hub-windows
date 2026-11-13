# WorkBuddy 团队网关桌面节点

下载 [Windows 安装包](https://api.435038537.xyz/downloads/WorkBuddyGatewayNode-Setup.exe)，在已登录 WorkBuddy 的当前 Windows 用户中安装。安装包带有 Python、Node.js 与固定版本的上游桥源码，不需要另外安装运行时。旧节点的本地配置及 Windows 凭据管理器令牌会沿用；首次运行会停止旧配置应用并替换当前用户的登录启动项。

窗口显示 WorkBuddy 登录文件、桥状态、网关状态、模型和验收进度。首次接入时输入管理员提供的一次性配对码；已配对用户无需再次输入。关闭窗口会缩到托盘；在托盘菜单选“退出”才会断开节点。桥的高级账号及积分设置仍在“打开桥控制台”中。

点击“检查上游升级”可查看上游最新提交并选择升级。程序将源码放在独立版本目录，做语法检查，再切换、启动并验证本地模型目录；失败会切回旧版本。“回退桥版本”可切回上一个版本。版本变化会向网关报告，管理员应在管理台检查该模型的重新验收结果。

卸载程序不会删除 `%APPDATA%\AgentGatewayNode` 的配置、上游源码和 Windows 凭据管理器令牌；需要彻底移除时，由当前 Windows 用户自行清理这些数据。

本工具基于 [Ianzhyh/workbuddy-to-dsh](https://github.com/Ianzhyh/workbuddy-to-dsh) 的 MIT 开源代码。它不是 WorkBuddy 官方服务。仅在你有权使用的账号和上游许可范围内运行。
