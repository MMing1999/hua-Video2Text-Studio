from __future__ import annotations

import platform
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from b2t.ai_transform import AISettings, resolve_ai_settings, transform_text
from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.error_help import explain_error
from b2t.i18n import tr
from b2t.inputs import AUDIO_SUFFIXES, VIDEO_SUFFIXES, parse_source_list
from b2t.library import WorkspaceLibrary
from b2t.models import TaskRecord
from b2t.preview import preview_source
from b2t.tasks import TaskService
from b2t.user_config import AIConfig, AppConfig


class TranscribeTaskRequest(BaseModel):
    source: str
    provider: str = "whisper"
    model: str = "small"
    prompt: str = ""
    keep_video: bool = False
    output_format: str = "txt"


class BatchTranscribeTaskRequest(BaseModel):
    sources: list[str] | None = None
    source_text: str | None = None
    provider: str = "whisper"
    model: str = "small"
    prompt: str = ""
    keep_video: bool = False
    output_format: str = "txt"


class TranscriptUpdateRequest(BaseModel):
    text: str
    kind: str | None = None
    output_format: str | None = None


class CategoryRequest(BaseModel):
    name: str | None = None
    category_id: int | None = None


class TagRequest(BaseModel):
    name: str | None = None
    tag_id: int | None = None


class PreviewRequest(BaseModel):
    source: str


class RevealRequest(BaseModel):
    path: str


class AISettingsUpdateRequest(BaseModel):
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None


class DownloadSettingsUpdateRequest(BaseModel):
    cookies_from_browser: str | None = None


class AITransformRequest(BaseModel):
    text: str
    mode: str = Field(pattern="^(proofread|format)$")
    instruction: str | None = None


