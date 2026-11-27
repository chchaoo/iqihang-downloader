# 爱启航课程下载器

面向 Windows 的第三方桌面工具。用户自行登录爱启航后，可以浏览账户下的课程目录，并将获得保存许可的点播、回放和资料下载到本地。支持按课程或章节批量选择、并发下载、失败重试和高分屏缩放。

本项目基于 [Tony15246/iqihang-cli](https://github.com/Tony15246/iqihang-cli) 的设计思路二次开发。桌面界面、下载队列、持久设置和打包流程为独立实现。上游仓库未声明开源许可，本仓库的许可仅适用于本项目自行编写的代码。本项目是第三方工具，与爱启航无官方关联。

## 下载与使用

| 版本 | 怎么启动 | 需要自行配置什么 |
| --- | --- | --- |
| **Windows x64 免安装版** | 从 Releases 下载 `*-portable-win-x64.zip`，完整解压后双击 `IQihangDownloader.exe` | 适用于 Windows 10 22H2 和 Windows 11 x64。随包提供 Python、FFmpeg、FFprobe、aria2 和 yt-dlp。无需修改 `PATH`。 |
| **Python 源码版** | 在 [Releases](https://github.com/chchaoo/iqihang-downloader/releases) 下载 `*-source.zip`，或使用 GitHub 的 Code → Download ZIP。解压后双击 `Start.cmd` | 安装 Python 3.10+（含 Tkinter）。下载视频需把 `ffmpeg.exe`、`ffprobe.exe` 所在目录加入 `PATH`。可选配置 `aria2c.exe` 加速 HTTP 分段，配置 `yt-dlp.exe` 加速兼容的 HLS/DASH。 |

[下载最新版本](https://github.com/chchaoo/iqihang-downloader/releases/latest)

1. 输入自己的爱启航账号和密码，点击「登录并加载目录」。
2. 选择保存目录，再选课程、章节或单个文件，点击「下载所选未完成」。也可用「下载全部未完成」。
3. 在「界面缩放」调节字号和控件尺寸。运行后生成的 `iqihang-settings.json` 在程序旁边，账号及密码使用 Windows DPAPI 加密。「恢复默认」会删除配置文件。

## 使用范围与权利说明

- **账号与课程访问。** 程序按用户提供的账号登录，读取服务端返回的课程目录，并请求对应的播放或资料地址。程序没有解锁未购课程、生成授权、伪造学习进度或移除 DRM 的功能。应在平台条款及具体课程许可允许的范围内保存内容。 [爱启航官网](https://www.iqihang.com/) · [爱启航公开隐私政策](https://mobile.iqihang.com/zt/privacy_app/index.html)
- **个人学习与技术措施。** 《中华人民共和国著作权法》第二十四条列明个人学习等情形，同时要求不得影响作品的正常使用，也不得不合理地损害权利人的合法权益。第四十九条规定了对技术措施的保护。是否允许保存某门课程，还要看适用的购课条款、作品状态、权利人许可及具体使用情形。 [国家版权局《中华人民共和国著作权法》](https://www.ncac.gov.cn/xxfb/flfg/flfg_532/202103/t20210309_50530.html)
- **课程内容与网络传播。** 本仓库及发布包提供程序，不包含课程视频、讲义、账号密码或登录配置。《信息网络传播权保护条例》第二条规定，通过信息网络向公众提供他人的作品等内容，一般须取得许可。请勿利用本项目转售、网盘共享或公开传播课程内容。 [国家行政法规库《信息网络传播权保护条例》](https://xzfg.moj.gov.cn/front/law/detail?LawID=1253)
- **软件许可。** 《计算机软件保护条例》第六条将软件著作权与开发软件所用的思想、处理过程和操作方法区分。本项目对独立编写的桌面代码采用 MIT 许可。FFmpeg、aria2、yt-dlp 等按各自许可随包提供，具体版本、源码地址和许可证见 [第三方组件](THIRD_PARTY.md) 及免安装包内的 `licenses/`。 [商务部《计算机软件保护条例》](https://tfs.mofcom.gov.cn/fgsjk/flfg/zscq/zzq/art/2024/art_d071637de09e4ca88b627f924a7280c3.html)

如你是权利人，或发现授权说明有误，可通过本仓库的 [Issues](https://github.com/chchaoo/iqihang-downloader/issues) 联系。请勿在 Issue 中附带账号、密码、登录令牌或带签名的媒体链接。
