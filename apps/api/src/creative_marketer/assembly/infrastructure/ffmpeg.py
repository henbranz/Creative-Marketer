from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from pathlib import Path

from creative_marketer.assembly.domain import (
    AssemblyOutputInvalid,
    AssemblyPlan,
    AssemblyRenderFailed,
    AssemblyRenderTimeout,
    FitMode,
    OverlayStyle,
)
from creative_marketer.assembly.rendering import MaterializedSource, RenderResult


class FFmpegAssemblyRenderer:
    """Pinned-profile renderer. It accepts typed values and always invokes argv without a shell."""

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        font_family: str = "Noto Sans",
        maximum_sources: int = 24,
        maximum_output_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.font_family = font_family
        self.maximum_sources = maximum_sources
        self.maximum_output_bytes = maximum_output_bytes

    async def version(self) -> str:
        stdout = await self._run((self.ffmpeg, "-version"), timeout=10)
        first = stdout.decode("utf-8", "replace").splitlines()[0] if stdout else ""
        match = re.fullmatch(r"ffmpeg version ([^ ]+).*", first)
        if match is None:
            raise RuntimeError("FFmpeg is unavailable or returned an unsupported version")
        return match.group(1)[:128]

    async def source_has_audio(self, path: str, workspace: str) -> bool:
        source = Path(path).resolve()
        root = Path(workspace).resolve()
        if root not in source.parents:
            raise AssemblyRenderFailed("ASSEMBLY_SOURCE_PATH_INVALID")
        raw = await self._run(
            (
                self.ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "json",
                str(source),
            ),
            timeout=20,
        )
        try:
            return bool(json.loads(raw).get("streams"))
        except (ValueError, TypeError) as error:
            raise AssemblyRenderFailed("ASSEMBLY_SOURCE_INVALID") from error

    async def render(
        self, plan: AssemblyPlan, sources: tuple[MaterializedSource, ...], workspace: str
    ) -> RenderResult:
        if len(sources) != len(plan.items) or not 1 <= len(sources) <= self.maximum_sources:
            raise AssemblyRenderFailed("ASSEMBLY_SOURCE_LIMIT")
        root = Path(workspace).resolve()
        if not root.is_dir():
            raise AssemblyRenderFailed("ASSEMBLY_WORKSPACE_INVALID")
        version = await self.version()
        any_audio = any(source.has_audio for source in sources)
        clips: list[Path] = []
        for index, (item, source) in enumerate(zip(plan.items, sources, strict=True)):
            source_path = Path(source.path).resolve()
            if root not in source_path.parents or not source_path.is_file():
                raise AssemblyRenderFailed("ASSEMBLY_SOURCE_PATH_INVALID")
            clip = root / f"clip-{index:03d}.mp4"
            clips.append(clip)
            duration = f"{item.timeline_duration_ms / 1000:.3f}"
            fit = self._fit_filter(
                item.fit_mode, plan.render_profile.width, plan.render_profile.height
            )
            video_filter = (
                f"{fit},fps={plan.render_profile.fps},format={plan.render_profile.pixel_format}"
            )
            args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            if source.media_kind == "IMAGE":
                args += ["-loop", "1", "-i", str(source_path)]
            else:
                args += ["-i", str(source_path)]
            if any_audio and not source.has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            args += [
                "-vf",
                video_filter,
                "-t",
                duration,
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                "24",
            ]
            if any_audio:
                if source.has_audio:
                    args += [
                        "-af",
                        "aresample=48000:async=1:first_pts=0,apad",
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0",
                    ]
                else:
                    args += ["-map", "0:v:0", "-map", "1:a:0"]
                args += ["-c:a", "aac", "-ar", "48000", "-ac", "2", "-shortest"]
            else:
                args += ["-an"]
            args += ["-movflags", "+faststart", str(clip)]
            await self._run(tuple(args), timeout=self._timeout(plan.timeline_duration_ms))
        concat = root / "concat.txt"
        concat.write_text(
            "".join(f"file 'clip-{index:03d}.mp4'\n" for index in range(len(clips))),
            encoding="utf-8",
        )
        joined = root / "joined.mp4"
        await self._run(
            (
                self.ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-protocol_whitelist",
                "file,pipe",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(concat),
                "-c",
                "copy",
                str(joined),
            ),
            timeout=self._timeout(plan.timeline_duration_ms),
        )
        final = root / "final.mp4"
        ass = root / "overlays.ass"
        self._write_ass(plan, ass)
        args = [
            self.ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(joined),
        ]
        if plan.overlays or plan.captions:
            args += [
                "-vf",
                f"ass={self._filter_path(ass)}",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
            ]
        else:
            args += ["-c:v", "copy"]
        if any_audio:
            args += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-ar", "48000"]
        else:
            args += ["-an"]
        args += ["-movflags", "+faststart", str(final)]
        await self._run(tuple(args), timeout=self._timeout(plan.timeline_duration_ms))
        return await self._inspect(final, plan, version, any_audio)

    @staticmethod
    def _fit_filter(mode: FitMode, width: int, height: int) -> str:
        if mode is FitMode.CROP_FILL:
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
                f"crop={width}:{height}"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black"
        )

    @staticmethod
    def _filter_path(path: Path) -> str:
        """Escape a controlled absolute path for FFmpeg's filter parser."""
        return str(path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

    def _write_ass(self, plan: AssemblyPlan, target: Path) -> None:
        header = (
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\nWrapStyle: 0\n"
            "[V4+ Styles]\n"
            "Format: Name,Fontname,Fontsize,PrimaryColour,OutlineColour,BorderStyle,"
            "Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Hook,{self.font_family},72,&H00FFFFFF,&H80000000,1,4,1,8,90,90,250,1\n"
            f"Style: Body,{self.font_family},54,&H00FFFFFF,&H80000000,1,3,1,2,100,100,330,1\n"
            f"Style: CTA,{self.font_family},64,&H00FFFFFF,&H802A1785,3,2,0,2,110,110,360,1\n"
            f"Style: Disclaimer,{self.font_family},30,&H00FFFFFF,&H80000000,1,2,0,2,90,90,180,1\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
        )
        rows: list[str] = []
        for item in plan.overlays:
            style = {
                OverlayStyle.HOOK_PRIMARY: "Hook",
                OverlayStyle.BODY_SUBTITLE: "Body",
                OverlayStyle.CTA_PRIMARY: "CTA",
                OverlayStyle.DISCLAIMER: "Disclaimer",
            }[item.style_token]
            rows.append(
                f"Dialogue: 0,{self._ass_time(item.start_ms)},"
                f"{self._ass_time(item.end_ms)},{style},,0,0,0,,"
                f"{self._ass_text(item.text)}"
            )
        for cue in plan.captions:
            rows.append(
                f"Dialogue: 0,{self._ass_time(cue.start_ms)},"
                f"{self._ass_time(cue.end_ms)},Body,,0,0,0,,{self._ass_text(cue.text)}"
            )
        target.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")

    @staticmethod
    def _ass_text(value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace("{", "\\{")
            .replace("}", "\\}")
            .replace("\r", " ")
            .replace("\n", "\\N")
        )

    @staticmethod
    def _ass_time(milliseconds: int) -> str:
        centiseconds = milliseconds // 10
        hours, rem = divmod(centiseconds, 360_000)
        minutes, rem = divmod(rem, 6_000)
        seconds, cs = divmod(rem, 100)
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

    @staticmethod
    def _timeout(duration_ms: int) -> float:
        return max(30.0, min(600.0, duration_ms / 1000 * 12.0))

    async def _inspect(
        self, path: Path, plan: AssemblyPlan, version: str, expected_audio: bool
    ) -> RenderResult:
        if not path.is_file() or not 0 < path.stat().st_size <= self.maximum_output_bytes:
            raise AssemblyOutputInvalid("ASSEMBLY_OUTPUT_INVALID")
        with path.open("rb") as handle:
            prefix = handle.read(32)
        if len(prefix) < 12 or prefix[4:8] != b"ftyp":
            raise AssemblyOutputInvalid("ASSEMBLY_OUTPUT_INVALID")
        raw = await self._run(
            (
                self.ffprobe,
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ),
            timeout=20,
        )
        try:
            value = json.loads(raw)
            video = next(stream for stream in value["streams"] if stream["codec_type"] == "video")
            has_audio = any(stream["codec_type"] == "audio" for stream in value["streams"])
            duration_ms = round(float(value["format"]["duration"]) * 1000)
            rate = video.get("avg_frame_rate", "0/1").split("/")
            fps = round(int(rate[0]) / int(rate[1]))
        except (KeyError, ValueError, TypeError, StopIteration, ZeroDivisionError) as error:
            raise AssemblyOutputInvalid("ASSEMBLY_OUTPUT_INVALID") from error
        if (
            video.get("codec_name") != "h264"
            or video.get("pix_fmt") != "yuv420p"
            or (video.get("width"), video.get("height"), fps) != (1080, 1920, 24)
            or has_audio != expected_audio
            or abs(duration_ms - plan.timeline_duration_ms) > 250
        ):
            raise AssemblyOutputInvalid("ASSEMBLY_OUTPUT_INVALID")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return RenderResult(
            str(path),
            "ffmpeg",
            version,
            duration_ms,
            1080,
            1920,
            24,
            has_audio,
            path.stat().st_size,
            "sha256:" + digest.hexdigest(),
        )

    @staticmethod
    async def _run(argv: tuple[str, ...], *, timeout: float) -> bytes:
        if not argv or any("\x00" in value for value in argv):
            raise AssemblyRenderFailed("ASSEMBLY_RENDER_FAILED")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
            )
        except OSError as error:
            raise RuntimeError("FFmpeg/ffprobe is unavailable") from error
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise AssemblyRenderTimeout("ASSEMBLY_RENDER_TIMEOUT") from error
        if process.returncode != 0:
            diagnostic = hashlib.sha256(stderr[:16_384]).hexdigest()[:16]
            raise AssemblyRenderFailed(f"ASSEMBLY_RENDER_FAILED:{diagnostic}")
        return stdout
