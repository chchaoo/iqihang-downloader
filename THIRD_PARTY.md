# 第三方组件

| 组件 | 上游与源码 | 许可说明 |
| --- | --- | --- |
| FFmpeg / FFprobe | [FFmpeg 源码 a8029b6528](https://github.com/FFmpeg/FFmpeg/tree/a8029b6528) · [LGPL shared Windows 构建及构建配置](https://github.com/FFmpeg-Builds/FFmpeg-Builds/releases/tag/autobuild-2026-11-26-12-56) | LGPL，共享库随免安装包附带。 |
| aria2 1.37.0 | [源码及 Windows 二进制](https://github.com/aria2/aria2/releases/tag/release-1.37.0) | GPL-2.0-or-later，包含上游 OpenSSL 许可说明。 |
| yt-dlp 2026.10.26 | [源码与构建工作流](https://github.com/yt-dlp/yt-dlp/tree/2026.10.26) · [正式发布](https://github.com/yt-dlp/yt-dlp/releases/tag/2026.10.26) | 上游 Windows 二进制含第三方组件，组合许可为 GPLv3+。 |
| Python 3.12.10 / Tcl/Tk | [CPython 源码及 Windows 构建配置](https://github.com/python/cpython/tree/v3.12.10) | 随包附带 Python 的完整许可。 |
| PyInstaller 6.22.0 | [源码、引导器和许可例外](https://github.com/pyinstaller/pyinstaller/tree/v6.22.0) | 免安装版使用其引导器。 |

FFmpeg、FFprobe、aria2 和 yt-dlp 为独立可执行组件，本程序通过子进程调用。免安装包中的 `licenses/` 包含组件原始许可证、构建来源和校验值。

`assets/github-*.png` 来自 [GitHub Brand Toolkit](https://brand.github.com/foundations/logo) 的 Invertocat 标识，仅用作打开 GitHub 项目页面的按钮图标。该商标归 GitHub, Inc. 所有，适用 [GitHub Logo Policy](https://docs.github.com/en/site-policy/other-site-policies/github-logo-policy)。
