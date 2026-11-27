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

粘贴代码加入清单后可点“预览代码”查看只读原文。点击“粘贴代码 / 图片”，在输入区直接 Ctrl+V / ⌘V 粘贴截图，会自动加入需求附件并显示缩略图；可连续粘贴不同图片，已有代码草稿保持不变。图片项提供“预览图片”，支持适应窗口、原始尺寸与滚动查看；关闭预览不影响已有导入计划。普通文件继续展示路径和文件名。

图片预览支持 PNG、JPEG、GIF、WebP、BMP，单张不超过 20 MiB；每次打开重新读取已收集的文件，损坏或不支持的图片会显示错误。输入区只使用本次粘贴事件的图片，不读取系统剪贴板历史。若宿主没有提供图片粘贴事件，仍可选择附件或使用原有“收集剪贴板截图”。

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

### BOS 只读查询（可选）

部署方获得相应内网服务授权后，仅在 **MCP 服务器进程环境**配置 `IMEDWAY_GATEWAY_KEY` 和 `IMEDWAY_BOS_TOKEN`。密钥不会传入面板、工具参数或日志；未配置或上游不可用不影响手工填写、预览、导入和交接等本地功能，启动时也不会自动连接 BOS。

Codex 面板可按数字序号精确查询，或按接受人/接受组、创建日期和标题做高级筛选；非序号查询至少需要一个组/人条件。日期默认显示 Asia/Hong_Kong 当日至次日，查询历史须明确放宽范围。查询和选择不会覆盖当前需求；只有点击“带入需求”才写入编号、标题、描述及操作时间，保留补充提示词与源码/附件清单，并使旧预览失效。来源标记为 BOS，修改编号或主动切换后回到手工来源；未返回的 URL 保持空白。

通用 MCP 提供同名能力的 `intake_bos_status` / `intake_bos_query`，面板使用 App-only 的 `panel_bos_status` / `panel_bos_query`。`configured` 只表示两个变量齐全，不等于已经连接。查询会把用户输入的筛选条件发送到部署方授权的远端服务；认证头仅用于该远端 HTTPS 连接，不发送项目源码或附件。服务只读，不写回 BOS 状态。显式带入后的需求字段会按用户确认保存在本地项目快照中。面板不会自动执行、编译或发布导入的源码。

换智能体继续工作时，`intake_context` 会同时读取已有交接记录，提示需求材料与记录时相比是否变化。`intake_append_handoff` 可保存已改文件、验证结果、未决问题和下一步；纯 Skill 也有 Python CLI。记录独立追加，保留原始需求和修改说明。材料相同不能证明业务源码或测试仍有效，交接状态也不代表用户已验收。用法见 [工作交接](skill/source-intake/references/handoff.md)。

## 开发与验证

```powershell
cd plugin
npm ci --ignore-scripts
npm test
cd ..
python -B -X utf8 -m unittest discover -s tests -v
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
