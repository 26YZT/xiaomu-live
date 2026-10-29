# 原版源码的本机改动备份

`manifest.json` 记录两个官方仓库的固定提交、补丁摘要和干净基线应用检查结果。
补丁不包含 `config.json`、密钥、Cookie、用户素材、日志或录制文件。
各仓库的原始 LICENSE / NOTICE 同目录保留；修改版不使用原作者品牌对外发布。

实际运行目录为工作区 `upstream/` 下的两个独立 Git checkout。
工作区外层 Git 忽略它们，因此将来只备份外层 Git 时，应保留这里的补丁；运行数据要另行安全备份。

恢复步骤：

1. 在新的空目录克隆 manifest 中的对应仓库，检出所列 commit。
2. 在仓库中执行 `git apply --check /补丁的绝对路径`，确认后 `git apply /补丁的绝对路径`。
3. 主仓库创建 Python 3.12 虚拟环境，从 `requirements.macos.lock.txt` 安装依赖。
4. 恢复自己的本机配置、素材和私密目录，从工作区根目录运行 `tools/upstream_local.py`；不要上传这些私密文件。

本机改动后可在工作区根目录运行：

```bash
.venv/bin/python tools/export_upstream_patches.py
```

脚本只导出跟踪文件的差异和明确列出的适配源码，并在临时干净上游快照上运行 `git apply --check`。
