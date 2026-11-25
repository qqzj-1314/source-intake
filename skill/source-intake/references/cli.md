# source_intake CLI 与数据格式

## 需求截图、文档与补充说明

新请求可增加 `work_package: {"enabled": true, "token": "本次唯一标识"}`；
`requirement.prompt` 保存用户补充修改提示词原文。附件来源使用
`{"path": "图片或文档路径", "role": "attachment"}`，直接按字节存入该需求包的 `attachments/`，不做源码识别。
启用工作包后，`sources: []` 也可导入，从而生成 README、结构化需求、修改说明及空附件目录。
未提供 token/name 时 plan 自动分配名称，apply 需传对应 `--expected-plan` 才复用该名称。
返回的 `work_package.dir` 是交给 AI 的需求目录。每次新预览应生成新的唯一标识，不能复用已存在的需求包名称覆盖旧材料。

脚本：`scripts/source_intake.py`（相对本参考文件上一级的 Skill 目录）。只用标准库；stdout 始终输出 JSON（诊断写 stderr）。

```text
python "<skill>/scripts/source_intake.py" plan --manifest <request.json>
python "<skill>/scripts/source_intake.py" apply --manifest <request.json> [--conflict skip|backup-replace] [--expected-plan <plan.json>]
python "<skill>/scripts/source_intake.py" history --root <项目目录>
```

`plan` 不创建项目目录。`apply` 会创建用户指定的根目录。读取 JSON 时支持 UTF-8（含 BOM）、带 BOM 的 UTF-16/UTF-32，以及 GB18030 回退，便于 Windows 重定向保存计划。

## 请求 JSON

```json
{
  "root": "D:/示例项目",
  "project_name": "示例项目",
  "profile": "hisui",
  "routes": {
    "csp": "frontend/csp",
    "js": "frontend/scripts/dhcnewpro/dhcemc",
    "css": "frontend/scripts/dhcnewpro/dhcemc/css",
    "cls": "backend"
  },
  "cls_layout": "package",
  "requirement": {
    "system": "manual",
    "id": "BOS-123",
    "title": "示例需求",
    "description": "需求描述",
    "url": "",
    "updated_at": ""
  },
  "sources": [
    {"path": "C:/某源文件.txt", "filename": "可选原文件名", "destination": "可选明确相对路径"},
    {"text": "源码文本", "filename": "可选文件名", "destination": "可选明确相对路径"}
  ]
}
```

必填：`root`、`sources`。`sources` 每项必须且只能有 `path` 或 `text` 之一。
`requirement.system` 只接受 `manual` 或 `BOS`；字段可空。
`profile` 默认 `hisui`；`flat` 使用根目录平铺。`routes` 可覆盖各类型相对目录（允许空字符串）。
`cls_layout` 为 `package`（默认，保留包层级目录）或 `fullname`（CLS 文件名带完整包名）。实际选择写入计划与批次清单。

### 默认路由

| 类型 | hisui | flat |
|---|---|---|
| csp | `frontend/csp` | 根目录 |
| js | `frontend/scripts/dhcnewpro/dhcemc` | 根目录 |
| css | `frontend/scripts/dhcnewpro/dhcemc/css` | 根目录 |
| cls | `backend` + Class 包层级 | 根目录 + Class 包层级（`cls_layout=package`） |

CLS 从实际 `Class DOC.EMDoc.BL.X ...` 声明得到包与类名；真实 `.cls` 文件优先保留原文件名，包层级取自声明。真实 `.csp/.js/.css/.cls` 文件优先保留原文件名。CSP 可由开头文件名注释识别；`.txt`/粘贴文本可按内容识别，但 JS/CSS 缺少名称或类型模糊时为 `pending`，不得自动编名。CSP 中唯一的 script/link 引用可辅助命名，多候选时保持 `pending`，且不会生成引用指向但未提供的空文件。

### 粘贴与字节

