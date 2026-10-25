# Fanqie Radar 内核

来源：https://github.com/WindXRan/fanqie-radar

本机接入来源：`fanqie-radar/src/fanqie_index`，版本 0.3.0。
原作者：方寸写作（FangCun Studio）。许可：MIT，完整文本见同目录 LICENSE。

保留 scoring.py、analysis.py 原始实现。书包 0.2.0 对 scraper.py 的状态解析增加了字符串外 undefined 值兼容；schema.py 增加采集时间、旧分类标记及最新章节字段。原有署名和 MIT 许可保留。
__init__.py 是书包的轻量入口；界面、后台更新调度和搜索衔接在书包中实现，
不启动原项目的独立 HTTP 或 MCP 服务。没有把启发式选书评分当作经过验证的预测。

保留原项目 data/ 的 2026-10-13 快照，并加入本机从番茄公开榜单采集的 2026-10-25 四份快照；实际日期和采集时间由界面显示。
用户更新的数据保存在个人运行目录，不写回此内核或内置快照。
