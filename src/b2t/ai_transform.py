from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Literal

TransformMode = Literal["proofread", "format"]

# Keep chunks comfortably under common output limits while preserving context.
DEFAULT_CHUNK_CHARS = 5000
DEFAULT_OVERLAP_CHARS = 120


@dataclass(slots=True)
class AISettings:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-flash"


PROOFREAD_SYSTEM = """你是中文文稿校对助手。请纠正明显错别字、重复口头禅与不通顺处，
尽量保留原意与说话风格，不要擅自扩写或删减关键信息。只输出校对后的正文，不要解释。"""

FORMAT_SYSTEM = """你是中文口语转写排版助手。输入通常缺少标点、换行和段落。
请整理成可读文稿，要求：
1. 补全合适的中文标点（句号、逗号、问号、顿号等）
2. 按语义分段；章节切换处可加简短小标题（用 Markdown ##）
3. 保留原意与关键信息，不要写成摘要，不要删改事实
4. 去掉明显口头禅堆叠（如连续的“然后然后”“那个那个”），但不要文学化改写
只输出排版后的正文，不要前言或解释。"""


def resolve_ai_settings(settings: AISettings | None = None) -> AISettings:
    base = settings or AISettings()
    api_key = base.api_key.strip() or os.getenv("DEEPSEEK_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    base_url = base.base_url.strip() or "https://api.deepseek.com"
    model = base.model.strip() or "deepseek-flash"
    # OpenAI SDK expects optional /v1; DeepSeek accepts both.
    if base_url.rstrip("/").endswith("api.deepseek.com"):
        base_url = "https://api.deepseek.com"
    return AISettings(api_key=api_key, base_url=base_url, model=model)


def transform_text(
    text: str,
    *,
    mode: TransformMode,
    settings: AISettings,
    instruction: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> str:
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("text is empty")

    resolved = resolve_ai_settings(settings)
    if not resolved.api_key:
        raise RuntimeError("AI 未配置：请先填写 API Key，或设置环境变量 DEEPSEEK_API_KEY")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "缺少 openai 包。请执行：uv sync --extra mimo  （或 pip/uv 安装 openai）"
        ) from exc

    client = OpenAI(api_key=resolved.api_key, base_url=resolved.base_url or None)
    system = PROOFREAD_SYSTEM if mode == "proofread" else FORMAT_SYSTEM

    chunks = _split_text(cleaned, chunk_chars=chunk_chars)
    outputs: list[str] = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        prefix = ""
        if total > 1:
            prefix = f"这是第 {index}/{total} 段，请只处理本段，保持与上下文语气一致。\n\n"
        if instruction and instruction.strip():
            user_content = f"{prefix}补充要求：{instruction.strip()}\n\n原文：\n{chunk}"
        else:
            user_content = f"{prefix}原文：\n{chunk}" if prefix else chunk

        outputs.append(
            _call_model(
                client=client,
                model=resolved.model,
                system=system,
                user_content=user_content,
            )
        )

    return "\n\n".join(part.strip() for part in outputs if part.strip()).strip() + "\n"


def _call_model(*, client, model: str, system: str, user_content: str) -> str:
    kwargs = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": 0.2,
    }
    # DeepSeek Flash 默认可能开 thinking；排版任务关闭以加快速度、降低费用。
    if "deepseek" in (model or "").lower():
        kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

    try:
        response = client.chat.completions.create(**kwargs)
    except Exception:
        # 兼容不支持 thinking 字段的网关
        kwargs.pop("extra_body", None)
        response = client.chat.completions.create(**kwargs)

    content = (response.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("AI 返回空结果")
    return content


def _split_text(text: str, *, chunk_chars: int) -> list[str]:
    if len(text) <= chunk_chars:
        return [text]

    # Prefer splitting near punctuation / whitespace to avoid cutting mid-sentence.
    parts: list[str] = []
    start = 0
    length = len(text)
    while start < length:
        end = min(start + chunk_chars, length)
        if end < length:
            window = text[start:end]
            cut = max(
                window.rfind("。"),
                window.rfind("！"),
                window.rfind("？"),
                window.rfind("；"),
                window.rfind("\n"),
                window.rfind("，"),
                window.rfind(","),
                window.rfind(" "),
            )
            if cut >= int(chunk_chars * 0.4):
                end = start + cut + 1
        parts.append(text[start:end].strip())
        start = end
    return [part for part in parts if part]


def looks_unpunctuated(text: str) -> bool:
    sample = text[:2000]
    punct = len(re.findall(r"[。！？；，、\.\!\?\;\,]", sample))
    return punct < max(3, len(sample) // 80)
