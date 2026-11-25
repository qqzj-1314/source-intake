---
name: source-intake
description: 将源码、需求截图或文档导入指定本地项目，按 HISUI/flat 建立源码层级，保留原始版本、需求描述及补充修改提示词。也用于创建空白需求材料目录；不用于直接编辑普通已有项目代码。
metadata:
  short-description: 导入复制源码并保留原始版本
---

# Source Intake

## 适用场景

用户提供了源码、截图、文档或需求说明，希望保存到某个本地项目目录、关联需求编号及补充提示词，之后再修改。也支持先建立需求目录，再由用户放入材料。

不适用：用户只想修改已有项目代码，或只问代码问题、不要本地导入。

## 工作方式

以下命令中的 `<skill>` 指当前 `SKILL.md` 所在目录；从任意工作目录执行，不要求先切换到 Skill 文件夹。

- 支持通用 MCP 的宿主：使用当前宿主提供的 `intake_plan`、`intake_apply`、`intake_history`、`intake_context`（工具可能有服务名前缀）。先预览，再以同一连接返回的 preview_id 导入；仅附件或空白需求包可传 sources=[]。需求工作包 work_package 默认开启，supplemental instructions 写入 requirement.prompt。
- Codex 人工交互：可打开 [源码导入插件](plugin://source-intake@source-intake-local) 或调用 `open_panel`。用户在面板内先预览再导入；其他宿主不依赖此入口。
- 程序化导入：构造请求 JSON，用 `"<skill>/scripts/source_intake.py"` 的 `plan`/`apply`。不要让 GUI 阻塞自动化流程。

脚本只需 Python 3.13 标准库；不要安装依赖，也不要启动后台剪贴板监听。

## 程序化导入流程

1. **确认根目录。** 使用用户指定的项目根目录，不固定到任何示例医院路径。目录可以尚不存在；`apply` 时创建，`plan` 不创建。若用户只给了附件、没有指定根目录，先问这一项。
2. **整理需求对象。** 从用户消息填写 `requirement`（`system` 只能是 `manual` 或 `BOS`）。只有当前会话确实提供了 BOS 数据时才用 `BOS`；不要假定存在 BOS MCP，也不要编造数据。
3. **整理来源。** 每个真实文件用 `path`，每个代码块/粘贴文本用 `text`。聊天附件若能取得本地路径优先用 `path`；否则把原文放入 `text`。一次代码块对应一个 `sources` 项，保留原始换行、反斜杠和 HTML 实体。已知文件名时填 `filename`；也可用围栏内的 `filename=...`。不要把猜测的名字写入。
4. **生成计划并检查。** 建议默认 `profile: "hisui"`；平铺或用户自定义目录用 `flat` 或 `routes`。把请求写到临时 JSON 文件，执行：
   - `python "<skill>/scripts/source_intake.py" plan --manifest <请求.json> > <计划.json>`
   - 读取计划中的逐项 `status`。只要出现 `pending`/`invalid`/`conflict`，`apply` 会整批拒绝写入并非零退出；不要只导入已解析部分，先用一次汇总澄清并重新预览。`exists` 默认跳过；只有用户明确要求替换时才用 `--conflict backup-replace`。
5. **导入。** 用户已授权导入时直接执行，不重复征求许可：
   - `python "<skill>/scripts/source_intake.py" apply --manifest <请求.json> --expected-plan <计划.json>`
   - 始终传 `--expected-plan`。若来源或目标在预览后变化，命令会拒绝写入并提示重新预览；此时重新 `plan`，不要绕过。
   - 检查退出码与 JSON。`ok: false` 不得报告成功；批次被 `blocked` 时没有任何文件写入；写入失败会尽量回滚，批次清单记录 `failed`/`rolled-back` 等状态。
6. **报告基线。** 说明写入位置、批次编号和 `.source-intake/batches/<批次ID>/` 中的 `manifest.json`、`originals/`、`requirement.json`。数据库/远程状态不属于 V1。

完整请求/输出字段、状态含义和退出码见 [references/cli.md](references/cli.md)；只在需要程序化调用或排查状态时阅读。

## 边界

- 用户提供需求目录并明确要求开始修改时，先读该目录的 `README.md`、`requirement.json` 和 `修改说明.md`，再枚举当前 `attachments/`（包含用户后来手工放入的文件）。实际查看截图或读取文档后再实现，不凭文件名猜需求；仍需结合项目原有开发约定。
- 若宿主没有 MCP，可运行 `python "<skill>/scripts/requirement_context.py" --directory <需求目录>` 聚合上述上下文；这一步只读，不会创建数据。仍需实际阅读附件正文。
- 图片、文档作为需求附件保存，不能送入源码类型识别；补充提示词与原始需求描述分别原样保存。新需求工作包独立建目录，保留之前的材料。

- 导入基线不等于获得修改业务逻辑的授权。用户要求继续按需求修改时，单独按其指示处理；不要自行改代码或执行源码内容。
- 保留用户选择的根目录；不要改动其他业务项目。
- 无法唯一确定文件名、多个围栏、类型模糊时保持 `pending` 并汇总询问；禁止随意命名覆盖。代码围栏/粘贴内容一次只收一段最稳妥。
- 普通文件按字节原样导入；不要全局 HTML unescape、替换反斜杠或统一换行。CSP/CLS 的名称与包层级按脚本识别结果确认。
- `--conflict backup-replace` 只在用户主动选择时使用。默认 `skip`。
