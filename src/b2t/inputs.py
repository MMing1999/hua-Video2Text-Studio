from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from b2t.models import REMOTE_SOURCE_KINDS, SourceKind, SourceRef


BV_PATTERN = re.compile(r"(BV[0-9A-Za-z]{10})")
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".flv", ".avi", ".webm"}

# host suffix → SourceKind（按匹配优先级排列）
_PLATFORM_HOSTS: tuple[tuple[SourceKind, tuple[str, ...]], ...] = (
    ("bilibili", ("bilibili.com", "b23.tv")),
    ("acfun", ("acfun.cn", "aixifan.com")),
    ("youtube", ("youtube.com", "youtu.be", "youtube-nocookie.com")),
    ("twitter", ("twitter.com", "x.com", "t.co")),
    ("instagram", ("instagram.com", "instagr.am")),
    ("facebook", ("facebook.com", "fb.watch", "fb.com")),
    ("douyin", ("douyin.com", "iesdouyin.com")),
    ("xiaohongshu", ("xiaohongshu.com", "xhslink.com", "rednote.com")),
)

_SUPPORTED_HINT = (
    "B 站 / A 站 / YouTube / X / Instagram / Facebook / 抖音 / 小红书链接，或本地音视频文件"
)


def parse_source(raw_input: str) -> SourceRef:
    value = raw_input.strip()
    if not value:
        raise ValueError("source cannot be empty")

    candidate_path = Path(value).expanduser()
    if candidate_path.exists():
        suffix = candidate_path.suffix.lower()
        if suffix in AUDIO_SUFFIXES:
            return SourceRef(
                raw_input=value,
                kind="audio",
                display_name=candidate_path.stem,
                path=candidate_path.resolve(),
            )
        if suffix in VIDEO_SUFFIXES:
            return SourceRef(
                raw_input=value,
                kind="video",
                display_name=candidate_path.stem,
                path=candidate_path.resolve(),
            )
        raise ValueError(f"unsupported local file type: {candidate_path.suffix}")

    match = BV_PATTERN.search(value)
    if match and (not _looks_like_url(value) or "bilibili.com" in value.lower() or "b23.tv" in value.lower()):
        bv = match.group(1)
        url = value if _looks_like_url(value) else f"https://www.bilibili.com/video/{bv}"
        page = _extract_page_from_url(url)
        return SourceRef(
            raw_input=value,
            kind="bilibili",
            display_name=bv,
            url=url,
            bv=bv,
            page=page,
        )

    if _looks_like_url(value):
        kind = _match_platform(value)
        if kind is None:
            raise ValueError(f"unsupported url host; currently accept {_SUPPORTED_HINT}")
        parsed = urlparse(value)
        display = _display_name_from_url(value, kind)
        page = _extract_page_from_url(value) if kind == "bilibili" else None
        bv = None
        if kind == "bilibili":
            bv_match = BV_PATTERN.search(value)
            bv = bv_match.group(1) if bv_match else None
            if bv:
                display = bv
        return SourceRef(
            raw_input=value,
            kind=kind,
            display_name=display,
            url=value,
            bv=bv,
            page=page,
        )

    raise ValueError(f"source must be {_SUPPORTED_HINT}")


def parse_source_list(raw_input: str) -> list[str]:
    sources = [
        line.strip()
        for line in raw_input.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if not sources:
        raise ValueError("source list cannot be empty")
    return sources


def safe_stem(value: str) -> str:
    stem = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-._")
    return stem or "b2t-output"


def is_remote_kind(kind: str) -> bool:
    return kind in REMOTE_SOURCE_KINDS


def _looks_like_url(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme in {"http", "https"} and parsed.netloc)


def _normalize_host(host: str) -> str:
    host = host.lower().strip(".")
    if host.startswith("www."):
        host = host[4:]
    if host.startswith("m."):
        host = host[2:]
    if host.startswith("mobile."):
        host = host[7:]
    return host


def _match_platform(url: str) -> SourceKind | None:
    host = _normalize_host(urlparse(url).netloc)
    for kind, domains in _PLATFORM_HOSTS:
        for domain in domains:
            if host == domain or host.endswith("." + domain):
                return kind
    return None


def _display_name_from_url(url: str, kind: str) -> str:
    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return parts[-1][:80]
    return kind


def _extract_page_from_url(url: str) -> int | None:
    """Extract a valid 1-based 'p' (page) parameter from URL query string."""
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query)
    p_values = query_params.get("p", [])
    if p_values:
        try:
            page = int(p_values[0])
        except ValueError:
            return None
        return page if page >= 1 else None
    return None
