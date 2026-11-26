# 跨智能体工作交接

用于接续已经实施的需求。原始需求、修改说明保留在原文件；AI 的进度另存到需求包的 `handoffs/<时间-随机ID>.json`。每次调用新增文件，读取不会创建目录。不修改以前记录，也不自动清理历史。

## MCP

1. `intake_context` 读取准确需求目录，查看原始材料、handoff.records、handoff.warnings。
2. 核对当前源码及有关附件。旧记录是执行者自报，不能当作最新源码已通过测试的证明。
3. 需要交接时，调用 `intake_append_handoff`，传 directory 和 `handoff.material_snapshot.fingerprint` 作为 expected_material_fingerprint，并填写 author、status、summary。
4. 可另传 changed_files（项目内相对路径）、validation、next_steps、open_questions、source_revision。validation 记录实际执行的命令/结果及未验证范围，不会自动运行测试。source_revision 是可选的源码提交或版本说明，不会自动查询 Git。

status 仅接受 `in_progress`、`blocked`、`ready_for_review`。它们是工作进度，不是用户验收或外部系统状态；author 是调用者提供的名称，不是经过认证的身份。

材料指纹包含当前读取视图的需求、README、补充说明及附件元数据/哈希，排除交接记录本身与机器目录。指纹不一致时拒绝保存，重新读取后再记录。指纹只描述需求材料，不描述项目源码；读取不锁定其他编辑器，不能作为外部文件的原子快照。材料读取有警告或缺少附件哈希时 complete=false，此时仍可保存进度，比较结果保留 unknown。

每条旧记录的 material_match：

- `unchanged`：当前和旧视图完整，材料指纹一致。
- `changed`：已观察到指纹不同，应复核材料。
- `unknown`：指纹相同但至少一次读取不完整，不能证明全部材料未变。

同时间的多个记录按文件 ID 排序，不能据此推断不同执行者之间的因果顺序。调用不是幂等操作；超时或回包失败后先读取记录，避免盲目重复追加。

## 仅 Python 的 Skill

先只读聚合上下文：

```sh
python "<skill>/scripts/requirement_context.py" --directory "<需求目录>"
python "<skill>/scripts/requirement_handoff.py" read --directory "<需求目录>" --limit 20
```

将本次真实进度写入 UTF-8 JSON 文件，例如：

```json
{
  "author": "当前智能体名称",
  "status": "in_progress",
  "summary": "已完成页面字段修改，后端仍待现场验证。",
  "changed_files": ["frontend/page.js"],
  "validation": "填写实际运行的命令、结果和未验证范围",
  "next_steps": "下一步检查保存接口",
  "open_questions": "仍需明确的问题；没有则为空",
  "source_revision": "当前源码提交或版本说明；可空",
  "expected_material_fingerprint": "替换为上一步返回的64位材料指纹"
}
```

```sh
python "<skill>/scripts/requirement_handoff.py" append --directory "<需求目录>" --input "<本次记录.json>"
```

检查退出码和 ok，不得把失败报告成已记录。仅允许在已有普通需求包内追加；不跟随 handoffs/ 的链接，不写包外目录。采用同目录临时文件加硬链接发布，文件系统不支持硬链接时明确失败，不能改用覆盖旧文件。读取最多扫描 500 个条目、单条 64 KiB、总计 4 MiB，默认返回最近 20 条；损坏/超限/链接均保留警告，不能把被截断的历史当成完整记录。超过容量需用户自行归档，工具不会删除历史。
