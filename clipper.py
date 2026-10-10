from __future__ import annotations

import base64
import io
import ipaddress
import json
import math
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


def validate_youtube_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise ValueError("Enter a valid YouTube or youtu.be video URL.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    is_youtube = (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
    )
    if parsed.scheme not in {"http", "https"} or not is_youtube:
        raise ValueError("Enter a valid YouTube or youtu.be video URL.")
    return url.strip()


def validate_public_video_url(url: str) -> str:
    try:
        parsed = urlparse(url.strip())
    except ValueError as exc:
        raise ValueError("The Reel video URL must be a valid public HTTPS URL.") from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise ValueError("The Reel video URL must be an HTTPS public URL.")
    if host == "localhost" or host.endswith(".localhost"):
        raise ValueError("The Reel video URL must be publicly accessible, not local.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        raise ValueError("The Reel video URL must use a public IP address.")
    return url.strip()


def validate_clip_range(start_seconds: float, duration_seconds: float) -> None:
    if not math.isfinite(start_seconds) or start_seconds < 0:
        raise ValueError("Clip start must be a finite number greater than or equal to 0.")
    if not math.isfinite(duration_seconds) or not 1 <= duration_seconds <= 180:
        raise ValueError("Clip duration must be between 1 and 180 seconds.")


def plan_clip_segments(
    video_duration: float,
    start_seconds: float,
    clip_duration_seconds: float,
    max_clips: int,
) -> list[tuple[float, float]]:
    validate_clip_range(start_seconds, clip_duration_seconds)
    if not math.isfinite(video_duration) or video_duration <= 0:
        raise ValueError("The video duration could not be determined.")
    if start_seconds >= video_duration:
        raise ValueError("Clip start must be before the end of the source video.")
    if max_clips < 1:
        raise ValueError("Create at least one clip.")

    segments: list[tuple[float, float]] = []
    current = start_seconds
    while current < video_duration and len(segments) < max_clips:
        remaining = video_duration - current
        if remaining < 1:
            break
        duration = min(clip_duration_seconds, remaining)
        segments.append((current, duration))
        current += duration
    if not segments:
        raise ValueError("The selected start leaves less than one second to clip.")
    return segments


def get_video_duration(input_path: Path) -> float:
    from moviepy import VideoFileClip

    if not input_path.is_file():
        raise FileNotFoundError(f"Source video not found: {input_path}")
    with VideoFileClip(str(input_path)) as source:
        duration = float(source.duration or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("The video duration could not be determined.")
    return duration


def center_crop_box(width: int, height: int) -> tuple[int, int, int, int]:
    if width <= 0 or height <= 0:
        raise ValueError("Video dimensions must be positive.")
    target_ratio = 9 / 16
    if width / height > target_ratio:
        crop_width = max(1, round(height * target_ratio))
        left = (width - crop_width) // 2
        return left, 0, left + crop_width, height
    crop_height = max(1, round(width / target_ratio))
    top = (height - crop_height) // 2
    return 0, top, width, top + crop_height


def download_youtube_video(url: str, destination: Path) -> tuple[Path, str]:
    import imageio_ffmpeg
    import yt_dlp

    validated_url = validate_youtube_url(url)
    destination.mkdir(parents=True, exist_ok=True)
    options: dict[str, Any] = {
        "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
        "merge_output_format": "mp4",
        "outtmpl": str(destination / "source.%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as downloader:
            info = downloader.extract_info(validated_url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise RuntimeError(f"YouTube download failed: {exc}") from exc

    files = [
        path
        for path in destination.glob("source.*")
        if path.is_file() and not path.name.endswith((".part", ".ytdl"))
    ]
    if len(files) != 1:
        raise RuntimeError("The downloader did not produce exactly one source video.")
    return files[0], str(info.get("title") or "Downloaded video")


def make_vertical_clip(
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> float:
    from moviepy import VideoFileClip

    validate_clip_range(start_seconds, duration_seconds)
    if not input_path.is_file():
        raise FileNotFoundError(f"Source video not found: {input_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with VideoFileClip(str(input_path)) as source:
        if start_seconds >= source.duration:
            raise ValueError("Clip start must be before the end of the source video.")
        end_seconds = min(start_seconds + duration_seconds, source.duration)
        clip = source.subclipped(start_seconds, end_seconds)
        left, top, right, bottom = center_crop_box(clip.w, clip.h)
        vertical = clip.cropped(x1=left, y1=top, x2=right, y2=bottom)
        try:
            vertical.resized(new_size=(1080, 1920)).write_videofile(
                str(output_path),
                codec="libx264",
                audio_codec="aac",
                fps=30,
                preset="medium",
                logger=None,
            )
        finally:
            vertical.close()
            clip.close()
        return end_seconds - start_seconds


def generate_video_metadata(
    video_path: Path,
    title: str,
    api_key: str,
    model_name: str = "gemini-3.8-flash",
) -> dict[str, Any]:
    from google import genai
    from google.genai import errors

    if not api_key.strip():
        raise ValueError("Enter a Gemini API key to generate AI metadata.")

    client = genai.Client(api_key=api_key.strip())
    uploaded = client.files.upload(file=str(video_path))
    if not uploaded.name:
        raise RuntimeError("Gemini uploaded the clip without returning a file name.")

    prompt = f"""Analyze the visual and spoken content of this short video, whose source title is {json.dumps(title)}.
Return JSON with these exact fields:
- virality_score: integer from 1 to 100, an AI estimate, not measured audience performance
- virality_reason: concise explanation grounded in this clip
- caption: engaging social caption based on this clip
- hashtags: space-separated list of 5 to 8 relevant hashtags
Do not claim that the score is measured or guaranteed."""
    try:
        deadline = time.monotonic() + 180
        state = getattr(uploaded.state, "name", str(uploaded.state)).upper()
        while state.endswith("PROCESSING") and time.monotonic() < deadline:
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
            state = getattr(uploaded.state, "name", str(uploaded.state)).upper()
        if not state.endswith("ACTIVE"):
            raise RuntimeError(f"Gemini video processing did not finish (state: {state}).")

        try:
            response = client.interactions.create(
                model=model_name,
                input=[
                    {
                        "type": "video",
                        "uri": uploaded.uri,
                        "mime_type": uploaded.mime_type,
                    },
                    {"type": "text", "text": prompt},
                ],
            )
        except errors.APIError as exc:
            raise RuntimeError(f"Gemini API request failed: {exc}") from exc
        if not response.output_text:
            raise RuntimeError("Gemini returned an empty metadata response.")
        result = json.loads(response.output_text)
        if not isinstance(result, dict):
            raise RuntimeError("Gemini returned metadata in an unexpected format.")
        score = result.get("virality_score")
        if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 100:
            raise RuntimeError("Gemini returned an invalid virality score.")
        for field in ("virality_reason", "caption", "hashtags"):
            if not isinstance(result.get(field), str) or not result[field].strip():
                raise RuntimeError(f"Gemini returned an invalid {field} value.")
        return result
    finally:
        try:
            client.files.delete(name=uploaded.name)
        except errors.APIError as exc:
            raise RuntimeError(
                "Gemini analysis ended, but the temporary uploaded video could not be deleted."
            ) from exc


def generate_video_metadata_together(
    video_path: Path,
    title: str,
    api_key: str,
    model_name: str = "Qwen/Qwen3.5-9B",
) -> dict[str, Any]:
    import requests
    from moviepy import VideoFileClip
    from PIL import Image

    if not api_key.strip():
        raise ValueError("Enter a Together AI API key to generate clip metadata.")
    if not video_path.is_file():
        raise FileNotFoundError(f"Clip not found: {video_path}")

    images: list[dict[str, Any]] = []
    with VideoFileClip(str(video_path)) as video:
        duration = float(video.duration or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("The clip duration could not be determined.")
        for position in (0.1, 0.5, 0.9):
            frame = video.get_frame(min(duration * position, max(0, duration - 0.01)))
            image = Image.fromarray(frame).convert("RGB")
            image.thumbnail((559, 559))
            image_bytes = io.BytesIO()
            image.save(image_bytes, format="JPEG", quality=80)
            image_data = base64.b64encode(image_bytes.getvalue()).decode("ascii")
            images.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{image_data}"},
                }
            )

    prompt = f"""Analyze these three frames sampled from a short video titled {json.dumps(title)}.
Base your feedback on visible content, not assumed audience statistics. Return JSON with exactly:
- virality_score: integer from 1 to 100, an AI estimate and not measured performance
- virality_reason: concise explanation grounded in the sampled frames
- caption: engaging social caption based on visible content
- hashtags: space-separated list of 5 to 8 relevant hashtags
Do not claim the score is measured or guaranteed."""
    try:
        response = requests.post(
            "https://api.together.ai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key.strip()}",
                "Content-Type": "application/json",
            },
            json={
                "model": model_name,
                "messages": [
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": prompt}, *images],
                    }
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": 700,
            },
            timeout=90,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not connect to Together AI: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Together AI returned a non-JSON response.") from exc
    if not response.ok:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(
            f"Together AI request failed ({response.status_code}): "
            f"{message or 'check your API key, model, and account limits.'}"
        )
    try:
        result = payload["choices"][0]["message"]["content"]
        if not isinstance(result, str) or not result:
            raise ValueError
        metadata = json.loads(result)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise RuntimeError("Together AI returned metadata in an unexpected format.") from exc
    if not isinstance(metadata, dict):
        raise RuntimeError("Together AI returned metadata in an unexpected format.")
    score = metadata.get("virality_score")
    if isinstance(score, bool) or not isinstance(score, int) or not 1 <= score <= 100:
        raise RuntimeError("Together AI returned an invalid virality score.")
    for field in ("virality_reason", "caption", "hashtags"):
        if not isinstance(metadata.get(field), str) or not metadata[field].strip():
            raise RuntimeError(f"Together AI returned an invalid {field} value.")
    return metadata


def _graph_request(
    method: str,
    url: str,
    access_token: str,
    *,
    data: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    import requests

    try:
        response = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            data=data,
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Could not connect to Meta Graph API: {exc}") from exc
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("Meta returned a non-JSON response.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Meta returned an unexpected response.")
    if not response.ok or "error" in payload:
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        raise RuntimeError(f"Meta Graph API error: {message or response.status_code}")
    return payload


def _graph_base_url(version: str) -> str:
    if not re.fullmatch(r"v\d{1,2}\.0", version):
        raise ValueError("Set META_GRAPH_API_VERSION to a supported version such as vNN.0.")
    return f"https://graph.facebook.com/{version}"


def create_instagram_reel_container(
    user_id: str,
    access_token: str,
    api_version: str,
    video_url: str,
    caption: str,
) -> str:
    public_url = validate_public_video_url(video_url)
    payload = _graph_request(
        "POST",
        f"{_graph_base_url(api_version)}/{user_id}/media",
        access_token,
        data={"media_type": "REELS", "video_url": public_url, "caption": caption},
    )
    container_id = payload.get("id")
    if not isinstance(container_id, str) or not container_id:
        raise RuntimeError("Meta did not return a Reel container ID.")
    return container_id


def get_instagram_container_status(
    container_id: str, access_token: str, api_version: str
) -> str:
    payload = _graph_request(
        "GET",
        f"{_graph_base_url(api_version)}/{container_id}",
        access_token,
        params={"fields": "status_code,status"},
    )
    status = payload.get("status_code")
    if not isinstance(status, str) or not status:
        raise RuntimeError("Meta did not return a Reel container status.")
    return status.upper()


def publish_instagram_reel(
    user_id: str, container_id: str, access_token: str, api_version: str
) -> str:
    payload = _graph_request(
        "POST",
        f"{_graph_base_url(api_version)}/{user_id}/media_publish",
        access_token,
        data={"creation_id": container_id},
    )
    media_id = payload.get("id")
    if not isinstance(media_id, str) or not media_id:
        raise RuntimeError("Meta did not return a published Reel ID.")
    return media_id