def create_app(
    *,
    task_service: TaskService,
    library: WorkspaceLibrary,
    database: AppDatabase,
    settings: Settings | None = None,
    default_provider: str = "whisper",
    default_model: str = "small",
    language: str = "zh-CN",
) -> FastAPI:
    workspace = settings or library.settings
    templates = Jinja2Templates(directory=str(Path(__file__).with_name("templates")))
    app = FastAPI(title="hua-Video2Text-Studio")

    project_root = Path(__file__).resolve().parents[2]
    assets_dir = project_root / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    def _load_config() -> AppConfig:
        return AppConfig.load(workspace)

    def _ai_public_payload(config: AppConfig) -> dict:
        resolved = resolve_ai_settings(
            AISettings(
                api_key=config.ai.api_key,
                base_url=config.ai.base_url,
                model=config.ai.model,
            )
        )
        key = resolved.api_key or ""
        masked = ""
        if key:
            masked = key if len(key) <= 8 else f"{key[:4]}…{key[-4:]}"
        return {
            "configured": bool(key.strip()),
            "api_key_masked": masked,
            "has_api_key": bool(key.strip()),
            "base_url": resolved.base_url,
            "model": resolved.model,
        }

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        config = _load_config()
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "default_provider": default_provider,
                "default_model": default_model,
                "language": language,
                "workspace_root": str(workspace.workspace_root.resolve()),
                "ai": _ai_public_payload(config),
            },
        )

    @app.post("/transcribe", response_class=HTMLResponse)
    async def transcribe_from_form(
        request: Request,
        source: str = Form(...),
        provider: str = Form("whisper"),
        model: str = Form("small"),
        prompt: str = Form(""),
        keep_video: str = Form(""),
    ) -> HTMLResponse:
        try:
            sources = parse_source_list(source)
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "default_provider": provider,
                    "default_model": model,
                    "language": language,
                    "workspace_root": str(workspace.workspace_root.resolve()),
                    "ai": _ai_public_payload(_load_config()),
                    "bootstrap_error": str(exc),
                    "bootstrap_source": source,
                },
                status_code=400,
            )

        tasks = _submit_transcription_tasks(
            task_service,
            sources=sources,
            provider=provider,
            model=model,
            prompt=prompt,
            keep_video=keep_video.strip().lower() in {"1", "true", "yes", "on"},
        )
        if len(tasks) > 1:
            return templates.TemplateResponse(
                request,
                "batch.html",
                {
                    "tasks": [asdict(task) for task in tasks],
                    "lang": language,
                    "t": lambda key, **kwargs: tr(language, key, **kwargs),
                },
            )

        task = tasks[0]
        return templates.TemplateResponse(
            request,
            "task.html",
            {
                "task_id": task.id,
                "lang": language,
                "t": lambda key, **kwargs: tr(language, key, **kwargs),
            },
        )

    @app.get("/tasks/batch", response_class=HTMLResponse)
    async def batch_task_page(request: Request, ids: str = Query("")) -> HTMLResponse:
        task_ids = [task_id.strip() for task_id in ids.split(",") if task_id.strip()]
        tasks = []
        for task_id in task_ids:
            task = database.get_task(task_id)
            if task is not None:
                tasks.append(asdict(task))
        return templates.TemplateResponse(
            request,
            "batch.html",
            {
                "tasks": tasks,
                "lang": language,
                "t": lambda key, **kwargs: tr(language, key, **kwargs),
            },
        )

    @app.get("/tasks/{task_id}", response_class=HTMLResponse)
    async def task_page(request: Request, task_id: str) -> HTMLResponse:
        task = database.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return templates.TemplateResponse(
            request,
            "task.html",
            {
                "task_id": task_id,
                "task": asdict(task),
                "lang": language,
                "t": lambda key, **kwargs: tr(language, key, **kwargs),
            },
        )

    @app.get("/videos/{video_id}", response_class=HTMLResponse)
    async def video_page(request: Request, video_id: int) -> HTMLResponse:
        video = database.get_video(video_id)
        if video is None:
            raise HTTPException(status_code=404, detail="video not found")
        transcript = library.load_active_transcript(video_id)
        versions = [asdict(version) for version in database.list_transcript_versions(video_id)]
        return templates.TemplateResponse(
            request,
            "video.html",
            {
                "video": video,
                "transcript": transcript,
                "versions": versions,
                "categories": database.list_categories(),
                "tags": database.list_tags(),
                "lang": language,
                "t": lambda key, **kwargs: tr(language, key, **kwargs),
            },
        )

    @app.post("/videos/{video_id}/edit")
    async def edit_video_transcript(video_id: int, text: str = Form(...)) -> RedirectResponse:
        library.save_edited_transcript(video_id, text)
        return RedirectResponse(url=f"/videos/{video_id}", status_code=303)

    @app.post("/videos/{video_id}/category")
    async def assign_video_category(video_id: int, category_name: str = Form("")) -> RedirectResponse:
        category_name = category_name.strip()
        category_id = None
        if category_name:
            category = database.create_category(category_name)
            category_id = int(category["id"])
        database.assign_category(video_id, category_id)
        return RedirectResponse(url=f"/videos/{video_id}", status_code=303)

    @app.post("/videos/{video_id}/tags")
    async def add_video_tag(video_id: int, tag_name: str = Form("")) -> RedirectResponse:
        tag_name = tag_name.strip()
        if tag_name:
            tag = database.create_tag(tag_name)
            database.add_video_tag(video_id, int(tag["id"]))
        return RedirectResponse(url=f"/videos/{video_id}", status_code=303)

    @app.post("/api/preview")
    async def preview_api(payload: PreviewRequest) -> JSONResponse:
        config = _load_config()
        cookie_path = workspace.workspace_root / "cookies.txt"
        return JSONResponse(
            preview_source(
                payload.source,
                cookies_from_browser=config.download.cookies_from_browser.strip() or None,
                cookie_file=cookie_path if cookie_path.exists() else None,
            )
        )

    @app.get("/api/thumbnail")
    async def thumbnail_proxy(url: str = Query(...)) -> Response:
        """Proxy remote cover images to bypass CDN hotlink checks in the browser."""
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=400, detail="invalid thumbnail url")
        host = parsed.netloc.lower()
        allowed = (
            "hdslb.com",
            "bilibili.com",
            "bilivideo.com",
            "akamaized.net",
            "ytimg.com",
            "googleusercontent.com",
            "ggpht.com",
            "acfun.cn",
            "aixifan.com",
            "twimg.com",
            "cdninstagram.com",
            "fbcdn.net",
            "facebook.com",
            "douyinpic.com",
            "douyin.com",
            "xhscdn.com",
            "xiaohongshu.com",
            "rednote.com",
        )
        if not any(host == domain or host.endswith("." + domain) for domain in allowed):
            raise HTTPException(status_code=400, detail="thumbnail host not allowed")

        referer = "https://www.bilibili.com/"
        if any(host.endswith(d) for d in ("ytimg.com", "googleusercontent.com", "ggpht.com")):
            referer = "https://www.youtube.com/"
        elif any(host.endswith(d) for d in ("acfun.cn", "aixifan.com")):
            referer = "https://www.acfun.cn/"
        elif any(host.endswith(d) for d in ("twimg.com",)):
            referer = "https://x.com/"
        elif any(host.endswith(d) for d in ("cdninstagram.com",)):
            referer = "https://www.instagram.com/"
        elif any(host.endswith(d) for d in ("fbcdn.net", "facebook.com")):
            referer = "https://www.facebook.com/"
        elif any(host.endswith(d) for d in ("douyinpic.com", "douyin.com")):
            referer = "https://www.douyin.com/"
        elif any(host.endswith(d) for d in ("xhscdn.com", "xiaohongshu.com", "rednote.com")):
            referer = "https://www.xiaohongshu.com/"

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
                "Referer": referer,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as resp:
                content = resp.read()
                content_type = resp.headers.get("Content-Type") or "image/jpeg"
        except urllib.error.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"thumbnail fetch failed: HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"thumbnail fetch failed: {exc.reason}") from exc

        return Response(
            content=content,
            media_type=content_type.split(";")[0].strip() or "image/jpeg",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @app.post("/api/upload")
    async def upload_media(file: UploadFile = File(...)) -> JSONResponse:
        filename = Path(file.filename or "upload.bin").name
        suffix = Path(filename).suffix.lower()
        allowed = AUDIO_SUFFIXES | VIDEO_SUFFIXES
        if suffix not in allowed:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported file type: {suffix or '(none)'}. allowed: {', '.join(sorted(allowed))}",
            )

        workspace.ensure_directories()
        stem = Path(filename).stem
        safe_stem = re.sub(r"[^\w.-]+", "-", stem, flags=re.UNICODE).strip("-._") or "upload"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = workspace.uploads_dir / f"{safe_stem}-{stamp}{suffix}"

        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="empty file")
        target.write_bytes(data)
        path = str(target.resolve())
        return JSONResponse(
            {
                "ok": True,
                "path": path,
                "filename": filename,
                "size_bytes": len(data),
            }
        )

    @app.get("/api/workspace")
    async def workspace_api() -> JSONResponse:
        def folder_info(path: Path) -> dict:
            exists = path.exists()
            count = 0
            if exists and path.is_dir():
                count = sum(1 for item in path.iterdir() if item.is_file())
            return {
                "path": str(path.resolve()),
                "exists": exists,
                "file_count": count,
            }

        return JSONResponse(
            {
                "workspace_root": str(workspace.workspace_root.resolve()),
                "folders": {
                    "downloads": folder_info(workspace.downloads_dir),
                    "uploads": folder_info(workspace.uploads_dir),
                    "audio": folder_info(workspace.audio_dir),
                    "transcripts_original": folder_info(workspace.transcripts_original_dir),
                    "transcripts_edited": folder_info(workspace.transcripts_edited_dir),
                    "metadata": folder_info(workspace.metadata_dir),
                },
            }
        )

    @app.post("/api/workspace/reveal")
    async def reveal_path(payload: RevealRequest) -> JSONResponse:
        target = Path(payload.path).expanduser()
        if not target.exists():
            raise HTTPException(status_code=404, detail="path not found")
        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(["open", "-R", str(target)], check=False)
            elif system == "Windows":
                subprocess.run(["explorer", "/select,", str(target)], check=False)
            else:
                parent = target if target.is_dir() else target.parent
                subprocess.run(["xdg-open", str(parent)], check=False)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        return JSONResponse({"ok": True, "path": str(target)})

    def _download_public_payload(config: AppConfig) -> dict:
        cookie_path = workspace.workspace_root / "cookies.txt"
        return {
            "cookies_from_browser": config.download.cookies_from_browser or "",
            "cookie_file": str(cookie_path),
            "cookie_file_exists": cookie_path.exists(),
        }

    @app.get("/api/settings/ai")
    async def get_ai_settings() -> JSONResponse:
        return JSONResponse(_ai_public_payload(_load_config()))

    @app.put("/api/settings/ai")
    async def update_ai_settings(payload: AISettingsUpdateRequest) -> JSONResponse:
        config = _load_config()
        if payload.api_key is not None:
            config.ai.api_key = payload.api_key.strip()
        if payload.base_url is not None:
            config.ai.base_url = payload.base_url.strip() or AIConfig().base_url
        if payload.model is not None:
            config.ai.model = payload.model.strip() or AIConfig().model
        config.save(workspace)
        return JSONResponse(_ai_public_payload(config))

    @app.get("/api/settings/download")
    async def get_download_settings() -> JSONResponse:
        return JSONResponse(_download_public_payload(_load_config()))

    @app.put("/api/settings/download")
    async def update_download_settings(payload: DownloadSettingsUpdateRequest) -> JSONResponse:
        config = _load_config()
        if payload.cookies_from_browser is not None:
            value = payload.cookies_from_browser.strip().lower()
            allowed = {"", "chrome", "safari", "firefox", "edge", "brave", "chromium"}
            if value not in allowed and ":" not in value:
                raise HTTPException(
                    status_code=400,
                    detail="cookies_from_browser must be empty or chrome/safari/firefox/edge/brave",
                )
            config.download.cookies_from_browser = value
        config.save(workspace)
        return JSONResponse(_download_public_payload(config))

    @app.post("/api/ai/transform")
    async def ai_transform(payload: AITransformRequest) -> JSONResponse:
        config = _load_config()
        try:
            text = transform_text(
                payload.text,
                mode=payload.mode,  # type: ignore[arg-type]
                settings=resolve_ai_settings(
                    AISettings(
                        api_key=config.ai.api_key,
                        base_url=config.ai.base_url,
                        model=config.ai.model,
                    )
                ),
                instruction=payload.instruction,
            )
        except Exception as exc:  # noqa: BLE001
            help_payload = explain_error(exc)
            raise HTTPException(
                status_code=400,
                detail={"message": str(exc), "help": help_payload},
            ) from exc
        return JSONResponse({"text": text, "mode": payload.mode})

    @app.get("/api/videos/{video_id}/transcript/download")
    async def download_transcript(video_id: int, version_id: int | None = Query(None)) -> FileResponse:
        if version_id is None:
            data = library.load_active_transcript(video_id)
        else:
            data = library.load_transcript_version(video_id, version_id)
        path = Path(str(data["file_path"]))
        if not path.exists():
            raise HTTPException(status_code=404, detail="transcript file not found")
        return FileResponse(path, filename=path.name, media_type="text/plain; charset=utf-8")

    @app.post("/api/tasks/transcribe")
    async def create_transcription_task(payload: TranscribeTaskRequest) -> JSONResponse:
        task = task_service.submit_transcription(
            source=payload.source,
            provider=payload.provider,
            model=payload.model,
            prompt=payload.prompt,
            keep_video=payload.keep_video,
            output_format=payload.output_format,
        )
        return JSONResponse({"task_id": task.id, "status": task.status})

    @app.post("/api/tasks/batch")
    async def create_batch_transcription_tasks(payload: BatchTranscribeTaskRequest) -> JSONResponse:
        source_chunks = list(payload.sources or [])
        if payload.source_text:
            source_chunks.append(payload.source_text)
        try:
            sources = parse_source_list("\n".join(source_chunks))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        tasks = _submit_transcription_tasks(
            task_service,
            sources=sources,
            provider=payload.provider,
            model=payload.model,
            prompt=payload.prompt,
            keep_video=payload.keep_video,
            output_format=payload.output_format,
        )
        return JSONResponse(
            {
                "items": [asdict(task) for task in tasks],
                "count": len(tasks),
            }
        )

    @app.get("/api/tasks")
    async def list_tasks(
        status: str | None = Query(None),
        provider: str | None = Query(None),
    ) -> JSONResponse:
        return JSONResponse(
            {
                "items": [
                    asdict(task)
                    for task in task_service.list_tasks()
                    if (status is None or task.status == status)
                    and (provider is None or task.provider == provider)
                ],
                "filters": {"status": status, "provider": provider},
            }
        )

    @app.get("/api/tasks/{task_id}")
    async def get_task(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        payload = asdict(task)
        if task.status == "failed" and task.error_message:
            payload["help"] = explain_error(task.error_message)
        return JSONResponse(payload)

    @app.get("/api/tasks/{task_id}/progress")
    async def get_task_progress(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        payload = asdict(task)
        if task.status == "failed" and task.error_message:
            payload["help"] = explain_error(task.error_message)
        return JSONResponse(payload)

    @app.get("/api/tasks/{task_id}/events")
    async def get_task_events(task_id: str) -> JSONResponse:
        task = task_service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        return JSONResponse({"items": database.list_task_events(task_id)})

    @app.get("/api/videos")
    async def list_videos_api(
        query: str | None = Query(None),
        category_id: int | None = Query(None),
        tag_id: int | None = Query(None),
    ) -> JSONResponse:
        return JSONResponse(
            {
                "items": database.list_videos(query=query, category_id=category_id, tag_id=tag_id),
                "filters": {
                    "query": query,
                    "category_id": category_id,
                    "tag_id": tag_id,
                },
            }
        )

    @app.get("/api/videos/{video_id}")
    async def get_video_api(video_id: int) -> JSONResponse:
        video = database.get_video(video_id)
        if video is None:
            raise HTTPException(status_code=404, detail="video not found")
        return JSONResponse(video)

    @app.get("/api/videos/{video_id}/transcript")
    async def get_video_transcript(video_id: int, version_id: int | None = Query(None)) -> JSONResponse:
        if version_id is None:
            return JSONResponse(library.load_active_transcript(video_id))
        return JSONResponse(library.load_transcript_version(video_id, version_id))

    @app.get("/api/videos/{video_id}/transcript/props")
    async def get_video_transcript_props(video_id: int) -> JSONResponse:
        try:
            return JSONResponse(library.load_transcript_props(video_id))
        except RuntimeError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/videos/{video_id}/metadata")
    async def get_video_metadata(video_id: int) -> JSONResponse:
        return JSONResponse(library.load_video_metadata(video_id))

    @app.put("/api/videos/{video_id}/transcript")
    async def update_video_transcript(video_id: int, payload: TranscriptUpdateRequest) -> JSONResponse:
        version_id = library.save_edited_transcript(
            video_id,
            payload.text,
            kind=payload.kind or "edited",
            output_format=payload.output_format,
        )
        return JSONResponse({"video_id": video_id, "version_id": version_id})

    @app.get("/api/videos/{video_id}/versions")
    async def list_video_versions(video_id: int) -> JSONResponse:
        versions = [asdict(version) for version in database.list_transcript_versions(video_id)]
        return JSONResponse({"items": versions})

    @app.get("/api/videos/{video_id}/versions/{version_id}")
    async def get_video_version(video_id: int, version_id: int) -> JSONResponse:
        return JSONResponse(library.load_transcript_version(video_id, version_id))

    @app.post("/api/videos/{video_id}/versions/{version_id}/activate")
    async def activate_video_version(video_id: int, version_id: int) -> JSONResponse:
        database.activate_transcript_version(video_id, version_id)
        return JSONResponse({"video_id": video_id, "version_id": version_id})

    @app.get("/api/categories")
    async def list_categories_api() -> JSONResponse:
        return JSONResponse({"items": database.list_categories()})

    @app.post("/api/categories")
    async def create_category_api(payload: CategoryRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        category = database.create_category(payload.name)
        return JSONResponse(category)

    @app.put("/api/categories/{category_id}")
    async def update_category_api(category_id: int, payload: CategoryRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        category = database.update_category(category_id, payload.name)
        if category is None:
            raise HTTPException(status_code=404, detail="category not found")
        return JSONResponse(category)

    @app.delete("/api/categories/{category_id}")
    async def delete_category_api(category_id: int) -> JSONResponse:
        database.delete_category(category_id)
        return JSONResponse({"category_id": category_id})

    @app.post("/api/videos/{video_id}/category")
    async def assign_category_api(video_id: int, payload: CategoryRequest) -> JSONResponse:
        category_id = payload.category_id
        if category_id is None and payload.name:
            category = database.create_category(payload.name)
            category_id = int(category["id"])
        database.assign_category(video_id, category_id)
        return JSONResponse({"video_id": video_id, "category_id": category_id})

    @app.get("/api/tags")
    async def list_tags_api() -> JSONResponse:
        return JSONResponse({"items": database.list_tags()})

    @app.post("/api/tags")
    async def create_tag_api(payload: TagRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        tag = database.create_tag(payload.name)
        return JSONResponse(tag)

    @app.put("/api/tags/{tag_id}")
    async def update_tag_api(tag_id: int, payload: TagRequest) -> JSONResponse:
        if not payload.name:
            raise HTTPException(status_code=400, detail="name is required")
        tag = database.update_tag(tag_id, payload.name)
        if tag is None:
            raise HTTPException(status_code=404, detail="tag not found")
        return JSONResponse(tag)

    @app.delete("/api/tags/{tag_id}")
    async def delete_tag_api(tag_id: int) -> JSONResponse:
        database.delete_tag(tag_id)
        return JSONResponse({"tag_id": tag_id})

    @app.post("/api/videos/{video_id}/tags")
    async def add_video_tag_api(video_id: int, payload: TagRequest) -> JSONResponse:
        tag_id = payload.tag_id
        if tag_id is None and payload.name:
            tag = database.create_tag(payload.name)
            tag_id = int(tag["id"])
        if tag_id is None:
            raise HTTPException(status_code=400, detail="tag is required")
        database.add_video_tag(video_id, tag_id)
        return JSONResponse({"video_id": video_id, "tag_id": tag_id})

    @app.delete("/api/videos/{video_id}/tags/{tag_id}")
    async def remove_video_tag_api(video_id: int, tag_id: int) -> JSONResponse:
        database.remove_video_tag(video_id, tag_id)
        return JSONResponse({"video_id": video_id, "tag_id": tag_id})

    @app.get("/health")
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    return app


def _submit_transcription_tasks(
    task_service: TaskService,
    *,
    sources: list[str],
    provider: str,
    model: str,
    prompt: str = "",
    keep_video: bool = False,
    output_format: str = "txt",
) -> list[TaskRecord]:
    return [
        task_service.submit_transcription(
            source=source,
            provider=provider,
            model=model,
            prompt=prompt,
            keep_video=keep_video,
            output_format=output_format,
        )
        for source in sources
    ]
