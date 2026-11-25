# 0.5.0 验收记录

本轮减少通用入口安装步骤：发行包携带通用 MCP/doctor 的 npm 依赖，Skill 可独立搬迁。Node.js 22.19+、Python 3.10+ 仍由用户准备；Codex 内嵌面板保留原 npm 安装流程。

## 自动化结果

实际通过的 [CI run 45624259798](https://github.com/qqzj-1314/source-intake/actions/runs/45624259798)，提交 `91dd02f4f098a72bbb74c768ec51793844281514`。

| 环境 | Python | Node/MCP | 结果 |
| --- | --- | --- | --- |
| GitHub Windows / Python 3.11 / Node 22 | 96 项，2 项现场样例跳过 | 24 项通过 | 成功 |
| GitHub macOS / Python 3.11 / Node 22 | 96 项，2 项现场样例跳过 | 24 项通过 | 成功 |
| GitHub Linux / Python 3.11 / Node 22 / Xvfb | 96 项，2 项现场样例跳过 | 24 项通过 | 成功 |
| GitHub Linux / Python 3.10 / Node 22 / Xvfb | 96 项，2 项现场样例跳过 | 24 项通过 | 成功 |
| 本机 Windows / Python 3.13 | 96 项通过、0 跳过 | 24 项通过 | 成功 |

CI 先验证已提交的编译产物与源码/依赖锁哈希一致，不在验证之前重建产物。独立运行测试再复制发行文件到新目录，排除所有 node_modules，实际运行配置导出、doctor、stdio MCP 预览→导入→搜索→读取上下文；确认补充说明的 CRLF 原文一致。修改源码或破坏 bundle 后，在无依赖环境启动会明确拒绝，损坏 bundle 不会被执行。

Skill 打包测试覆盖目录布局、原始字节与校验文件、排除缓存/链接/外部材料、错误元数据、输出目录位于源码内部、重复构建和避免覆盖完整项目 ZIP。

## 发行包和原生宿主

- 从实际构建的完整 ZIP 解压到临时目录，核对 RELEASE.json 全部文件哈希；目录没有 node_modules，仍能导出全部客户端配置、运行 doctor，返回 `runtime_mode=standalone`，Python 内核来自解压目录。
- Skill 包的每个文件与完整包同一提交中的 Skill 文件逐字节一致。单独解压 Skill，仅调用 Python CLI 完成空需求包预览→导入→索引→上下文读取，保留原始补充说明和空附件目录；不需要 Node.js。
- Qoder CLI 实际模型会话连接上述无 npm 依赖的解压目录，并调用 intake_status，工具返回 `version=0.5.0`、`runtime_mode=standalone`。完整导入流程另由三系统 MCP 测试覆盖。
- Claude Code 2.1.327 使用该解压目录的 `--plugin-dir` 加载原生插件；init 事件确认插件版本 0.5.0，插件 MCP connected。隔离 settings 的会话没有登录，未证明模型工具调用成功。
- 本机 Codex 插件注册更新到 0.5.0；已安装 Skill 的 8 个文件与产品源文件哈希一致。当前应用可能需要重启才能刷新插件。

## 真实 CI 发现的问题

两轮 macOS 测试失败保留在 [run 45623691864](https://github.com/qqzj-1314/source-intake/actions/runs/45623691864) 和 [run 45624012503](https://github.com/qqzj-1314/source-intake/actions/runs/45624012503)。新增测试先后对入口和内核路径使用字符串比较，macOS 的 `/var` 与 `/private/var` 指向相同目录却被误判。现统一将测试临时工作区解析为真实路径，并对实际入口比较真实路径；没有放宽产物哈希或无依赖要求。

## 边界

通用 MCP 不在启动时联网安装依赖，也不自带 Node/Python。Skill 打包器只面向本项目的一行 name/description frontmatter，不是通用 YAML 工具。Trae、WorkBuddy 的宿主界面及 Skill 导入仍未实测；跨系统 CI 与 MCP 协议成功不能替代这些宿主证据。第三方依赖许可随 bin/THIRD_PARTY_NOTICES.md 分发，本项目没有额外声明开源许可。
