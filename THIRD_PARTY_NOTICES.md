# 第三方许可说明

本项目不在仓库中分发语音模型、FFmpeg 二进制文件或 `whisper.cpp` 编译结果。用户明确授权后，本地环境准备脚本可能从下列项目下载并使用它们：

| 组件 | 用途 | 上游项目 | 许可证 |
| --- | --- | --- | --- |
| `whisper.cpp` | 本地语音识别 | https://github.com/ggml-org/whisper.cpp | MIT |
| Whisper GGML 模型 | 本地语音识别模型 | https://huggingface.co/ggerganov/whisper.cpp | MIT，以上游说明为准 |
| `imageio-ffmpeg` | 获取 FFmpeg 可执行文件 | https://github.com/imageio/imageio-ffmpeg | BSD-2-Clause |
| FFmpeg | 提取视频画面和音轨 | https://ffmpeg.org | LGPL/GPL，以实际构建为准 |

第三方组件的许可条款、版本和分发方式可能变化，使用时应同时查看对应上游项目的当前说明。
