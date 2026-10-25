# 安卓融合版开源说明

书包安卓融合版从 0.4.0 起，将 Legado 阅读器的源码编译进同一个 APK。安装包只注册一个书包桌面入口，找书和阅读共享包名 `com.bookpack.mobile`。Legado 保留其原作者、贡献者署名和第三方许可，不宣称为书包原创阅读内核。

融合发行的应用代码按 GNU GPL 第 3 版提供，完整许可见 [COPYING](COPYING)。安装包内的「设置 → 阅读方式 → Legado · GPLv3 开源许可」可离线查看许可，旁边提供对应源码入口。软件不提供担保。各第三方组件仍保留其原有许可与版权说明。此说明针对安卓融合发行，不改动原有桌面版独立发行的说明。

Legado 来源：[LegadoTeam/legado](https://github.com/LegadoTeam/legado)，原版权声明为 Legado / gedoor 与贡献者。采用官方发布版 `3.26101819`，提交 `a7d37f1707a70a459ad708f4fe79341d1e42d716`。原始来源和归档校验值记录于 [UPSTREAM.json](vendor/legado/UPSTREAM.json)，必要源码保存在 `vendor/legado/`。

2026-10-25 的融合修改：

- 用 `legado/` 的 Android library 构建配置编译原有应用源码、资源、书籍模块和 Rhino 模块；保留原有阅读页面、排版及硬件按键处理。
- 合并 Manifest，保留书包的桌面入口、包名和签名；阅读器使用同一应用身份。
- 新增 `EmbeddedReader.kt`，把书包书架中的文件存为私有阅读副本，再调用 Legado 原有导入和续读功能；未改动其小说解析和翻页实现。
- 对原有更新分发入口作标记修改，避免在融合版里下载安装独立 Legado 包。原始 Cronet 加载构建脚本只调整输入文件路径，仍保留其固定版本校验和转换过程。

完整对应源码和构建脚本通过 [公开仓库](https://github.com/3538246218/bookpack) 提供，各版本 Release 的源码归档包含所采用的 Legado 文件。构建依赖版本在 Gradle catalog 和构建文件中固定。

独立安装的 Legado 和融合版使用不同的应用目录，旧阅读记录不会自动迁移。请使用原应用的备份、导入功能迁移需要保留的阅读数据。书包的既有任务和书籍继续保存在原目录中。
