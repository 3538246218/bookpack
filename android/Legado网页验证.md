# Legado 接入：网页验证阶段

用户选择的接入方式是一键调用独立安装的 Legado，书包负责传递原始 TXT / EPUB。2026-10-25 先按用户要求完成网页测试，随后按新指令构建 0.3.0 APK。本文记录网页阶段；原生结果另见 [0.3.0 验证记录](0.3.0验证记录.md)。

## 已完成

- 书架和任务完成入口按设置选择 Legado 或内置阅读。
- 阅读方式开关保留选择，展示连接状态及 Legado 官方下载入口。网页明确说明原生跳转需要安卓应用；提供改用内置阅读的操作。
- 网页支持通过书架按钮和顶部加号实际上传 TXT / EPUB。同名文件分别保存，原始文件字节保持不变。
- 网页内置阅读支持章节、搜索、排版、书签及刷新后恢复进度。
- 修复章节加载期间书签误报成功的问题：正文完成加载后开放书签操作，收到保存成功的结果才显示成功提示。回归检查直接核对保存记录。
- 本地预览提供手机窄屏、手机宽屏及网页宽屏；所有预览数据保存在独立 `.ui-run/legado-web-preview` 目录。

## 实际验证

- 19 项现有及新增自动化网页检查通过，JavaScript 异常为 0。覆盖实际获取打包、榜单、景色、TXT / EPUB 阅读及 Legado 选择、提示、切换与接口分发。自动化里的原生接口使用测试桩，实际安卓文件授权及启动留待设备验证。
- 6 项 Python 检查通过，包含网页导入后保留 GB18030 原始字节、同名书分别保留、重新加载后阅读，以及 EPUB 原文件与正文解析。
- Edge 浏览器实际通过系统文件选择器上传 TXT 和 EPUB，并打开正文。TXT 切到第二章、添加书签、返回书架、刷新后恢复；两种手机预览宽度均无横向溢出。页面控制台没有警告或错误。
- 下载链接在网页测试框架外打开；网页的运行与存储说明使用当前测试环境信息。

本地报告在 `outputs/web-validation.json` 和 `outputs/browser-preview-validation.json`，实际浏览器截图在 `outputs/previews/网页测试-EPUB阅读.jpg`。

## 打开测试页

在项目根目录依次运行：

```powershell
python -X utf8 android/tools/prepare.py
python -X utf8 android/tools/start_web_preview.py
```

第二条命令输出测试地址并启动后台服务，当前连接信息保存在 `outputs/web-preview.json`。预览默认使用内置阅读，可以在「设置 → 阅读方式」查看 Legado 入口与网页提示。实际文件授权及应用跳转通过安卓 APK 验证，浏览器不能替代这部分验证。

Legado 接口依据：[官方 AndroidManifest](https://github.com/LegadoTeam/legado/blob/531f9fbf07bdf491cb0aafaf02c35058f3bd0692/app/src/main/AndroidManifest.xml)、[官方文件接收入口](https://github.com/LegadoTeam/legado/blob/531f9fbf07bdf491cb0aafaf02c35058f3bd0692/app/src/main/java/io/legado/app/ui/association/FileAssociationActivity.kt)。阅读进度与书签由各自阅读器保存，两边暂不互通。