- 一段 `text` 只收集一段源码；可剥离包住全部文本的最外层代码围栏。围栏信息中的文件名（如 ` ```js filename=demo.js `）可作为文件名。存在多个围栏或围栏外还有正文时为 `pending`。
- 原始粘贴文本（含围栏）完整保存在批次 `originals/`；导入内容使用剥离围栏后的文本。
- 普通文件按字节写入，不统一换行、不丢 BOM、不转码（UTF-8 BOM、CRLF、GB18030 均保留）。
- 不做全局 HTML unescape，不替换反斜杠。

### 路径安全

`destination` 必须是根目录内的相对文件路径；拒绝绝对路径、盘符、UNC、`..` 越界、空段、Windows 保留名/非法字符、`.source-intake` 元数据段。解析符号链接/junction 后的落点必须仍在根目录内；目标为已存在目录时该项 `invalid`。同批多个来源指向同一目标（含大小写等价、链接等价）时全部标记 `conflict`，不写入。

## plan 输出

关键字段：

```json
{
  "ok": true,
  "command": "plan",
  "root": "...",
  "root_exists": false,
  "project_name": "...",
  "profile": "hisui",
  "routes": {},
  "cls_layout": "package",
  "requirement": {},
  "items": [
    {
      "index": 0,
      "source_kind": "path",
      "source_path": "...",
      "source_name": "...",
      "kind": "csp",
      "filename": "...",
      "destination": "frontend/csp/...",
      "status": "ready",
      "notes": [],
      "encoding": "utf-8",
      "bom": false,
      "line_endings": "crlf",
      "fence_language": null,
      "fence_filename": null,
      "fence_stripped": false,
      "original_sha256": "...",
      "imported_sha256": "...",
      "target_exists": false,
      "target_sha256": null
    }
  ],
  "summary": {"total": 1, "ready": 1, "pending": 0, "invalid": 0, "conflict": 0, "same": 0, "exists": 0},
  "warnings": []
}
```

计划状态：

| status | 含义 | apply 行为 |
|---|---|---|
| `ready` | 目标不存在，可写入 | 写入 |
| `same` | 目标存在且内容一致 | 跳过（`skipped-same`） |
| `exists` | 目标存在但内容不同 | 默认跳过（`skipped-existing`）；主动 `backup-replace` 时备份后替换 |
| `pending` | 缺名称/类型/包层级、多围栏等未决 | 整批拒绝（`blocked`），不写入任何文件 |
| `invalid` | 来源无效或目标路径/目录非法 | 整批拒绝（`blocked`），不写入任何文件 |
| `conflict` | 同批目标重复 | 整批拒绝（`blocked`），不写入任何文件 |

`plan` 可原样重定向为 expected-plan。即使有 `pending`，`plan` 本身成功时仍以退出码 0 返回，逐项状态为准。`apply` 遇到任何未决/无效/冲突项会整批拒绝，避免只导入部分来源。

## apply 输出与 expected-plan

```json
{
  "ok": true,
  "command": "apply",
  "status": "completed",
  "batch_id": "20261125-163000-abcdef",
  "batch_dir": "D:/示例项目/.source-intake/batches/...",
  "items": [{"status": "written", "final_status": "written", "plan_status": "ready"}],
  "summary": {"written": 1, "total": 1}
}
```

- 传入 `--expected-plan` 时，先比较根目录、条目数、`original_sha256`/`imported_sha256`、`target_sha256` 与目标相对路径。预览后来源或目标变化、或目标在写入前变化，则拒绝写入并提示重新预览。此路径属于命令级错误：退出码 2，不写文件。
- 计划中只要存在 `pending`/`invalid`/`conflict`，`apply` 返回 `status: "blocked"`、`ok: false`（退出码 1），整批不写文件、不创建项目目录或批次。修正后需重新 `plan`。
- 同名冲突默认 `skip`。`backup-replace` 先保存旧文件到批次 `backups/`，再以同目录临时文件 + `os.replace` 替换。仅在用户主动选择时使用。
- 写入失败会停止后续写入并尽量回滚本批已写文件；批次清单记录 `failed`、`rolled-back`、`rollback-failed`、`skipped-not-attempted` 等状态；结果 `ok: false`（退出码 1）。
- 来源清单为空时 `status: "no-write"`，不创建根目录/批次。
- 元数据目录 `.source-intake`、`.source-intake/batches` 解析链接后的落点必须在根目录内；如果它们是指向项目外的 junction/符号链接，命令拒绝写入（退出码 2），外部目录不会产生文件。
- 批次会先以 `running` 状态写出 `manifest.json`，再保存 `requirement.json` 和 `originals/`，最后才写源码目标。基线保存失败时（`stage` 为 `manifest-init`、`originals-dir`、`requirement-snapshot`、`originals`、`baseline-manifest-update`），源码目标不写入，清单尽力记录 `failed`/`skipped-not-attempted`。如果连清单也无法保存，结果会带 `"tracking_limited": true` 和 `"manifest_error"`。
- 最终清单保存失败时，源码已写入但结果返回 `"status": "tracking-failed"`、`ok: false` 和 `tracking_limited`，不得视为成功。
- `status` 为 `completed` 表示命令执行完成；仍需查看 `summary`/逐项状态（`skipped-same`、`skipped-existing` 等可能未写入）。

批次布局：

```text
<root>/.source-intake/
  batches/<批次ID>/
    manifest.json         项目、来源、需求、目标、hash、编码、状态、时间、备份、错误
    requirement.json      需求快照（含 snapshot_at）
    originals/            本次所有来源的原始快照
    backups/              替换前备份（如有）
```

## history 输出

```json
{"ok": true, "command": "history", "root": "...", "batches": [{"batch_id": "...", "created_at": "...", "status": "completed", "requirement": {}, "summary": {}, "items": [], "manifest_path": "..."}], "warnings": []}
```

项目尚无批次时返回空数组。

## 退出码

| 退出码 | 场景 |
|---|---|
| 0 | plan 成功；apply `completed`（可含内容一致/同名跳过）或空清单 `no-write`；history 成功 |
| 1 | apply 被 `blocked`（存在 pending/invalid/conflict）、基线写入失败、回滚失败或清单保存受限（`tracking-failed`）等 `ok: false` 情况 |
| 2 | 参数/JSON/请求校验错误、expected-plan 不匹配、元数据目录越界、命令级 IO 异常 |

错误时 stdout 仍是 JSON：

```json
{"ok": false, "command": "apply", "error": {"type": "PlanMismatchError", "message": "..."}}
```

## 使用建议

- 自动调用始终先 `plan`，保存计划，再带 `--expected-plan` 执行 `apply`。
- 未决项汇总后用最少问题向用户澄清；不要通过手动改计划或忽略状态强行写入。
- 保留用户选择的根目录；导入基线不是修改业务逻辑的授权。
