from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from creative_marketer.assembly.domain import (
    AssemblyNormalizedClipInvalid,
    AssemblyOutputInvalid,
    AssemblyPlan,
    AssemblyRenderFailed,
    AssemblyRenderTimeout,
    FitMode,
    OverlayStyle,
)
from creative_marketer.assembly.rendering import MaterializedSource, RenderResult


@dataclass(frozen=True, slots=True)
class VideoProbe:
    codec: str
    pixel_format: str
    width: int
    height: int
    avg_frame_rate: str
    real_frame_rate: str
    time_base: str
    start_time: float
    duration_ms: int
    frame_count: int | None


class FFmpegAssemblyRenderer:
    """Pinned-profile renderer. It accepts typed values and always invokes argv without a shell."""

    def __init__(
        self,
        *,
        ffmpeg: str = "ffmpeg",
        ffprobe: str = "ffprobe",
        font_family: str = "Noto Sans",
        encoding_preset: str = "medium",
        scaling_flags: str = "lanczos",
        minimum_timeout_seconds: float = 30.0,
        maximum_timeout_seconds: float = 600.0,
        fake_placeholder_mode: bool = False,
        maximum_sources: int = 24,
        maximum_output_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        if encoding_preset not in {"medium", "veryfast", "ultrafast"}:
            raise ValueError("unsupported FFmpeg encoding preset")
        if scaling_flags not in {"lanczos", "fast_bilinear"}:
            raise ValueError("unsupported FFmpeg scaling flags")
        if not 1 <= minimum_timeout_seconds <= maximum_timeout_seconds <= 3600:
            raise ValueError("unsupported FFmpeg timeout bounds")
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.font_family = font_family
        self.encoding_preset = encoding_preset
        self.scaling_flags = scaling_flags
        self.minimum_timeout_seconds = minimum_timeout_seconds
        self.maximum_timeout_seconds = maximum_timeout_seconds
        self.fake_placeholder_mode = fake_placeholder_mode
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
        if self.fake_placeholder_mode and not any_audio:
            return await self._render_fake_placeholders(plan, sources, root, version)
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
                f"setpts=PTS-STARTPTS,{fit},fps={plan.render_profile.fps},"
                f"format={plan.render_profile.pixel_format}"
            )
            args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            if source.media_kind == "IMAGE":
                args += ["-loop", "1", "-i", str(source_path)]
            else:
                if item.source_in_ms:
                    args += ["-ss", f"{item.source_in_ms / 1000:.3f}"]
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
                self.encoding_preset,
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(plan.render_profile.fps),
            ]
            if any_audio:
                if source.has_audio:
                    args += [
                        "-af",
                        "asetpts=PTS-STARTPTS,aresample=48000:async=1:first_pts=0,apad",
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
            if source.media_kind == "VIDEO":
                await self._validate_video_clip(
                    clip,
                    expected_duration_ms=item.timeline_duration_ms,
                    width=plan.render_profile.width,
                    height=plan.render_profile.height,
                    fps=plan.render_profile.fps,
                )
        joined = root / "joined.mp4"
        join_args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
        for clip in clips:
            join_args += ["-i", str(clip)]
        if any_audio:
            inputs = "".join(
                f"[{index}:v:0]setpts=PTS-STARTPTS[v{index}];"
                f"[{index}:a:0]asetpts=PTS-STARTPTS[a{index}];"
                for index in range(len(clips))
            )
            pads = "".join(f"[v{index}][a{index}]" for index in range(len(clips)))
            graph = f"{inputs}{pads}concat=n={len(clips)}:v=1:a=1[outv][outa]"
            maps = ["-map", "[outv]", "-map", "[outa]"]
        else:
            inputs = "".join(
                f"[{index}:v:0]setpts=PTS-STARTPTS[v{index}];" for index in range(len(clips))
            )
            pads = "".join(f"[v{index}]" for index in range(len(clips)))
            graph = f"{inputs}{pads}concat=n={len(clips)}:v=1:a=0[outv]"
            maps = ["-map", "[outv]"]
        join_args += [
            "-filter_complex",
            graph,
            *maps,
            "-c:v",
            "libx264",
            "-preset",
            self.encoding_preset,
            "-crf",
            "18",
            "-pix_fmt",
            plan.render_profile.pixel_format,
            "-r",
            str(plan.render_profile.fps),
        ]
        if any_audio:
            join_args += ["-c:a", "aac", "-ar", "48000", "-ac", "2"]
        else:
            join_args += ["-an"]
        join_args += ["-movflags", "+faststart", str(joined)]
        await self._run(tuple(join_args), timeout=self._timeout(plan.timeline_duration_ms))
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
                self.encoding_preset,
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
        return await self._inspect(final, plan, f"assembly-motion-v2+ffmpeg-{version}", any_audio)

    async def _render_fake_placeholders(
        self,
        plan: AssemblyPlan,
        sources: tuple[MaterializedSource, ...],
        root: Path,
        version: str,
    ) -> RenderResult:
        """Render synthetic fake-provider media without encoding duplicate static frames."""
        ass = root / "overlays.ass"
        self._write_ass(plan, ass)
        boundaries = {0, plan.timeline_duration_ms}
        for item in plan.items:
            boundaries.update(
                (item.timeline_start_ms, item.timeline_start_ms + item.timeline_duration_ms)
            )
        for value in plan.overlays:
            boundaries.update((value.start_ms, value.end_ms))
        for cue in plan.captions:
            boundaries.update((cue.start_ms, cue.end_ms))
        ordered = sorted(boundaries)
        clips: list[Path] = []
        for ordinal, (start_ms, end_ms) in enumerate(pairwise(ordered)):
            if end_ms <= start_ms:
                continue
            item_index = next(
                index
                for index, item in enumerate(plan.items)
                if item.timeline_start_ms
                <= start_ms
                < item.timeline_start_ms + item.timeline_duration_ms
            )
            item, source = plan.items[item_index], sources[item_index]
            source_path = Path(source.path).resolve()
            if root not in source_path.parents or not source_path.is_file():
                raise AssemblyRenderFailed("ASSEMBLY_SOURCE_PATH_INVALID")
            frame = root / f"placeholder-frame-{ordinal:03d}.mp4"
            clip = root / f"placeholder-clip-{ordinal:03d}.mp4"
            clips.append(clip)
            args = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
            if source.media_kind == "IMAGE":
                # Fake providers intentionally emit non-production placeholder
                # bytes. Do not repeatedly decode those bytes: a malformed
                # synthetic image can make image2 loop forever without yielding
                # a frame. Instead, derive a stable visual placeholder from the
                # immutable source identity. Live media never uses this path.
                color = hashlib.sha256(source.asset_id.encode("utf-8")).hexdigest()[:6]
                args += [
                    "-f",
                    "lavfi",
                    "-i",
                    (
                        f"color=c=0x{color}:"
                        f"s={plan.render_profile.width}x{plan.render_profile.height}:"
                        f"r={plan.render_profile.fps}"
                    ),
                ]
            else:
                source_ms = item.source_in_ms + start_ms - item.timeline_start_ms
                # Demo video fixtures are intentionally tiny. Loop them before
                # seeking so later overlay/caption boundaries can still sample
                # a deterministic frame from the synthetic source.
                args += ["-stream_loop", "-1", "-ss", f"{source_ms / 1000:.3f}"]
                args += ["-i", str(source_path)]
            fit = self._fit_filter(
                item.fit_mode, plan.render_profile.width, plan.render_profile.height
            )
            args += [
                "-vf",
                (
                    f"{fit},setpts=PTS+{start_ms / 1000:.3f}/TB,"
                    f"ass={self._filter_path(ass)},setpts=PTS-STARTPTS,"
                    f"format={plan.render_profile.pixel_format}"
                ),
                "-t",
                "0.100",
                "-frames:v",
                "1",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-r",
                str(plan.render_profile.fps),
                "-an",
                str(frame),
            ]
            await self._run(tuple(args), timeout=self._timeout(end_ms - start_ms))
            frame_count = max(
                1,
                round((end_ms - start_ms) * plan.render_profile.fps / 1000),
            )
            await self._run(
                (
                    self.ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-stream_loop",
                    str(frame_count - 1),
                    "-i",
                    str(frame),
                    "-frames:v",
                    str(frame_count),
                    "-c:v",
                    "copy",
                    "-an",
                    "-movflags",
                    "+faststart",
                    str(clip),
                ),
                timeout=self._timeout(end_ms - start_ms),
            )
        concat = root / "placeholder-concat.txt"
        concat.write_text(
            "".join(f"file '{clip.name}'\n" for clip in clips),
            encoding="utf-8",
        )
        final = root / "final.mp4"
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
                "-movflags",
                "+faststart",
                str(final),
            ),
            timeout=self._timeout(plan.timeline_duration_ms),
        )
        return await self._inspect(final, plan, f"fake-placeholder-v1+ffmpeg-{version}", False)

    async def _validate_video_clip(
        self,
        path: Path,
        *,
        expected_duration_ms: int,
        width: int,
        height: int,
        fps: int,
    ) -> None:
        probe = await self._probe_video(path)
        checks = (
            (probe.codec == "h264", "codec"),
            (probe.pixel_format == "yuv420p", "pixel_format"),
            ((probe.width, probe.height) == (width, height), "dimensions"),
            (abs(self._rate(probe.avg_frame_rate) - fps) < 0.01, "average_frame_rate"),
            (abs(self._rate(probe.real_frame_rate) - fps) < 0.01, "real_frame_rate"),
            (abs(probe.start_time) <= 0.05, "start_time"),
            (abs(probe.duration_ms - expected_duration_ms) <= 250, "duration"),
            (probe.frame_count is not None and probe.frame_count > 1, "frame_count"),
        )
        failure = next((name for valid, name in checks if not valid), None)
        if failure is not None:
            # Only bounded stream metadata is surfaced; paths and media content stay private.
            diagnostic = json.dumps(
                {
                    "field": failure,
                    "codec": probe.codec[:16],
                    "pixel_format": probe.pixel_format[:16],
                    "dimensions": [probe.width, probe.height],
                    "avg_frame_rate": probe.avg_frame_rate[:24],
                    "r_frame_rate": probe.real_frame_rate[:24],
                    "time_base": probe.time_base[:24],
                    "start_time": round(probe.start_time, 6),
                    "duration_ms": probe.duration_ms,
                    "frame_count": probe.frame_count,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            raise AssemblyNormalizedClipInvalid(diagnostic)

    async def _probe_video(self, path: Path) -> VideoProbe:
        raw = await self._run(
            (
                self.ffprobe,
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                (
                    "stream=codec_name,pix_fmt,width,height,avg_frame_rate,r_frame_rate,"
                    "time_base,start_time,duration,nb_frames,nb_read_frames:format=duration"
                ),
                "-of",
                "json",
                str(path),
            ),
            timeout=20,
        )
        try:
            value = json.loads(raw)
            stream = value["streams"][0]
            duration = stream.get("duration") or value["format"]["duration"]
            raw_frames = stream.get("nb_read_frames") or stream.get("nb_frames")
            frame_count = None if raw_frames in {None, "N/A"} else int(raw_frames)
            return VideoProbe(
                codec=str(stream["codec_name"]),
                pixel_format=str(stream["pix_fmt"]),
                width=int(stream["width"]),
                height=int(stream["height"]),
                avg_frame_rate=str(stream["avg_frame_rate"]),
                real_frame_rate=str(stream["r_frame_rate"]),
                time_base=str(stream["time_base"]),
                start_time=float(stream.get("start_time", 0)),
                duration_ms=round(float(duration) * 1000),
                frame_count=frame_count,
            )
        except (KeyError, ValueError, TypeError, IndexError) as error:
            raise AssemblyNormalizedClipInvalid("probe") from error

    @staticmethod
    def _rate(value: str) -> float:
        try:
            numerator, denominator = value.split("/", 1)
            return int(numerator) / int(denominator)
        except (ValueError, ZeroDivisionError) as error:
            raise AssemblyNormalizedClipInvalid("frame_rate") from error

    def _fit_filter(self, mode: FitMode, width: int, height: int) -> str:
        if mode is FitMode.CROP_FILL:
            return (
                f"scale={width}:{height}:force_original_aspect_ratio=increase:"
                f"flags={self.scaling_flags},"
                f"crop={width}:{height}"
            )
        return (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:"
            f"flags={self.scaling_flags},"
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

    def _timeout(self, duration_ms: int) -> float:
        return max(
            self.minimum_timeout_seconds,
            min(self.maximum_timeout_seconds, duration_ms / 1000 * 12.0),
        )

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
