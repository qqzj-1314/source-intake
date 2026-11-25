# 源码导入 / Source Intake

在 Codex 内整理从内网复制的源码、需求材料与修改说明，先建立可追溯的本地基线，再交给 AI 改需求。

插件入口：`plugin://source-intake@source-intake-local`。面板嵌在 Codex 中，选择文件时使用系统对话框。

## 安装

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
```

创建空白需求目录会保存当前需求说明并创建空的 attachments；不会同时导入清单中的文件。正常导入则保存源码与附件。导入结果中提供可复制给 AI 的完整目录提示词。

BOS 目前使用人工需求字段，查询及状态写回尚未接入。面板不会自动执行、编译或发布导入的源码。

## 开发与验证

```powershell
cd plugin
npm ci --ignore-scripts
npm test
cd ..
python -X utf8 -m unittest discover -s tests -v
```

`plugin/` 是 MCP Apps 面板与 stdio 服务，`skill/source-intake/` 是 Skill 与 Python 导入内核，`tests/` 是内核测试。Skill 可单独复制到 Codex 的 skills 目录。

测试默认使用临时目录和合成输入。可选的现场只读集成测试在没有对应本地样例时跳过；不会将现场项目放入此仓库。

提交完成后运行 `python scripts/build-release.py` 生成 `dist/source-intake-<版本>.zip` 和 SHA256 校验文件。脚本只打包 Git 已提交内容，并附带提交号及逐文件校验清单。

发布包由 Git 中的产品源文件生成，不包含本机配置、凭据、个人偏好、真实需求附件、导入批次或 node_modules。使用锁文件安装依赖；离线机器需事先准备 npm 缓存。
