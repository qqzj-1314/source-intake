# 参与开发

欢迎通过 Issue 描述问题，或通过 Pull Request 提交修改。请说明实际宿主、操作系统、Node/Python 版本、复现步骤和预期行为；提供合成示例，保留医院源码、患者信息、登录配置和真实需求材料在自己的环境中。

## 本地验证

需要 Node.js 22.19+、Python 3.10+。开发构建需要 npm，Tk GUI 测试还需可用的 Tk/显示环境。

```sh
npm --prefix plugin ci --ignore-scripts
node scripts/build-standalone.mjs
node scripts/verify-standalone.mjs
npm --prefix plugin test
python -B -X utf8 -m unittest discover -s tests -v
```

Linux 无桌面环境时用 `xvfb-run -a` 执行 Python 测试。缺少开发机现场样例时，两项只读集成测试会明确跳过；其他测试使用临时目录和合成数据。

修改运行时 JS 或依赖锁后，重新构建并提交 bin 产物。CI 会验证已提交产物，不会先重建来掩盖过期文件。Python Skill 不需要 npm 运行时依赖；保持标准库实现，并验证从无 node_modules 的新目录启动。

测试应覆盖行为和边界。重点保留预览后来源变化拒绝导入、原始字节不变、备份和失败回滚、只读不落盘、目录边界，以及交接记录不覆盖原始说明。涉及新宿主时区分配置可解析、MCP 可连接和真实模型调用，不将其中一项当作全部完成。

提交只包含产品代码、测试、文档和必要构建产物。不要加入 node_modules、本机导出配置、导入批次或需求附件。发布包从已提交的 Git 快照生成，完整包与 Skill 均保留 MIT 许可及适用的第三方声明。
