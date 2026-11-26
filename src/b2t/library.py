from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from b2t.config import Settings
from b2t.database import AppDatabase
from b2t.inputs import safe_stem
from b2t.models import TranscriptResult


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class WorkspaceLibrary:
    def __init__(self, settings: Settings, database: AppDatabase) -> None:
        self.settings = settings
        self.database = database
        self.settings.ensure_directories()

    def register_transcript_result(self, result: TranscriptResult) -> int:
        transcript_path = self._ensure_original_transcript(result)
        metadata_path = self._ensure_metadata_file(result, transcript_path)
        text = transcript_path.read_text(encoding="utf-8")

        video_id = self.database.create_video(
            source_kind=result.source.kind,
            source_input=result.source.raw_input,
            source_url=result.source.url,
            source_bv=result.source.bv,
            title=(result.metadata.get("download") or {}).get("title") or result.source.display_name,
            display_name=result.source.display_name,
            language=result.metadata.get("language"),
            engine=result.engine,
            model=result.model,
            video_path=str(result.video_path) if result.video_path else None,
            audio_path=str(result.audio_path),
            metadata_path=str(metadata_path),
        )
        if self.database.get_active_transcript_version(video_id) is None:
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )
        return video_id

    def save_edited_transcript(
        self,
        video_id: int,
        text: str,
        *,
        kind: str = "edited",
        output_format: str | None = None,
    ) -> int:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")

        suffix = self._resolve_transcript_suffix(video_id, output_format=output_format)
        base_name = safe_stem(str(video["display_name"]) or f"video-{video_id}")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.settings.transcripts_edited_dir / f"{base_name}-{video_id}-{timestamp}{suffix}"
        path.write_text(text.rstrip() + "\n", encoding="utf-8")
        version_kind = (kind or "edited").strip() or "edited"
        return self.database.create_transcript_version(
            video_id=video_id,
            kind=version_kind,
            file_path=str(path),
            text_sha256=sha256_text(text.rstrip() + "\n"),
            char_count=len(text.rstrip() + "\n"),
            is_active=True,
        )

    def load_transcript_props(self, video_id: int) -> dict[str, object]:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")
        version = self.database.get_active_transcript_version(video_id)
        if version is None:
            raise RuntimeError(f"active transcript not found for video {video_id}")
        path = Path(version.file_path)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        stat = path.stat() if path.exists() else None
        words = [part for part in text.split() if part]
        return {
            "video_id": video_id,
            "version_id": version.id,
            "kind": version.kind,
            "title": video.get("title") or video.get("display_name") or "",
            "file_path": str(path.resolve()) if path.exists() else version.file_path,
            "suffix": path.suffix.lower() or ".txt",
            "format": "markdown" if path.suffix.lower() == ".md" else "txt",
            "size_bytes": int(stat.st_size) if stat else 0,
            "created_at": video.get("created_at") or version.created_at,
            "modified_at": (
                datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds")
                if stat
                else version.updated_at
            ),
            "char_count": version.char_count or len(text),
            "word_count": len(words),
            "engine": video.get("engine") or "",
            "model": video.get("model") or "",
            "source_kind": video.get("source_kind") or "",
            "source_input": video.get("source_input") or "",
        }

    def _resolve_transcript_suffix(self, video_id: int, *, output_format: str | None = None) -> str:
        fmt = (output_format or "").strip().lower()
        if fmt in {"md", "markdown"}:
            return ".md"
        if fmt in {"txt", "text"}:
            return ".txt"
        active = self.database.get_active_transcript_version(video_id)
        if active is not None:
            suffix = Path(active.file_path).suffix.lower()
            if suffix in {".md", ".txt"}:
                return suffix
        try:
            meta = self.load_video_metadata(video_id)
            if str(meta.get("output_format") or "").lower() in {"md", "markdown"}:
                return ".md"
        except Exception:  # noqa: BLE001
            pass
        return ".txt"

    def load_active_transcript(self, video_id: int) -> dict[str, object]:
        version = self.database.get_active_transcript_version(video_id)
        if version is None:
            raise RuntimeError(f"active transcript not found for video {video_id}")
        return self.load_transcript_version(video_id, version.id)

    def load_transcript_version(self, video_id: int, version_id: int) -> dict[str, object]:
        version = self.database.get_transcript_version(video_id, version_id)
        if version is None:
            raise RuntimeError(f"transcript version not found: video={video_id} version={version_id}")
        path = Path(version.file_path)
        return {
            "version_id": version.id,
            "kind": version.kind,
            "file_path": version.file_path,
            "is_active": version.is_active,
            "text": path.read_text(encoding="utf-8"),
        }

    def load_video_metadata(self, video_id: int) -> dict[str, object]:
        video = self.database.get_video(video_id)
        if video is None:
            raise RuntimeError(f"video not found: {video_id}")
        metadata_path = Path(str(video["metadata_path"]))
        return json.loads(metadata_path.read_text(encoding="utf-8"))

    def index_existing_workspace(self) -> None:
        self.settings.ensure_directories()
        for metadata_path in sorted(self.settings.metadata_dir.glob("*.json")):
            transcript_path = self.settings.transcripts_original_dir / f"{metadata_path.stem}.txt"
            if not transcript_path.exists():
                md_path = self.settings.transcripts_original_dir / f"{metadata_path.stem}.md"
                if md_path.exists():
                    transcript_path = md_path
            if not transcript_path.exists():
                fallback = metadata_path.with_suffix(".txt")
                if fallback.exists():
                    transcript_path = fallback
                else:
                    fallback_md = metadata_path.with_suffix(".md")
                    if fallback_md.exists():
                        transcript_path = fallback_md
            if not transcript_path.exists():
                continue

            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            source = data.get("source") or {}
            video_id = self.database.create_video(
                source_kind=source.get("kind") or "audio",
                source_input=source.get("raw_input") or transcript_path.stem,
                source_url=source.get("url"),
                source_bv=source.get("bv"),
                title=(data.get("download") or {}).get("title") or transcript_path.stem,
                display_name=transcript_path.stem,
                language=data.get("language"),
                engine=data.get("engine") or "unknown",
                model=str(data.get("model") or ""),
                video_path=data.get("video_path"),
                audio_path=data.get("audio_path") or "",
                metadata_path=str(metadata_path),
            )
            if self.database.get_active_transcript_version(video_id) is not None:
                continue
            text = transcript_path.read_text(encoding="utf-8")
            self.database.create_transcript_version(
                video_id=video_id,
                kind="original",
                file_path=str(transcript_path),
                text_sha256=sha256_text(text),
                char_count=len(text),
                is_active=True,
            )

    def _ensure_original_transcript(self, result: TranscriptResult) -> Path:
        target = self.settings.transcripts_original_dir / result.transcript_path.name
        if result.transcript_path.resolve() != target.resolve():
            target.write_text(result.transcript_path.read_text(encoding="utf-8"), encoding="utf-8")
        return target

    def _ensure_metadata_file(self, result: TranscriptResult, transcript_path: Path) -> Path:
        target = self.settings.metadata_dir / f"{transcript_path.stem}.json"
        metadata = {
            **result.metadata,
            "engine": result.engine,
            "model": result.model,
            "audio_path": str(result.audio_path),
            "video_path": str(result.video_path) if result.video_path else None,
            "transcript_path": str(transcript_path),
        }
        target.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        return target
