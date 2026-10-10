from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv

from clipper import (
    create_instagram_reel_container,
    download_youtube_video,
    generate_video_metadata,
    generate_video_metadata_together,
    get_instagram_container_status,
    get_video_duration,
    make_vertical_clip,
    plan_clip_segments,
    publish_instagram_reel,
    validate_clip_range,
    validate_public_video_url,
    validate_youtube_url,
)

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "outputs"
OUTPUT_DIR.mkdir(exist_ok=True)
load_dotenv(ROOT / ".env")

st.set_page_config(page_title="SideHustle AI Clipper", page_icon="🎬", layout="centered")
st.title("SideHustle AI Clipper")
st.caption("Create portrait clips locally, generate optional AI metadata, and publish Reels.")

with st.sidebar:
    st.subheader("Optional integrations")
    st.caption("Credentials stay on this computer. They are not saved by this app.")
    ai_provider = st.selectbox("AI provider", ["Together AI", "Gemini"])
    gemini_key = st.text_input(
        "Gemini API key",
        value=os.getenv("GEMINI_API_KEY", ""),
        type="password",
        help="Optional. Add GEMINI_API_KEY to .env or paste a key for this session.",
    )
    together_key = st.text_input(
        "Together AI API key",
        value=os.getenv("TOGETHER_API_KEY", ""),
        type="password",
        help="Optional. Add TOGETHER_API_KEY to .env or paste a rotated key for this session.",
    )
    st.caption("Instagram publishing requires an Instagram professional account, a connected Facebook Page, Meta permissions, and a publicly accessible HTTPS video URL.")

source_mode = st.radio("Video source", ["YouTube URL", "Upload a video"], horizontal=True)
youtube_url = ""
uploaded_video = None
if source_mode == "YouTube URL":
    youtube_url = st.text_input("YouTube video URL", placeholder="https://www.youtube.com/watch?v=...")
else:
    uploaded_video = st.file_uploader(
        "Choose a video",
        type=["mp4", "mov", "m4v", "webm"],
        help="The selected source video is processed on this computer.",
    )

source_title = st.text_input("Video title", value="")
first, second = st.columns(2)
with first:
    start_seconds = st.number_input("Clip start (seconds)", min_value=0.0, value=0.0, step=1.0)
with second:
    duration_seconds = st.number_input(
        "Clip length (seconds)", min_value=1, max_value=180, value=45, step=1
    )
max_clips = st.number_input(
    "Maximum number of clips",
    min_value=1,
    max_value=20,
    value=10,
    step=1,
    help="Clips are cut consecutively from the chosen start. The final clip may be shorter.",
)

if st.button("Create multiple 9:16 clips", type="primary", use_container_width=True):
    created_paths: list[Path] = []
    try:
        validate_clip_range(start_seconds, float(duration_seconds))
        if source_mode == "YouTube URL":
            validate_youtube_url(youtube_url)
            if not youtube_url:
                raise ValueError("Enter a YouTube video URL.")
        elif uploaded_video is None:
            raise ValueError("Choose a video file to upload.")

        clip_id = uuid.uuid4().hex[:12]
        with tempfile.TemporaryDirectory(prefix="sidehustle-") as temporary_directory:
            temporary_path = Path(temporary_directory)
            if source_mode == "YouTube URL":
                source_path, downloaded_title = download_youtube_video(
                    youtube_url, temporary_path / "source"
                )
                title = source_title.strip() or downloaded_title
            else:
                suffix = Path(uploaded_video.name).suffix.lower() or ".mp4"
                source_path = temporary_path / f"source{suffix}"
                source_path.write_bytes(uploaded_video.getvalue())
                title = source_title.strip() or Path(uploaded_video.name).stem
            source_duration = get_video_duration(source_path)
            segments = plan_clip_segments(
                source_duration,
                float(start_seconds),
                float(duration_seconds),
                int(max_clips),
            )
            clips: list[dict[str, Any]] = []
            progress = st.progress(0, text="Preparing clips...")
            for index, (segment_start, segment_duration) in enumerate(segments, start=1):
                output_path = OUTPUT_DIR / f"clip-{clip_id}-{index:02d}.mp4"
                created_paths.append(output_path)
                actual_duration = make_vertical_clip(
                    source_path,
                    output_path,
                    segment_start,
                    segment_duration,
                )
                clips.append(
                    {
                        "path": str(output_path),
                        "title": title,
                        "duration": actual_duration,
                        "start": segment_start,
                    }
                )
                progress.progress(
                    index / len(segments),
                    text=f"Created clip {index} of {len(segments)}...",
                )

        st.session_state["clips"] = clips
        st.session_state["selected_clip_index"] = 0
        st.session_state.pop("metadata", None)
        st.session_state.pop("reel_caption", None)
        st.session_state.pop("ig_container_id", None)
        st.session_state.pop("ig_status", None)
        omitted = (
            " A final fragment shorter than 1 second was skipped."
            if source_duration - (segments[-1][0] + segments[-1][1]) > 0
            and source_duration - (segments[-1][0] + segments[-1][1]) < 1
            else ""
        )
        if len(segments) == int(max_clips) and (
            segments[-1][0] + segments[-1][1] < source_duration
        ):
            omitted += " The maximum clip count was reached; the remaining source was not processed."
        st.success(f"Created {len(clips)} clips.{omitted}")
    except (ValueError, OSError, RuntimeError) as error:
        for created_path in created_paths:
            created_path.unlink(missing_ok=True)
        st.error(str(error))

