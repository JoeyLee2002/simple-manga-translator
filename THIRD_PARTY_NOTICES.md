# 第三方组件

本程序使用以下开源组件；各组件保留自身许可证。

| 组件 | 许可证 | 来源 |
| --- | --- | --- |
| PyMuPDF / MuPDF | GNU AGPL 3.0 | https://github.com/pymupdf/PyMuPDF |
| RapidOCR | Apache-2.0 | https://github.com/RapidAI/RapidOCR |
| PaddleOCR 模型 | Apache-2.0 | https://github.com/PaddlePaddle/PaddleOCR |
| ONNX Runtime | MIT | https://github.com/microsoft/onnxruntime |
| OpenCV | Apache-2.0 及其第三方声明 | https://github.com/opencv/opencv |
| NumPy | BSD-3-Clause | https://github.com/numpy/numpy |
| Pillow | HPND | https://github.com/python-pillow/Pillow |
| ReportLab | BSD | https://hg.reportlab.com/hg-public/reportlab/ |
| Requests | Apache-2.0 | https://github.com/psf/requests |

英文识别模型与字典来自 RapidAI/RapidOCR 模型仓库：

- https://modelscope.cn/models/RapidAI/RapidOCR
- 模型：`onnx/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile.onnx`
- 字典：`paddle/PP-OCRv4/rec/en_PP-OCRv4_rec_mobile/en_dict.txt`

模型由安装脚本下载，不存放在 Git 历史中。Windows 便携版包含运行所需模型。

Windows 中文字体由运行时从系统读取，安装包不分发系统字体。
