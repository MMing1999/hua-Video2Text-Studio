from __future__ import annotations

from typing import Any


def explain_error(raw: str | Exception | None) -> dict[str, Any]:
    """Map raw exceptions into a user-facing title/detail/fix_steps payload."""
    message = str(raw or "").strip() or "未知错误"
    lower = message.lower()

    rules: list[tuple[tuple[str, ...], dict[str, Any]]] = [
        (
            (
                "source must be",
                "unsupported url host",
                "unsupported local file",
                "source cannot be empty",
            ),
            {
                "title": "链接或文件无法识别",
                "detail": message,
                "fix_steps": [
                    "粘贴 B 站 / A 站 / YouTube / X / Instagram / Facebook / 抖音 / 小红书链接，或 BV 号",
                    "本地文件请使用 mp4 / mkv / mov / mp3 / wav 等常见格式",
                    "确认路径存在且有读取权限",
                ],
            },
        ),
        (
            ("412", "http error 412", "precondition failed"),
            {
                "title": "下载被拦截（412）",
                "detail": message,
                "fix_steps": [
                    "关闭系统代理后再试，或设置 B2T_USE_PROXY=0（B 站常见）",
                    "在「设置 → 下载 / Cookie」选择从浏览器读取登录态",
                    "或把 cookies.txt 放到工作区 .b2t/ 后重试",
                ],
            },
        ),
        (
            ("login required", "sign in", "cookies", "confirm your age", "age-gated", "private video"),
            {
                "title": "需要登录态或 Cookie",
                "detail": message,
                "fix_steps": [
                    "X / Instagram / Facebook / 部分 YouTube 内容通常需要 Cookie",
                    "在「设置 → 下载 / Cookie」选择 chrome / safari 等浏览器",
                    "或导出 Netscape 格式 cookies.txt 放到工作区 .b2t/",
                ],
            },
        ),
        (
            ("ffmpeg",),
            {
                "title": "缺少 ffmpeg",
                "detail": message,
                "fix_steps": [
                    "macOS 执行：brew install ffmpeg",
                    "安装后重新打开 Studio / 重启服务",
                    "在终端执行 which ffmpeg 确认可用",
                ],
            },
        ),
        (
            ("whisper", "openai-whisper", "no module named 'whisper'"),
            {
                "title": "Whisper 未安装或不兼容",
                "detail": message,
                "fix_steps": [
                    "在项目目录执行：uv sync --extra whisper --extra web",
                    "确认 Python 版本为 3.10–3.12",
                    "Intel Mac 可能无法安装当前 Whisper/PyTorch 组合",
                ],
            },
        ),
        (
            ("yt-dlp", "youtube-dl"),
            {
                "title": "下载组件异常",
                "detail": message,
                "fix_steps": [
                    "执行 uv sync 确保 yt-dlp 已安装",
                    "检查网络；海外站若需代理可设 B2T_USE_PROXY=1",
                    "抖音 / 小红书较不稳定，可更新 yt-dlp 后重试",
                ],
            },
        ),
        (
            ("api key", "unauthorized", "401", "invalid_api_key", "ai 未配置", "openai"),
            {
                "title": "AI 接口未配置或鉴权失败",
                "detail": message,
                "fix_steps": [
                    "在 Studio「AI 设置」中填写 Base URL、API Key、模型名",
                    "确认接口兼容 OpenAI Chat Completions",
                    "若使用代理，检查网络与 Key 额度",
                ],
            },
        ),
        (
            ("empty transcript", "transcriber returned"),
            {
                "title": "转写结果为空",
                "detail": message,
                "fix_steps": [
                    "确认视频有人声，不是纯 BGM",
                    "尝试更大 Whisper 模型（small / medium）",
                    "检查音频是否成功提取到 .b2t/audio/",
                ],
            },
        ),
    ]

    for needles, payload in rules:
        if any(needle in lower for needle in needles):
            return payload

    return {
        "title": "处理失败",
        "detail": message,
        "fix_steps": [
            "查看下方原始错误信息",
            "确认链接可访问、依赖已安装（uv run bili2text doctor）",
            "仍失败时把错误原文反馈给开发者",
        ],
    }