clips: list[dict[str, Any]] = st.session_state.get("clips", [])
available_clips = [
    clip for clip in clips if Path(clip["path"]).is_file()
]
if available_clips:
    clip_labels = [
        f"Clip {index + 1} · {clip['duration']:.1f}s · {Path(clip['path']).name}"
        for index, clip in enumerate(available_clips)
    ]
    selected_label = st.selectbox(
        "Select a clip to preview, analyze, or publish",
        clip_labels,
        index=min(
            int(st.session_state.get("selected_clip_index", 0)),
            len(available_clips) - 1,
        ),
    )
    selected_index = clip_labels.index(selected_label)
    st.session_state["selected_clip_index"] = selected_index
    selected_clip = available_clips[selected_index]
    clip_path = Path(selected_clip["path"])
    if st.session_state.get("active_clip_path") != str(clip_path):
        st.session_state["active_clip_path"] = str(clip_path)
        st.session_state.pop("metadata", None)
        st.session_state.pop("metadata_clip_path", None)
        st.session_state["reel_caption"] = ""
    st.subheader(f"Clip {selected_index + 1} of {len(available_clips)}")
    st.video(str(clip_path))
    st.download_button(
        "Download MP4",
        data=clip_path.read_bytes(),
        file_name=clip_path.name,
        mime="video/mp4",
        use_container_width=True,
    )
    with st.expander(f"Preview and download all {len(available_clips)} clips"):
        for index, clip in enumerate(available_clips, start=1):
            item_path = Path(clip["path"])
            st.markdown(f"**Clip {index}** · starts at {clip['start']:.1f}s · {clip['duration']:.1f}s")
            st.video(str(item_path))
            st.download_button(
                f"Download clip {index}",
                data=item_path.read_bytes(),
                file_name=item_path.name,
                mime="video/mp4",
                key=f"all-clips-download-{item_path.name}",
            )
    st.caption(
        f"{selected_clip['title']} · starts at {selected_clip['start']:.1f}s · "
        f"{selected_clip['duration']:.1f} seconds · 9:16 · 1080×1920"
    )

    st.subheader("AI content analysis")
    if ai_provider == "Together AI":
        st.caption("Together AI analyzes three sampled video frames. The virality score is an AI estimate, not real performance data.")
        ai_key = together_key
    else:
        st.caption("Gemini analyzes the clip's video content. The virality score is an AI estimate, not real performance data.")
        ai_key = gemini_key
    if st.button("Analyze clip and write caption", disabled=not ai_key.strip()):
        try:
            with st.spinner(f"Analyzing clip with {ai_provider}..."):
                if ai_provider == "Together AI":
                    metadata = generate_video_metadata_together(
                        clip_path,
                        selected_clip["title"],
                        together_key,
                        os.getenv("TOGETHER_MODEL", "Qwen/Qwen3.5-9B"),
                    )
                else:
                    metadata = generate_video_metadata(
                        clip_path,
                        selected_clip["title"],
                        gemini_key,
                        os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                    )
            st.session_state["metadata"] = metadata
            st.session_state["metadata_clip_path"] = str(clip_path)
            st.session_state["reel_caption"] = (
                f"{metadata['caption']}\n\n{metadata['hashtags']}"
            )
            (OUTPUT_DIR / f"{clip_path.stem}.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except (ValueError, OSError, RuntimeError) as error:
            st.error(str(error))

    metadata: dict[str, Any] | None = st.session_state.get("metadata")
    if metadata:
        st.metric("AI-estimated virality", f"{metadata['virality_score']}/100")
        st.write(metadata["virality_reason"])
        caption_default = f"{metadata['caption']}\n\n{metadata['hashtags']}"
    else:
        caption_default = ""
    if "reel_caption" not in st.session_state:
        st.session_state["reel_caption"] = caption_default

    st.subheader("Instagram Reels")
    st.warning(
        "Meta fetches the video from a public URL. This app does not upload your local file "
        "to a hosting service; publish only after hosting the MP4 at a public HTTPS URL."
    )
    public_video_url = st.text_input(
        "Public video URL",
        placeholder="https://your-public-host.example/clip.mp4",
        help="Must be directly reachable by Meta without login, cookies, or a local-network address.",
    )
    reel_caption = st.text_area("Reel caption", max_chars=2200, key="reel_caption")
    access_token = os.getenv("IG_ACCESS_TOKEN", "")
    instagram_user_id = os.getenv("IG_USER_ID", "")
    graph_version = os.getenv("META_GRAPH_API_VERSION", "")
    container_id = st.session_state.get("ig_container_id")
    credentials_ready = bool(access_token and instagram_user_id and graph_version)
    if not credentials_ready:
        st.info("Set IG_ACCESS_TOKEN, IG_USER_ID, and META_GRAPH_API_VERSION in .env to enable publishing.")

    if container_id:
        st.info(f"Existing Reel container: {container_id}. Continue with this container; do not create another.")
        if st.button("Check status and publish when ready", disabled=not credentials_ready):
            try:
                status = get_instagram_container_status(
                    container_id, access_token, graph_version
                )
                st.session_state.pop("ig_status", None)
                st.write(f"Meta container status: {status}")
                if status == "FINISHED":
                    media_id = publish_instagram_reel(
                        instagram_user_id, container_id, access_token, graph_version
                    )
                    st.session_state.pop("ig_container_id", None)
                    st.session_state.pop("ig_status", None)
                    st.success(f"Reel published successfully (media ID: {media_id}).")
                elif status == "PUBLISHED":
                    st.session_state.pop("ig_container_id", None)
                    st.session_state.pop("ig_status", None)
                    st.success("This Reel container has already been published.")
                elif status == "IN_PROGRESS":
                    st.info("Meta is still processing this Reel. Check again later.")
                elif status in {"ERROR", "EXPIRED"}:
                    st.session_state["ig_status"] = status
                    st.error(f"Meta could not publish this container (status: {status}).")
                else:
                    st.error(f"Unexpected Meta container status: {status}")
            except (ValueError, RuntimeError) as error:
                st.error(str(error))
        if st.session_state.get("ig_status") in {"ERROR", "EXPIRED"}:
            if st.button("Clear failed container"):
                st.session_state.pop("ig_container_id", None)
                st.session_state.pop("ig_status", None)
                st.rerun()
    elif st.button("Create Reel container", disabled=not credentials_ready):
        try:
            validate_public_video_url(public_video_url)
            if not reel_caption.strip():
                raise ValueError("Enter a caption before publishing.")
            container_id = create_instagram_reel_container(
                instagram_user_id,
                access_token,
                graph_version,
                public_video_url,
                reel_caption.strip(),
            )
            st.session_state["ig_container_id"] = container_id
            st.session_state.pop("ig_status", None)
            st.success(
                f"Container created ({container_id}). Meta may need time to process it; "
                "use the status button to continue without creating a duplicate."
            )
            st.rerun()
        except (ValueError, RuntimeError) as error:
            st.error(str(error))

st.divider()
st.subheader("Recent local clips")
recent_clips = sorted(OUTPUT_DIR.glob("clip-*.mp4"), key=lambda path: path.stat().st_mtime, reverse=True)
if not recent_clips:
    st.caption("Clips you create are kept in the local outputs folder.")
else:
    for saved_clip in recent_clips[:10]:
        saved_time = datetime.fromtimestamp(saved_clip.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        sidecar = saved_clip.with_suffix(".json")
        score_text = ""
        if sidecar.is_file():
            try:
                history_metadata = json.loads(sidecar.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                history_metadata = {}
            score_text = f" · AI score {history_metadata.get('virality_score', 'n/a')}/100"
        st.write(f"{saved_clip.name} · {saved_time}{score_text}")
        st.download_button(
            f"Download {saved_clip.name}",
            data=saved_clip.read_bytes(),
            file_name=saved_clip.name,
            mime="video/mp4",
            key=f"download-{saved_clip.name}",
        )
