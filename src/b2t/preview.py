from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from b2t.error_help import explain_error
from b2t.inputs import is_remote_kind, parse_source


def preview_source(
    raw_input: str,
    *,
    cookies_from_browser: str | None = None,
    cookie_file: str | Path | None = None,
) -> dict[str, Any]:
    try:
        source = parse_source(raw_input)
    except ValueError as exc:
        help_payload = explain_error(exc)
        return {
            "ok": False,
            "error": str(exc),
            "help": help_payload,
        }

    if source.kind in {"audio", "video"}:
        assert source.path is not None
        path = source.path
        size = path.stat().st_size if path.exists() else 0
        return {
            "ok": True,
            "kind": source.kind,
            "display_name": source.display_name,
            "title": path.name,
            "thumbnail": None,
            "duration": None,
            "uploader": None,
            "url": None,
            "bv": None,
            "path": str(path),
            "size_bytes": size,
            "readable": True,
            "message": "本地文件可读",
        }

    if not is_remote_kind(source.kind) or not source.url:
        return {
            "ok": False,
            "kind": source.kind,
            "error": "unsupported source for preview",
            "help": explain_error("unsupported source for preview"),
        }

    try:
        info = _extract_remote_info(
            source.url,
            cookies_from_browser=cookies_from_browser,
            cookie_file=cookie_file,
        )
    except Exception as exc:  # noqa: BLE001 - surface as preview failure
        help_payload = explain_error(exc)
        return {
            "ok": False,
            "kind": source.kind,
            "bv": source.bv,
            "url": source.url,
            "error": str(exc),
            "help": help_payload,
        }

    thumbnail = info.get("thumbnail")
    thumbnails = info.get("thumbnails") or []
    if not thumbnail and thumbnails:
        thumbnail = thumbnails[-1].get("url")

    return {
        "ok": True,
        "kind": source.kind,
        "display_name": source.display_name,
        "title": info.get("title") or source.display_name,
        "thumbnail": thumbnail,
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("creator") or info.get("channel"),
        "url": info.get("webpage_url") or source.url,
        "bv": source.bv,
        "path": None,
        "size_bytes": None,
        "readable": True,
        "message": "预览读取成功",
    }


def _extract_remote_info(
    url: str,
    *,
    cookies_from_browser: str | None = None,
    cookie_file: str | Path | None = None,
) -> dict[str, Any]:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise RuntimeError(
            "yt-dlp is not installed. Run `uv sync` to install the core dependencies."
        ) from exc

    ydl_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    env_cookie = os.getenv("B2T_COOKIE_FILE")
    candidates = [
        Path(env_cookie).expanduser() if env_cookie else None,
        Path(cookie_file).expanduser() if cookie_file else None,
    ]
    for candidate in candidates:
        if candidate is not None and candidate.exists():
            ydl_opts["cookiefile"] = str(candidate)
            break
    cfbr = cookies_from_browser or os.getenv("B2T_COOKIES_FROM_BROWSER")
    if cfbr:
        ydl_opts["cookiesfrombrowser"] = (cfbr.strip(),)
    use_proxy = os.getenv("B2T_USE_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
    if not use_proxy:
        ydl_opts["proxy"] = ""

    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info and info["entries"]:
            info = info["entries"][0]
        return ydl.sanitize_info(info)
