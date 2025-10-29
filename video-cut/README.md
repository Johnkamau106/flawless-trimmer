## VidSlicer

Paste a video link (YouTube, Instagram, Facebook, TikTok, Twitter/X), preview it, pick quality, trim a range, and download as video or audio. Saved clips (with thumbnails) are kept locally.

### Tech Stack
- Frontend: React (Vite), `react-player`, `axios`
- Backend: Flask, `yt-dlp`, `ffmpeg`
- Database: SQLite (local file)

### Features
- URL input with auto metadata fetch and platform detection
- Cleans YouTube Shorts tracking params (removes `si`)
- Preview player with controls
- Format selection (144p–4K when available) and audio-only (MP3)
- Range trimming via ffmpeg
- Download streaming
- Saved clips sidebar with thumbnail, title, and time range

---

## Prerequisites
- Python 3.10+
- Node.js 18+
- ffmpeg installed and available in PATH
  - Windows (PowerShell): `choco install ffmpeg` (or download from `https://ffmpeg.org/download.html`)
  - macOS: `brew install ffmpeg`
  - Ubuntu/Debian: `sudo apt update && sudo apt install -y ffmpeg`

---

## Local Development

1) Backend (Flask)
```bash
cd server
python -m venv .venv
# Windows
. .venv\\Scripts\\activate
# macOS/Linux
# source .venv/bin/activate
pip install -r requirements.txt
python app.py  # http://localhost:5000
```

2) Frontend (Vite + React)
```bash
cd ..
npm install
npm run dev  # http://localhost:5173
```

Frontend expects backend at `http://localhost:5000`. Override with env var:
```bash
# Windows PowerShell (temporary)
$env:VITE_API_BASE_URL="http://localhost:5000"
npm run dev

# macOS/Linux
VITE_API_BASE_URL=http://localhost:5000 npm run dev
```

Optional: Vite proxy (if you prefer) can be added to `vite.config.js` to proxy `/api` to `http://localhost:5000`.

---

## Build & Run (Production-ish)

Frontend build:
```bash
npm run build
npm run preview  # serves the static build at http://localhost:4173
```

Backend can be served behind a production WSGI server (e.g., `waitress` on Windows or `gunicorn` on Linux) after ensuring `ffmpeg` and `yt-dlp` are available. Example (Windows):
```bash
pip install waitress
python -m waitress --host=0.0.0.0 --port=5000 app:app
```

---

## Environment

Backend:
- `VIDSLICER_DB` — Optional path to SQLite file (default: `server/vidslicer.db`)

Frontend:
- `VITE_API_BASE_URL` — Base URL to Flask API (default: `http://localhost:5000`)

---

## API Reference

### POST `/api/inspect`
Request:
```json
{ "url": "https://www.youtube.com/watch?v=..." }
```
Response:
```json
{
  "metadata": {
    "id": "...",
    "title": "...",
    "uploader": "...",
    "duration": 123,
    "thumbnail": "...",
    "webpage_url": "...",
    "platform": "youtube"
  },
  "formats": [
    { "format_id": "...", "ext": "mp4", "height": 720, "fps": 30, "kind": "video" },
    { "format_id": "...", "ext": "m4a", "kind": "audio" }
  ],
  "cleanedUrl": "https://www.youtube.com/watch?v=..."
}
```

### POST `/api/download`
Request (video):
```json
{ "url": "...", "format_id": "22", "start": 0, "end": 60 }
```
Request (audio mp3):
```json
{ "url": "...", "audio_only": true, "start": 30, "end": 120 }
```
Response: streamed file (content-disposition: attachment)

### POST `/api/clip`
Request:
```json
{ "url": "...", "title": "...", "duration": 123, "start_time": 0, "end_time": 60 }
```
Response:
```json
{ "clip": { "id": 1, "thumbnail": "/api/thumbnail/1", "created_at": "..." } }
```

### GET `/api/clips`
Response:
```json
{ "clips": [ { "id": 1, "url": "...", "start_time": 0, "end_time": 60 } ] }
```

---

## Troubleshooting
- ffmpeg not found: Ensure `ffmpeg` is installed and in PATH (`ffmpeg -version`).
- Some links return errors: The source may block downloads; try with cookies or different network. `yt-dlp` can be configured with additional options if needed.
- Trimming issues on some formats: The app falls back to re-encoding if stream copy fails; this is slower but more reliable.
- CORS errors in dev: Verify backend at `http://localhost:5000` is running and `VITE_API_BASE_URL` is set correctly.

---

## Legal & Security Notes
- Downloading content may be subject to platform terms and local laws; use responsibly and obtain permission where required.
- Avoid running this server on the open internet without additional rate-limiting, auth, and request validation.

---

## Roadmap (stretch)
- Batch downloads
- Login + cloud synced history
- Billing for high-quality downloads
- Desktop build (Electron)
