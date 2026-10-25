# 书包安卓 0.3.0 测试版

现在可以从书架或任务完成入口，一键把 TXT / EPUB 交给另行安装的 Legado 阅读。首次选择保存位置，之后打开同一本、内容未改变的书会恢复 Legado 的阅读进度，不再重复导入。

- 支持 Legado 的正文排版、EPUB 插图和音量键翻页：音量减下一页，音量加上一页。
- 设置中可选择 Legado 或内置阅读，保存选择并显示连接状态。没有安装 Legado 时提供官方下载入口，也可明确切换为内置阅读。
- 修复重复送书造成新增副本、阅读位置回到开头的问题；重启书包和 Legado 后仍可继续阅读。
- 网页端可实际导入 TXT / EPUB，同名文件分别保留；修复内置阅读在章节尚未加载完成时误报书签保存成功的问题。

**需要另外安装 [Legado 官方阅读器](https://github.com/LegadoTeam/legado/releases/latest)**，本 APK 不含 Legado。阅读进度、书签和排版各自保存，书包与 Legado 暂不互通。内置阅读保留为文字阅读选项，没有硬件音量键翻页。

附件：`bookpack-android-0.3.0-debug.apk`，43,633,571 字节，约 41.6 MiB。包名和调试签名沿用上一版，可以覆盖升级；最低 Android 7.0 / API 24，包含 arm64-v8a 与 x86_64。

SHA-256：`1f4c17604fc64952fe00c3f7369bda768137a23b8be41b2ccb03efd0c06677ca`

验证：6 项 Python 检查、19 项网页交互检查、14 项原生 Legado 检查通过。Android 15 x86_64 模拟器配合官方 Legado 3.26101819，实际检查 TXT 正文、EPUB 插图、音量键上下页、两端重启后的进度和副本数量。APK v2 签名与 16 KB 对齐通过；尚未验证实体 ARM 手机。详见 [0.3.0 验证记录](https://github.com/3538246218/bookpack/blob/main/android/0.3.0%E9%AA%8C%E8%AF%81%E8%AE%B0%E5%BD%95.md)。
