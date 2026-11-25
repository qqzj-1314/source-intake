# 各编程智能体接入与验证

核对日期：2026-11-25。这里的“支持”分为配置格式、真实协议和宿主实际调用三层。
安装后保留整个项目目录；换机器或移动目录后重新导出配置。

## 通用安装

需要 Node.js 22.19+、npm、Python 3.10+。通用 MCP 和 CLI 不依赖 Tk、Codex 或桌面窗口。

```sh
node scripts/configure.mjs --client all
node plugin/doctor-launch.mjs --config plugin/.local/clients/generic.json
```

可用 `--python <解释器绝对路径>` 指定 Python，`--output <目录>` 改输出目录。生成器只写导出文件，保留原客户端配置；目标文件内容不同则拒绝覆盖，显式 `--force` 才备份后重写。
0.5.0 起完整包自带编译后的通用运行时和依赖许可文本。没有开发用 node_modules 也能导出配置、运行 doctor 和启动通用 MCP；Node/Python 仍是前提。修改源码或依赖锁后需要重新构建，不能把陈旧 bin 当作最新实现。升级旧版本后应重新导出使用 launch.mjs 的配置。

同一服务器提供 `intake_status`、`intake_plan`、`intake_apply`、`intake_history`、`intake_context`、`intake_list_requirements`，以及 `implement_requirement` 提示词。
智能体先预览，使用同一 MCP 连接返回的 preview_id 执行；令牌过期、使用过或来源变更后需要重新预览。
已经有用户导入授权时可直接完成流程，无需人为增加重复确认。替换原文件须有对应用户授权。

## 客户端入口

| 客户端 | 安装/载入方式 | 当前验证 |
| --- | --- | --- |
| Codex | `./install.ps1` 安装原生内嵌面板；通用工具可另用生成的 codex.toml 片段 | 原内嵌面板已验收；通用 stdio 黑盒测试 |
| Claude Code | 临时 `claude --mcp-config <claude-code.json> --strict-mcp-config`；或 `claude mcp add-json --scope user source_intake '<JSON中source_intake条目>'`；原生插件 `claude --plugin-dir <本项目目录>` | 本机 2.1.327：通用配置 connected；原生插件 0.3.0 加载并显示插件 MCP connected；manifest 校验通过。当前模型请求 404，隔离 settings 的原生会话无登录，未证明模型调用流程 |
| Qoder CLI | `qoder --mcp-config <qoder.json> --strict-mcp-config`；或 `qoder mcp add-json source_intake '<JSON中source_intake条目>'` | 实际 DeepSeek-Flash 会话调用健康检查，以及预览→导入→读取需求上下文成功；落盘原提示词核对一致 |
| Qoder IDE | 扩展/连接器 → 自定义 MCP → JSON，合并 qoder.json 中的服务器条目 | 配置依据官方文档；IDE 界面未实测 |
| Trae | 在 MCP 设置的手动添加入口合并 trae.json 中的 mcpServers 配置 | 通用协议测试；当前未安装宿主，未宣称界面或工具调用实测 |
| WorkBuddy | 连接器/自定义 MCP 中填写 workbuddy.json；本地 stdio 方式 | 官方 Connector 规范支持 command/args/env；宿主未实测 |
| 其他支持 stdio MCP 的智能体 | 导入 generic.json 中的服务器条目 | 协议级兼容；需在目标宿主运行 doctor 或实际调用确认 |

Windows 终端粘贴 JSON 时须遵守当前 Shell 引号规则；也可让目标 AI 阅读生成文件并仅添加 source_intake 条目。不要用整份导出覆盖已有 MCP 配置。
同一宿主不要重复注册相同入口。若 Codex 面板与通用 MCP 需要并存，可把生成配置中的服务名改为 `source_intake_universal`，工具前缀由宿主决定；Skill 使用工具的功能名，不依赖固定前缀。

没有 MCP 的工具可导入 `skill/source-intake/`，按 SKILL.md 调用 Python CLI。
Skill 安装目录和宿主自动发现规则不同；可使用其“导入 Skill”入口，不能假定所有软件都读取 `.codex/skills`。

Claude 原生插件通过同一已编译入口启动；私有本地插件校验有公开目录许可/图标提示，本项目没有声明开源许可，也没有向公共市场发布。

## UI 和平台边界

- Codex 的内嵌 MCP App 面板保留；通用服务器只暴露标准工具，不向其他宿主强行返回其无法展示的 Codex 面板。
- 通用入口不会读取剪贴板或弹选择器。截图/文档以文件路径作为 attachment 传入；复制文本通过 text 传入。
- 截图读取的原生便捷入口仍属于 Windows 面板。所有宿主都可读取已保存图片。
- 文件和需求目录是共享交接格式，preview_id 只在当前服务实例有效，不能跨智能体传递；跨工具接续时读取已生成目录或重新预览。
- 文档/图片实际内容由智能体自身的图片/文档工具读取，intake_context 不把文件名或清单当作已读正文。
- 0.4.0 的实际 CI 已在 Windows、Linux、macOS 上通过内核、Tk 测试与通用 MCP 流程（Python 3.11 / Node 22）。每个平台有 2 项仅开发机具备样例的集成测试明确跳过。该证据不等于所有操作系统上的每种 IDE 界面都已验证，详见 [0.4 验收记录](verification-0.4.md)。

## 官方依据

- [Claude Code MCP](https://code.claude.com/docs/en/mcp)：stdio 配置、配置作用域、--mcp-config。
- [Claude 插件 manifest](https://code.claude.com/docs/en/plugins/manifest-reference)：skills、自定义 mcpServers 文件、CLAUDE_PLUGIN_ROOT。
- [Qoder MCP reference](https://docs.qoder.com/cli/mcp-reference)：stdio、mcpServers 与 CLI/项目配置。
- [Trae MCP overview](https://docs.trae.ai/ide/model-context-protocol)：手动 MCP 接入入口；本次该页正文无法提取，具体界面仍待宿主验证。
- [WorkBuddy Connector](https://open.workbuddy.cn/en/docs/connector)：本地 stdio 与 mcpServers/command/args/env。
