# hua-Video2Text-Studio

把视频变成可读文稿的本地工作台：预览确认、转写、编辑，以及 AI 排版。

## 关于本项目

本项目基于 [lanbinleo/bili2text](https://github.com/lanbinleo/bili2text)（MIT License）二次开发。

上游提供了 B 站视频下载、音频提取与语音转写等基础能力；本仓库在此之上增加了 Studio 前端、链接预览、文稿目录、DeepSeek 纠错排版，以及对使用流程的改造。

感谢原作者 [lanbinleo](https://github.com/lanbinleo) 的开源工作。

## 许可证

[MIT License](./LICENSE)  
Copyright (c) 2023 Lanbin07 and bili2text contributors  
Copyright (c) 2026 hua-Video2Text-Studio contributors

## 快速开始

需要 Python 3.10–3.12、[uv](https://github.com/astral-sh/uv)、ffmpeg。

```bash
git clone https://github.com/MMing1999/hua-Video2Text-Studio.git
cd hua-Video2Text-Studio
uv sync --extra whisper --extra web --extra mimo
uv run bili2text ui --host 127.0.0.1 --port 8765
```

浏览器打开 `http://127.0.0.1:8765`。

CLI 转写示例：

```bash
uv run bili2text tx "https://www.bilibili.com/video/BVxxxxxx"
```

## 当前能力概览

- 粘贴 B 站链接或本地音视频 → 预览 → 转写
- Whisper 等本地/云端识别引擎
- Studio：文稿编辑、历史库、文件目录、设置
- DeepSeek Flash：AI 纠错 / 排版（可选）

更细的二次开发规划见 [docs/FEATURE-PLANS.md](./docs/FEATURE-PLANS.md)。
