# 源码导入 / Source Intake

整理从内网复制的源码、需求材料与修改说明，先建立可追溯的本地基线，再交给 AI 改需求。支持标准 MCP 和可搬迁 Skill；Codex 保留内嵌面板，Claude Code、Qoder、Trae、WorkBuddy 等工具可使用通用入口。

插件入口：`plugin://source-intake@source-intake-local`。面板嵌在 Codex 中，选择文件时使用系统对话框。

本项目以 [MIT License](LICENSE) 开源。独立 Skill 包同样附带许可证；预编译运行时的第三方许可见 [THIRD_PARTY_NOTICES](bin/THIRD_PARTY_NOTICES.md)。参与开发见 [贡献说明](CONTRIBUTING.md)。

## 安装

通用 MCP / Claude Code / Qoder / Trae / WorkBuddy：下载完整发行包并解压，准备 Node.js 22.19+ 和 Python 3.10+，无需先安装 npm 依赖：

```sh
node scripts/configure.mjs --client all
node plugin/doctor-launch.mjs --config plugin/.local/clients/generic.json
```

将对应配置添加到宿主 MCP 设置。Claude Code 也可使用 `claude --plugin-dir <本项目目录>` 加载原生本地插件。具体命令、宿主验证情况和边界见 [兼容性与安装](docs/compatibility.md)。通用入口不要求 Codex 或 Tk；不会在启动时自动联网装依赖。

只使用 Skill 的工具可下载 `source-intake-skill-<版本>.skill` 或同内容 ZIP，使用宿主的导入入口，或解压后将 `source-intake` 目录放入其 Skills 目录。Skill 的 Python CLI 仅用标准库，不需要 Node.js；各宿主的实际导入验证情况见兼容性记录。

以下为 **Codex 内嵌面板** 安装方式：

Windows、Node.js 22.19+、npm、Python 3.10+（含 Tk），以及支持本地插件的 Codex Desktop。

下载源码包并解压到长期保留的目录，在 PowerShell 中运行：

```powershell
./install.ps1
# 多个 Python 环境时可指定：
./install.ps1 -Python 'C:\path\to\python.exe'
# 只安装依赖并生成本地市场文件，不注册到 Codex：
./install.ps1 -PrepareOnly
```

安装后重启 Codex，打开“源码导入”。安装器根据当前机器重新生成绝对路径，不使用作者机器的路径。插件运行依赖解压目录，请勿移动或删除；移动后重新安装即可。

## 使用

选择或输入项目目录，填写需求编号、描述和补充说明，收集源码或需求附件，检查路径后先预览再导入。不存在的目标目录会在导入时创建。

源码可按 HISUI 目录规则或平铺布局导入；真实文件保留原始字节。默认跳过同名目标，主动选择备份后替换时先留备份。`.source-intake/batches/` 保存每批原始版本、需求快照和状态。

需求材料和 AI 阅读入口的具体布局见面板导入结果。可以只建立需求目录，再自行放入截图或文档。后续向 AI 提供需求目录，并要求先阅读其中的 README 和修改说明，再执行需求。

需求附件可通过文件选择、复制的文件列表或“收集剪贴板截图”添加。截图支持 Windows 常见 PNG、24/32 位 DIB 格式；其他格式可先保存成图片再选择。每个需求工作包使用独立目录，重复导入保留以前的材料：

```text
项目/
  requirements/需求编号-唯一标识/
    README.md         AI 阅读入口、源码链接和附件清单
    requirement.json  结构化需求快照
    修改说明.md       补充提示词原文
    attachments/      截图、文档及后来手动添加的材料
    handoffs/         后续按需追加的工作进度、验证与下一步
```

创建空白需求目录会保存当前需求说明并创建空的 attachments；不会同时导入清单中的文件。正常导入则保存源码与附件。导入结果中提供可复制给 AI 的完整目录提示词。

BOS 目前使用人工需求字段，查询及状态写回尚未接入。面板不会自动执行、编译或发布导入的源码。

换智能体继续工作时，`intake_context` 会同时读取已有交接记录，提示需求材料与记录时相比是否变化。`intake_append_handoff` 可保存已改文件、验证结果、未决问题和下一步；纯 Skill 也有 Python CLI。记录独立追加，保留原始需求和修改说明。材料相同不能证明业务源码或测试仍有效，交接状态也不代表用户已验收。用法见 [工作交接](skill/source-intake/references/handoff.md)。

## 开发与验证

```powershell
cd plugin
npm ci --ignore-scripts
npm test
cd ..
python -X utf8 -m unittest discover -s tests -v
```

`plugin/` 是 MCP Apps 面板与 stdio 服务，`skill/source-intake/` 是 Skill 与 Python 导入内核，`tests/` 是内核测试。Skill 可单独复制到 Codex 的 skills 目录。

`plugin/universal-server.mjs` 是所有标准 MCP 客户端共用的无界面入口；`scripts/configure.mjs` 导出配置，`scripts/doctor.mjs` 实际连接服务器检查内核。其他智能体可用其 Skill 导入入口加载 `skill/source-intake/`，或直接按 SKILL.md 执行 CLI。

每次跨工具接续需求时使用 `intake_context`，或执行 `python skill/source-intake/scripts/requirement_context.py --directory <需求目录>`，重新汇总补充提示词和后来添加的附件，再实际阅读图片或文档。
只知道项目目录时，可用 `intake_list_requirements` 或 `python skill/source-intake/scripts/requirement_index.py --root <项目目录> --query <编号或标题>` 查找。Codex 面板提供同样的“已有需求”入口；同编号多个工作包会分别列出，材料归档不代表业务需求已经完成。

产品改进见 [CHANGELOG](CHANGELOG.md)，持续迭代计划见 [产品计划](docs/PRODUCT-ROADMAP.md)。

测试默认使用临时目录和合成输入。可选的现场只读集成测试在没有对应本地样例时跳过；不会将现场项目放入此仓库。

源码开发时先 `node scripts/build-standalone.mjs`，再运行测试并提交。提交完成后运行 `python scripts/build-release.py` 生成完整项目包、独立 Skill 包及 SHA256 校验文件。脚本仅打包 Git 已提交内容，Skill 也从该提交的原始字节生成；重复打包同一提交的 ZIP 元数据固定。

发布包不包含本机配置、凭据、个人偏好、真实需求附件、导入批次或 node_modules。`bin/` 包含预编译的通用服务与健康检查器，以及依赖声明和第三方许可文本；源文件与产物哈希不匹配时不会在无依赖环境中执行陈旧产物。Node.js 和 Python 本身仍需提前具备。

`bin/*.mjs` 是生成物，请修改源码并重新构建。开发环境装有依赖时可回退源码，健康检查中的 `runtime_mode` 区分 `standalone` 与 `source`。Codex 内嵌面板继续使用原安装流程；通用离线入口和 Skill 是另外两种使用方式。
