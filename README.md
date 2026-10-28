# docs-feed

把技术日报、技术文章和 AI 产品创意汇总为一个 Atom 1.0 全文订阅源。

**订阅地址：** https://raw.githubusercontent.com/kejun/docs-feed/main/releases.atom

将此地址直接添加到 RSS / Atom 阅读器即可。GitHub 仓库页面上的 `https://github.com/kejun/docs-feed/releases.atom` 是 GitHub 自动生成的 Release 订阅，不能用仓库文件替换；本项目使用上面的 Raw 地址。

## 内容来源

| 来源 | 范围 |
| --- | --- |
| [daily-rss](https://github.com/kejun/daily-rss) | 技术日报 Markdown 文档 |
| [blogpost](https://github.com/kejun/blogpost) | 技术文章 Markdown 文档 |
| [daily-investor/ideas](https://github.com/kejun/daily-investor/tree/main/ideas) | 仅 `ideas/` 内的 Markdown 文档 |

每篇文档是一条订阅项，包含来源分类、标题、摘要、HTML 全文和 GitHub 原文链接。排除 README、AGENTS、SKILL、工作流、测试夹具和已识别的仓库维护文档，不收录 `observations/`。筛选规则见 `feed.json`。

## 更新规则

- GitHub Actions 每天北京时间（Asia/Shanghai）早上 8:30 检查三个来源，也可在 [Actions](https://github.com/kejun/docs-feed/actions/workflows/update-feed.yml) 点击 **Run workflow** 手动更新。定时任务可能因 GitHub 排队而延迟。
- 按文档最后修改时间倒序保留最近 100 篇，数量可在 `feed.json` 修改。
- 发布时间和修改时间来自完整 Git 历史；定时检查本身不会改变订阅时间。文档编辑保留相同条目 ID，避免生成重复项；文件改名或移动会产生新 ID。
- HTML 正文保留表格、列表、代码和图片；相对链接转换为绝对地址，移除脚本标签和危险属性。Mermaid 等依赖脚本的图表保留代码文本。
- 内容没有变化时不提交。任一来源无法读取或没有符合条件的文档时，更新失败并保留已有订阅文件，避免发布缺失来源的结果。
- 发布前同步主分支和三个来源，再重新生成订阅文件；遇到并发提交时最多尝试 3 次，避免旧任务重跑时产生合并冲突或覆盖较新的内容。发布脚本仅用于 CI 的临时工作区，会重置本仓库和来源仓库的本地修改。
- 无需服务器或额外 API Key。只向本仓库写入订阅文件，三个内容仓库仅供读取。
- 如果仓库连续 60 天没有活动，GitHub 可能停用定时工作流；可在 Actions 重新启用。

## 本地生成

需要 Git 和 Python 3.11+，源仓库必须包含完整历史。

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
git clone https://github.com/kejun/daily-rss.git .sources/daily-rss
git clone https://github.com/kejun/blogpost.git .sources/blogpost
git clone https://github.com/kejun/daily-investor.git .sources/daily-investor
python -m unittest discover -s tests -v
python scripts/generate_feed.py
```

重复生成前，在各源仓库执行 `git pull --ff-only` 获取更新。自定义配置和目录可通过 `--config`、`--sources-dir`、`--output` 指定。
