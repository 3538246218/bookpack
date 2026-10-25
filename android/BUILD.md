# 构建安卓融合版

对应源码包含书包代码、`vendor/legado/` 中的固定版本源码、`legado/` 中的接入代码，以及 Gradle 构建配置。无需独立安装 Legado 才能运行最终 APK。

需要 JDK 21、Python 3.12、Android SDK Platform 36 和 Build Tools 36。设置 `ANDROID_HOME` / `ANDROID_SDK_ROOT`，或在未提交的 `local.properties` 中填写 `sdk.dir`。

在仓库根目录执行：

```text
python -X utf8 android/tools/prepare.py
python -X utf8 android/tools/prepare_legado.py
```

然后在 `android/` 目录使用随附的 Gradle 8.14.4 wrapper：

```text
./gradlew :app:assembleDebug --max-workers=1 -PbookpackPython=/absolute/path/to/python3.12
```

Windows 使用 `gradlew.bat`。本项目本机的 `tools/build.py` 会自动将源码同步到 ASCII 路径 `E:/dev-tools/builds/bookpack-android`，并使用 `E:/dev-tools` 下已安装的工具构建，以避开部分 Windows 安卓工具的中文路径限制。

构建需要联网获取固定版本的 Maven 依赖和官方 Cronet 编译 JAR。OpenCC 的版本、下载地址与 SHA-256 记录在 `third-party/opencc.lock.json`；可通过 `BOOKPACK_DEPENDENCY_CACHE` 指定准备脚本的下载缓存目录。应用本身不在首次运行时下载 Legado APK。

调试构建使用开发机的调试签名。其他开发者的调试签名与已发布测试包不同，不能直接覆盖已发布的包；重新构建不需要公开或上传任何签名私钥。

许可与修改范围见 [NOTICE](NOTICE.md)。生成的 APK、构建目录和本机任务不提交到源码仓库。
