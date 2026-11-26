from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from b2t.config import Settings
from b2t.downloaders.base import Downloader
from b2t.models import DownloadResult, SourceRef


class YtDlpDownloader(Downloader):
    name = "yt-dlp"

    def __init__(self, cookies_from_browser: str | None = None) -> None:
        self.cookies_from_browser = cookies_from_browser

    def download(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        progress=None,
        keep_video: bool | None = None,
    ) -> DownloadResult:
        if not source.url and not source.bv:
            raise ValueError("yt-dlp downloader requires a remote url")

        settings.ensure_directories()

        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise RuntimeError(
                "yt-dlp is not installed. Run `uv sync` to install the core dependencies."
            ) from exc

        ydl_opts = self._build_ydl_opts(source, settings, keep_video=keep_video)
        if progress is not None:
            def progress_hook(data: dict[str, Any]) -> None:
                status = data.get("status")
                if status == "downloading":
                    total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
                    downloaded = data.get("downloaded_bytes") or 0
                    stage_progress = (downloaded / total) if total else None
                    progress.running(
                        "downloading",
                        message="downloading",
                        stage_progress=stage_progress,
                        indeterminate=stage_progress is None,
                    )
                elif status == "finished":
                    progress.running("downloading", message="download_finished", stage_progress=1.0)
            ydl_opts["progress_hooks"] = [progress_hook]
            ydl_opts["noprogress"] = False

        target_url = source.url or f"https://www.bilibili.com/video/{source.bv}"
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target_url, download=True)
            if "entries" in info and info["entries"]:
                info = info["entries"][0]
            info = ydl.sanitize_info(info)

            video_path = self._resolve_video_path(ydl, info)
            if not video_path.exists():
                raise RuntimeError(f"yt-dlp reported success but no file was found at {video_path}")

        return DownloadResult(
            source=source,
            video_path=video_path,
            title=info.get("title"),
            webpage_url=info.get("webpage_url") or source.url,
            metadata={
                "title": info.get("title"),
                "uploader": info.get("uploader"),
                "duration": info.get("duration"),
                "id": info.get("id"),
                "webpage_url": info.get("webpage_url") or source.url,
            },
        )

    def _build_ydl_opts(
        self,
        source: SourceRef,
        settings: Settings,
        *,
        keep_video: bool | None = None,
    ) -> dict[str, Any]:
        ydl_opts: dict[str, Any] = {
            "noplaylist": True,
            "outtmpl": str(settings.downloads_dir / "%(id)s.%(ext)s"),
            "noprogress": True,
            "quiet": True,
            "no_warnings": True,
        }

        # Transcription only needs the audio track, so download audio-only by
        # default instead of the best video (which can be a multi-GB 4K file
        # that `ffmpeg -vn` immediately discards). Pass keep_video=True or set
        # B2T_KEEP_VIDEO=1 / --keep-video to download and keep the full video.
        if keep_video is None:
            keep_video = os.getenv("B2T_KEEP_VIDEO", "").strip().lower() in {"1", "true", "yes", "on"}
        if keep_video:
            ydl_opts["format"] = "bv*+ba/b"
            ydl_opts["merge_output_format"] = "mp4"
        else:
            ydl_opts["format"] = "ba/bestaudio/best"

        # Cookies: env B2T_COOKIE_FILE > workspace cookies.txt
        cookie_file = os.getenv("B2T_COOKIE_FILE")
        if cookie_file:
            cookie_path = Path(cookie_file).expanduser()
        else:
            cookie_path = settings.workspace_root / "cookies.txt"
        if cookie_path.exists():
            ydl_opts["cookiefile"] = str(cookie_path)

        # Priority: constructor arg > B2T_COOKIES_FROM_BROWSER env var.
        # Useful for X / Instagram / Facebook / age-gated YouTube / B 站风控.
        cfbr = self.cookies_from_browser or os.getenv("B2T_COOKIES_FROM_BROWSER")
        if cfbr:
            ydl_opts["cookiesfrombrowser"] = (cfbr.strip(),)

        # Default: ignore system proxy (helps B 站 CDN). Set B2T_USE_PROXY=1 to keep it.
        # YouTube / 海外站若需代理，请显式打开。
        use_proxy = os.getenv("B2T_USE_PROXY", "").strip().lower() in {"1", "true", "yes", "on"}
        if not use_proxy:
            ydl_opts["proxy"] = ""

        if source.kind == "bilibili" and source.page is not None:
            ydl_opts["playlist_items"] = str(source.page)
            ydl_opts["noplaylist"] = False
            ydl_opts["outtmpl"] = str(settings.downloads_dir / "%(id)s.%(playlist_index)02d.%(ext)s")
        return ydl_opts

    def _resolve_video_path(self, ydl: Any, info: dict[str, Any]) -> Path:
        requested_downloads = info.get("requested_downloads") or []
        for requested in requested_downloads:
            filepath = requested.get("filepath")
            if filepath:
                return Path(filepath)

        prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared

        merged_mp4 = prepared.with_suffix(".mp4")
        if merged_mp4.exists():
            return merged_mp4

        return prepared
