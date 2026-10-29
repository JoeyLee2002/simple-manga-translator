# 简单漫画翻译器

Windows 漫画翻译工具。导入原稿，批量翻译，在气泡中嵌入中文，导出 PDF。

![主界面](docs/screenshot.png)

## 功能

- 导入图片、PDF、CBZ、ZIP 和文件夹。
- 本地英文、中英文 OCR，可选云端视觉识别。
- 接入 DeepSeek 和兼容 Chat Completions 的接口。
- 编辑原文与译文，拖动文字框，合并气泡，调整字号。
- 术语表、译文缓存、任务暂停与继续。
- 检查漏译、重叠和越界，输出 PDF、译文 CSV 和检查报告。

## Windows 直接使用

1. 打开 [Releases 下载页](https://github.com/JoeyLee2002/simple-manga-translator/releases/latest)，下载名称带 `windows-x64` 的 ZIP。
2. 右键选择“全部解压”，打开解压后的 `simple-manga-translator` 文件夹。
3. 双击 `简单漫画翻译器.exe`。保持 `_internal` 文件夹与程序在同一目录。
4. 打开“引擎设置”，填写自己的 API 密钥并保存。
5. 新建项目，导入原稿，点击“开始/继续翻译”。

便携版适用于 **Windows 10/11 64位**，包含 Python、CPU OCR 运行库和识别模型。OCR 使用 CPU，翻译使用在线接口。

## DeepSeek 配置

在 [DeepSeek 开放平台](https://platform.deepseek.com/) 创建自己的 API 密钥，然后打开程序的“引擎设置”。

| 字段 | 填写内容 |
| --- | --- |
| 翻译引擎 | `DeepSeek` |
| API地址 | `https://api.deepseek.com` |
| 模型名称 | 默认 `deepseek-flash`，可按账号支持的模型修改 |
| API密钥 | 自己创建的密钥 |
| 文字识别 | 英文原稿选“英文OCR”，中英混合选“中英OCR” |
| 原文 / 目标 | 英译中可设为 `en` / `zh-CN` |
| 并发 | 默认 `4`；限流或内存紧张时可改为 `1` 或 `2` |

点击“保存设置”。需要下次继续使用同一密钥时，勾选“记住密钥”。仓库和安装包不包含可共用的 API 密钥；接口费用与模型可用性以服务商账户为准。

![引擎设置](docs/settings.png)

## 完整操作流程

1. **新建项目**：填写新的项目目录，点击“创建项目”。建议使用专门的工作文件夹。
2. **导入原稿**：点击“导入原稿”，浏览选择文件，或粘贴完整路径。多个路径每行一个，也支持文件夹。
3. **设置术语**：在“术语/背景”填写固定人名、组织名和背景信息。
4. **开始翻译**：点击“开始/继续翻译”，等待页面状态更新。可随时停止，已完成的页面会保存。
5. **核对修改**：切换“原图”和“译文”对照。选中文字区，在右侧修改，点击“保存并更新预览”。
6. **导出 PDF**：点击“导出PDF”，确认保存路径，点击“检查并导出”。

输出包括 PDF、译文 CSV 和检查报告。漏译、文字重叠或越界会阻止导出；复杂背景和语义问题仍需逐页核对。

详细操作见 [使用说明](docs/使用说明.md)，安装、更新及排错见 [部署指南](docs/部署指南.md)。

## 源码运行

需要 Windows 10/11 64位、Python 3.10，以及系统中的微软雅黑或宋体。下列命令在 PowerShell 中逐行执行：

```powershell
git clone https://github.com/JoeyLee2002/simple-manga-translator.git
cd simple-manga-translator
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/setup_models.py
.\.venv\Scripts\python.exe run.py
```

如果系统未安装 `py` 启动器，可将 `py -3.10` 换成指向 Python 3.10 的 `python`。命令直接调用虚拟环境中的 Python。

模型从 RapidAI 的公开模型仓库下载，文件哈希在脚本中固定。确认 `assets/models` 中存在 `en_rec.onnx` 和 `en_dict.txt` 后即可运行。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 构建 Windows 版

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

输出目录为 `发行版/简单漫画翻译器`。

分发时将这个目录整体压缩。便携版应包含程序、`_internal`、使用文档及许可证文件。具体构建步骤见 [部署指南](docs/部署指南.md#从源码构建windows版)。

## 常见问题

| 情况 | 处理方式 |
| --- | --- |
| 提示填写 API 密钥 | 打开“引擎设置”，输入密钥并保存 |
| 401、403 或其他接口错误 | 核对密钥、接口地址、模型名称和服务商账户状态 |
| 模型下载失败 | 检查网络后重新运行 `python tools/setup_models.py` |
| 没有识别到文字 | 核对 OCR 语言；日文等原稿可使用支持图片输入的云端视觉接口 |
| 译文过长或压盖 | 修改译文、调整字号与文字框，或合并同一气泡的文字区 |
| 找不到中文字体 | 确认系统安装了微软雅黑或宋体 |
| 重复导入后页数没有增加 | 像素完全相同的图片会自动跳过 |
| 想重新翻译一句 | 选中对应文字区，点击“重译选区” |

## 快捷操作

| 操作 | 用法 |
| --- | --- |
| 保存 | `Ctrl+S` |
| 撤销 | `Ctrl+Z` |
| 上页 / 下页 | `PageUp` / `PageDown` |
| 缩放画面 | `Ctrl+滚轮` |
| 合并文字区 | `Ctrl` 多选后点击“合并所选” |
| 添加文字框 | 在画面中右键拖动 |
| 移动 / 调整大小 | 拖动文字框 / 拖动右下角 |

## 文件与接口

项目由 `project.json`、`images` 和 `pages` 组成，可整体备份。程序配置和译文缓存存放在 `%LOCALAPPDATA%/MangaTranslatorStudio`。

英文及中英 OCR 在本地运行，只将识别文字发送到翻译接口；云端视觉 OCR 会向所配置的接口发送整页图片。“在线翻译”使用非官方接口，可能受到限流。

## 许可

[AGPL-3.0](LICENSE)。第三方依赖和 OCR 模型见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
