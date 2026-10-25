# Fanqie Radar 内核

来源：https://github.com/WindXRan/fanqie-radar

本机接入来源：`fanqie-radar/src/fanqie_index`，版本 0.3.0。
原作者：方寸写作（FangCun Studio）。许可：MIT，完整文本见同目录 LICENSE。

保留 schema.py、scoring.py、analysis.py、scraper.py 原始实现。
__init__.py 是书包的轻量入口；界面、后台更新调度和搜索衔接在书包中实现，
不启动原项目的独立 HTTP 或 MCP 服务。没有把启发式选书评分当作经过验证的预测。

内置榜单来自原项目 data/ 的 2026-10-13 快照；实际日期由界面显示。
用户更新的数据保存在个人运行目录，不写回此内核或内置快照。
