# Marginalia Android

这是 Marginalia 的独立 Android 客户端。它是一个轻量 WebView 外壳：

- 首次打开输入电脑端显示的局域网地址，或打开二维码扫描器；
- 地址保存在手机本地，下次直接进入阅读界面；
- 论文内容、解析、翻译、笔记和任务仍由电脑端处理，手机端不复制数据库；
- 电脑端重新构建后，WebView 每次重新连接都会加载电脑端的新版本。

构建：在 `mobile/android` 目录执行 `gradlew.bat assembleDebug`。生成的 APK 位于 `app/build/outputs/apk/debug/app-debug.apk`。也可以直接用 Android Studio 打开 `mobile/android`。
