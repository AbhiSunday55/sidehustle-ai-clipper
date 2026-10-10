# SideHustle AI Clipper

A local Streamlit app for downloading or selecting a video, making a 9:16 clip,
optionally analyzing the actual clip with Gemini, and publishing an Instagram Reel
through Meta's Graph API.

## What works

- Downloads a YouTube video with `yt-dlp` or accepts a local video upload.
- Splits the selected source from a chosen start into consecutive clips, then
  center-crops/resizes each to an H.264/AAC 1080×1920 MP4.
- Shows each generated clip in a selector with an individual preview and download.
  The batch is capped at 20 clips; the final clip can be shorter than the selected
  clip length.
- Optionally uploads that clip to Gemini for visual/audio analysis, an explicitly
  estimated (not measured) virality score, a caption, and hashtags.
- Creates and checks a Meta Reel media container, then publishes that same
  container when processing is complete. A pending container ID is kept in the
  app session so checking again does not create a duplicate.
- Keeps generated MP4s and AI metadata in the local `outputs` folder.

## Requirements

- Python 3.10 or newer.
- Internet access for package installation, YouTube downloads, and optional APIs.
- API credentials only for the optional Gemini and Instagram features.

MoviePy uses the `imageio-ffmpeg` package for FFmpeg. It is installed with the
Python dependencies; a separate system FFmpeg installation is normally not needed.

## Install and run in VS Code (Windows)

Open this project folder in VS Code, then run these commands in its PowerShell
terminal:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
Copy-Item .env.example .env
streamlit run app.py
```

If PowerShell blocks virtual-environment activation, use the environment's
executable directly instead:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\streamlit.exe run app.py
```

The app opens in your browser. Generated clips are saved under `outputs`.
Keep `.env` private; it is excluded from Git by `.gitignore`.

## Optional Gemini analysis

For Together AI, set a newly generated `TOGETHER_API_KEY` in `.env` and select
Together AI in the app. The default vision model is `Qwen/Qwen3.5-9B`; override it
with `TOGETHER_MODEL`. Together receives three downscaled JPEG frames from the
selected clip and returns a caption, hashtags, and a model-estimated score.

For Gemini, create a Gemini API key in Google AI Studio and set `GEMINI_API_KEY` in `.env`.
The app uses the Gemini Interactions API with the video-understanding model set by
`GEMINI_MODEL` (default `gemini-3.8-flash`), uploads the generated clip for analysis,
and deletes the uploaded Gemini file after the request. API quotas, pricing, and
regional availability are controlled by Google. The score is a model-generated
estimate, not evidence of actual reach or virality.

## Optional Instagram publishing

Configure `IG_ACCESS_TOKEN`, `IG_USER_ID`, and `META_GRAPH_API_VERSION` in `.env`.
The Instagram account must meet Meta's current publishing requirements (including
professional-account/Page setup and the necessary app permissions). Choose a
Graph API version supported by your Meta app; versions expire.

Meta's publishing API fetches media from a publicly reachable HTTPS URL. This app
does not upload clips to a hosting service or expose your computer to the internet.
Host the finished MP4 yourself, enter its direct public URL, create one Reel
container, then use **Check status and publish when ready**. If Meta is still
processing, check again later; the same container is reused.

## Notes and limitations

- YouTube and social-platform terms, copyright, privacy, and account rules still
  apply. Only process and publish videos you are allowed to use.
- The crop is centered. It does not detect speakers or automatically find
  highlights; choose the start and duration yourself. Clips are consecutive
  segments, not AI-selected highlights.
- Scheduling, trend research, public hosting, and other social platforms are not
  implemented.
- A real end-to-end API publishing test requires your own valid credentials,
  eligible account, reachable hosted MP4, and Meta's approval/permissions. The
  app reports API errors instead of showing a fake success.

## Run tests

```powershell
python -m unittest discover -s tests -v
```
