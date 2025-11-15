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
- `VIDSLICER_COOKIES` — Path to cookies.txt file for authenticated downloads (e.g., `/path/to/cookies.txt`)
- `VIDSLICER_COOKIES_FROM_BROWSER` — Auto-use browser cookies: `chrome` or `firefox` (auto-detected on Linux/WSL if Chrome is found)
- `VIDSLICER_UA` — Custom User-Agent string (optional)
- `VIDSLICER_CACHE` — Enable file caching for faster repeated downloads (default: `true`, set to `false` to disable)
- `VIDSLICER_CACHE_DIR` — Cache directory path (default: `server/cache`)
- `VIDSLICER_CONCURRENT_FRAGMENTS` — Number of concurrent fragments to download (default: `4`, increase for faster downloads on good connections)
- `PORT` — Flask server port (default: `5000`)

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

## Performance Optimizations

The app includes several optimizations for faster downloads:

1. **Concurrent Fragment Downloads**: Downloads multiple video fragments in parallel (configurable via `VIDSLICER_CONCURRENT_FRAGMENTS`)
   - **YouTube**: Default 2 fragments to avoid rate limiting
   - **Other platforms**: Default 4 fragments (can be increased)
2. **Chunked Streaming**: Files are streamed in 8KB chunks instead of loading entirely into memory
3. **File Caching**: Repeated downloads of the same video/trim are served from cache instantly (can be disabled with `VIDSLICER_CACHE=false`)
4. **Fast FFmpeg Encoding**: Uses `veryfast` preset for re-encoding when needed, with `faststart` flag for streaming optimization
5. **Progress Tracking**: Real-time download progress with percentage and MB transferred
6. **Automatic Retry Logic**: Retries failed downloads up to 3 times with exponential backoff (2s, 4s delays)

**YouTube-Specific Optimizations**:
- Uses single-stream format (`best`) instead of merged video+audio to reduce concurrent requests
- Reduced concurrent fragments (2 instead of 4) to avoid rate limiting
- Built-in retry logic for 403 errors

**Tip**: For faster downloads on high-speed connections (non-YouTube), increase concurrent fragments:
```bash
export VIDSLICER_CONCURRENT_FRAGMENTS=8  # Only affects non-YouTube platforms
```

---

## Troubleshooting

### YouTube 403 Forbidden Errors
If you encounter "HTTP Error 403: Forbidden" when downloading from YouTube, try these solutions:

1. **Update yt-dlp** (most common fix):
   ```bash
   pip install -U yt-dlp
   ```

2. **Use browser cookies** (recommended for authenticated content):
   - The app automatically tries to use cookies from Chrome/Firefox if available
   - For manual cookie export:
     - Install a browser extension like "Get cookies.txt" (Chrome/Edge) or "cookies.txt" (Firefox)
     - Export cookies for `youtube.com` to a file (e.g., `cookies.txt`)
     - Set environment variable: `export VIDSLICER_COOKIES=/path/to/cookies.txt` (Linux/WSL) or `set VIDSLICER_COOKIES=C:\path\to\cookies.txt` (Windows CMD)
   - Or use browser cookies automatically:
     ```bash
     export VIDSLICER_COOKIES_FROM_BROWSER=chrome  # or firefox
     ```

3. **Wait and retry**: The app automatically retries 403 errors with delays (2s, 4s). If it still fails:
   - Wait 5-10 minutes before trying again
   - YouTube may be temporarily rate-limiting your IP
   - Try using a VPN or different network

4. **Reduce concurrent requests** (if still having issues):
   ```bash
   export VIDSLICER_CONCURRENT_FRAGMENTS=1  # Even more conservative for YouTube
   ```

### Other Issues
- **ffmpeg not found**: Ensure `ffmpeg` is installed and in PATH (`ffmpeg -version`).
- **Some links return errors**: The source may block downloads; try with cookies or different network. `yt-dlp` can be configured with additional options if needed.
- **Trimming issues on some formats**: The app falls back to re-encoding if stream copy fails; this is slower but more reliable.
- **CORS errors in dev**: Verify backend at `http://localhost:5000` is running and `VITE_API_BASE_URL` is set correctly.

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
