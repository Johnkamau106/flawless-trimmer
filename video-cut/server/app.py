import os
import re
import tempfile
import uuid
import hashlib
import threading
from datetime import datetime
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode

from flask import Flask, jsonify, request, send_file, Response, stream_with_context
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy


DB_PATH = os.environ.get("VIDSLICER_DB", os.path.join(os.path.dirname(__file__), "vidslicer.db"))
CACHE_DIR = os.environ.get("VIDSLICER_CACHE_DIR", os.path.join(os.path.dirname(__file__), "cache"))
CACHE_ENABLED = os.environ.get("VIDSLICER_CACHE", "true").lower() == "true"
CACHE_LOCK = threading.Lock()

# Ensure cache directory exists
if CACHE_ENABLED:
    os.makedirs(CACHE_DIR, exist_ok=True)


def _cache_key(url: str, format_id: Optional[str], audio_only: bool, start: Optional[float], end: Optional[float]) -> str:
    """Generate a cache key from download parameters."""
    key_str = f"{url}|{format_id}|{audio_only}|{start}|{end}"
    return hashlib.sha256(key_str.encode()).hexdigest()


def _get_cached_path(cache_key: str, ext: str) -> Optional[str]:
    """Get cached file path if it exists."""
    if not CACHE_ENABLED:
        return None
    cached_file = os.path.join(CACHE_DIR, f"{cache_key}.{ext}")
    if os.path.exists(cached_file):
        return cached_file
    return None


def _save_to_cache(cache_key: str, source_path: str, ext: str) -> str:
    """Save file to cache and return cache path."""
    if not CACHE_ENABLED:
        return source_path
    try:
        cached_file = os.path.join(CACHE_DIR, f"{cache_key}.{ext}")
        # Copy file to cache (simple approach - could optimize with hardlinks)
        import shutil
        with CACHE_LOCK:
            if not os.path.exists(cached_file):
                shutil.copy2(source_path, cached_file)
        return cached_file
    except Exception:
        return source_path


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["JSON_SORT_KEYS"] = False

CORS(app)
db = SQLAlchemy(app)


class Clip(db.Model):
    __tablename__ = "clips"

    id = db.Column(db.Integer, primary_key=True)
    url = db.Column(db.Text, nullable=False)
    platform = db.Column(db.String(32), nullable=True)
    title = db.Column(db.Text, nullable=True)
    duration = db.Column(db.Float, nullable=True)
    start_time = db.Column(db.Float, nullable=True)
    end_time = db.Column(db.Float, nullable=True)
    thumbnail_path = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "platform": self.platform,
            "title": self.title,
            "duration": self.duration,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "thumbnail": f"/api/thumbnail/{self.id}" if self.thumbnail_path else None,
            "created_at": self.created_at.isoformat(),
        }


def ensure_db():
    with app.app_context():
        db.create_all()


YOUTUBE_HOSTS = {"www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"}


def clean_youtube_params(url: str) -> str:
    parsed = urlparse(url)
    if parsed.netloc not in YOUTUBE_HOSTS:
        return url
    query = dict(parse_qsl(parsed.query))
    # Remove YouTube tracking params like "si" often present in Shorts
    query.pop("si", None)
    new_query = urlencode(query, doseq=True)
    cleaned = parsed._replace(query=new_query)
    return urlunparse(cleaned)


def _safe_filename(title: str, ext: str) -> tuple:
    """Return (ascii_filename, rfc5987_filename) for use in Content-Disposition headers.

    ascii_filename is an ASCII-safe fallback. rfc5987_filename is the RFC5987 encoded
    UTF-8 value (e.g. "UTF-8''%E2%82%ACname.mp4").
    """
    import unicodedata
    from urllib.parse import quote

    # Normalize and attempt to produce a readable ASCII fallback
    nm = unicodedata.normalize("NFKD", (title or "video"))
    ascii = nm.encode("ascii", "ignore").decode("ascii")
    # Remove characters that are unsafe in filenames, replace spaces with underscores
    ascii = re.sub(r"[^\w\-\s]", "", ascii).strip().replace(" ", "_")
    if not ascii:
        ascii = "video"
    # Ensure ext has no leading dot
    ext = (ext or "mp4").lstrip(".")
    ascii_full = f"{ascii}.{ext}"

    # RFC5987 encode the UTF-8 filename
    utf8_quoted = quote((title or "video").encode("utf-8"))
    rfc5987 = f"UTF-8''{utf8_quoted}.{ext}"

    return ascii_full, rfc5987


def detect_platform(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if any(k in host for k in ["youtube.com", "youtu.be"]):
        return "youtube"
    if any(k in host for k in ["instagram.com"]):
        return "instagram"
    if any(k in host for k in ["facebook.com", "fb.watch"]):
        return "facebook"
    if any(k in host for k in ["tiktok.com"]):
        return "tiktok"
    if any(k in host for k in ["twitter.com", "x.com"]):
        return "twitter"
    return "unknown"


def require_yt_dlp():
    try:
        import yt_dlp  # noqa: F401
    except Exception as exc:
        return str(exc)
    return None


def _referer_for(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if any(k in host for k in ["tiktok.com"]):
            return "https://www.tiktok.com/"
        if any(k in host for k in ["instagram.com"]):
            return "https://www.instagram.com/"
        if any(k in host for k in ["facebook.com", "fb.watch"]):
            return "https://www.facebook.com/"
        if any(k in host for k in ["twitter.com", "x.com"]):
            return "https://twitter.com/"
    except Exception:
        pass
    return None


def _build_ydl_opts(base: Optional[dict] = None, for_url: Optional[str] = None) -> dict:
    is_youtube = for_url and detect_platform(for_url) == "youtube"
    
    # For YouTube, use fewer concurrent fragments to avoid rate limiting
    # For other platforms, use more for speed
    default_concurrent = 2 if is_youtube else int(os.environ.get("VIDSLICER_CONCURRENT_FRAGMENTS", "4"))
    concurrent_fragments = int(os.environ.get("VIDSLICER_CONCURRENT_FRAGMENTS", str(default_concurrent)))
    
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        # Optimize download speed with concurrent fragments
        "concurrent_fragment_downloads": concurrent_fragments,
        # Add retry logic for failed downloads
        "retries": 3,
        "fragment_retries": 3,
        "file_access_retries": 3,
        # Use a realistic desktop UA to reduce blocking
        "http_headers": {
            "User-Agent": os.environ.get(
                "VIDSLICER_UA",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
        },
    }
    
    # YouTube-specific options
    if is_youtube:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "web"],  # Try Android client first (less restrictions), fallback to web
            }
        }
        # For YouTube, prefer single-stream formats to reduce concurrent requests
        # This helps avoid rate limiting
    
    cookiefile = os.environ.get("VIDSLICER_COOKIES")
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
    
    # Use browser cookies automatically if available (prefer env, fallback to chrome)
    cookies_from_browser = os.environ.get("VIDSLICER_COOKIES_FROM_BROWSER")
    if not cookies_from_browser:
        # Auto-detect for most Linux/WSL; checks for '~/.config/google-chrome' data directory
        chrome_cookie_dir = os.path.expanduser("~/.config/google-chrome")
        if os.path.isdir(chrome_cookie_dir):
            cookies_from_browser = "chrome"
        # Also check for Firefox on WSL/Linux
        firefox_cookie_dir = os.path.expanduser("~/.mozilla/firefox")
        if not cookies_from_browser and os.path.isdir(firefox_cookie_dir):
            cookies_from_browser = "firefox"
    
    if cookies_from_browser:
        opts["cookiesfrombrowser"] = cookies_from_browser
    
    # Add site-specific Referer when helpful
    if for_url:
        ref = _referer_for(for_url)
        if ref:
            opts.setdefault("http_headers", {})["Referer"] = ref
    
    if base:
        opts.update(base)
    return opts


def list_formats(url: str):
    """Extract metadata and format list. Retry with alternate extractor args for YouTube if needed."""
    import yt_dlp

    last_exc = None
    info = None

    # Build a list of candidate ydl_opts to try - default first, then some YouTube-specific fallbacks
    try_opts = []
    try_opts.append(_build_ydl_opts({"skip_download": True}, for_url=url))

    if detect_platform(url) == "youtube":
        # Try alternative player_client orders (sometimes one works where another is blocked)
        try_opts.append(_build_ydl_opts({"skip_download": True, "extractor_args": {"youtube": {"player_client": ["web"]}}}, for_url=url))
        try_opts.append(_build_ydl_opts({"skip_download": True, "extractor_args": {"youtube": {"player_client": ["android"]}}}, for_url=url))
        try_opts.append(_build_ydl_opts({"skip_download": True, "extractor_args": {"youtube": {"player_client": ["android_webview"]}}}, for_url=url))
        try_opts.append(_build_ydl_opts({"skip_download": True, "extractor_args": {"youtube": {"player_client": ["tv_embedded"]}}}, for_url=url))
        try_opts.append(_build_ydl_opts({"skip_download": True, "extractor_args": {"youtube": {"player_client": ["mweb"]}}}, for_url=url))

    for ydl_opts in try_opts:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
            break
        except Exception as e:
            last_exc = e
            # try next opts
            continue

    if not info:
        # raise the last extraction error to be handled by caller
        raise last_exc or Exception("Failed to extract formats")

    formats = []
    best_playback = None

    def choose_playback(f):
        # Prioritize progressive mp4, then HLS, then DASH
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        has_video = vcodec != "none" and vcodec is not None
        has_audio = acodec != "none" and acodec is not None
        if not has_video:
            return None
        if has_audio and ext == "mp4" and proto in {"https", "http"}:
            return {"type": "mp4", "url": f.get("url")}
        if proto in {"m3u8", "m3u8_native", "hls"}:
            return {"type": "hls", "url": f.get("url")}
        if proto in {"dash", "http_dash_segments"} or ext == "mpd":
            return {"type": "dash", "url": f.get("url")}
        return None

    for f in info.get("formats", []):
        if f.get("vcodec") != "none" and f.get("acodec") == "none":
            kind = "video_only"
        elif f.get("vcodec") == "none" and f.get("acodec") != "none":
            kind = "audio"
        else:
            kind = "video"

        height = f.get("height")
        fmt_label = f.get("format_note") or (f"{height}p" if height else "unknown")
        formats.append({
            "format_id": f.get("format_id"),
            "ext": f.get("ext"),
            "fps": f.get("fps"),
            "filesize": f.get("filesize") or f.get("filesize_approx"),
            "height": height,
            "width": f.get("width"),
            "label": fmt_label,
            "kind": kind,
            "protocol": f.get("protocol"),
            "url": f.get("url"),
        })
        if not best_playback:
            cand = choose_playback(f)
            if cand:
                best_playback = cand

    # As a fallback, some extractors provide top-level url for streaming
    if not best_playback:
        top_url = info.get("url")
        if top_url:
            best_playback = {"type": "mp4", "url": top_url}

    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "platform": detect_platform(url),
        "playback": best_playback,
    }
    return meta, formats


def download_media(url: str, format_id: Optional[str], audio_only: bool, start: Optional[float], end: Optional[float]):
    import yt_dlp
    import subprocess
    import time

    temp_dir = tempfile.mkdtemp(prefix="vidslicer_")
    base_name = uuid.uuid4().hex
    download_path = os.path.join(temp_dir, base_name + ".%(ext)s")

    ydl_opts = _build_ydl_opts({
        "outtmpl": download_path,
    }, for_url=url)

    # Decide initial format choice
    if audio_only:
        ydl_opts.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        })
        preferred_format = ydl_opts["format"]
    else:
        if format_id:
            preferred_format = format_id
        else:
            # For all non-specific cases, use bestvideo+bestaudio which works reliably
            # The fallback chain below will handle edge cases
            preferred_format = "bestvideo+bestaudio/best"
            ydl_opts.update({"merge_output_format": "mp4"})
        ydl_opts["format"] = preferred_format

    # Retry download on failure with fallback handling
    max_retries = 3
    last_error = None
    downloaded = None
    info = None
    fallback_formats = []
    
    # Define fallback format chains - try progressively simpler options
    if not audio_only:
        fallback_formats = [
            "bestvideo+bestaudio/best",  # Primary: video + audio merge
            "best[ext=mp4]/best",         # Best mp4 file
            "best",                       # Absolute best available
        ]
    else:
        fallback_formats = [
            "bestaudio/best",
            "best",
        ]

    for attempt in range(max_retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # prepare_filename will return the file path based on info and outtmpl
                downloaded = ydl.prepare_filename(info)
                break  # Success
        except Exception as e:
            last_error = e
            error_str = str(e)

            # If the error is about a requested/selected format not being available, try next fallback
            if ("Requested format is not available" in error_str or "format not available" in error_str.lower() or "requested format is not available" in error_str.lower()):
                # Try next fallback format if available
                if fallback_formats:
                    next_format = fallback_formats.pop(0)
                    if next_format != ydl_opts.get("format"):
                        ydl_opts["format"] = next_format
                        time.sleep(0.5)
                        continue
                # If no more fallbacks, raise the error
                raise

            # Don't retry on private/sign-in errors
            if "Private video" in error_str or "Sign in" in error_str or "not available" in error_str.lower():
                raise

            # Retry on common rate-limit/403 errors with backoff
            if attempt < max_retries and ("403" in error_str or "Forbidden" in error_str or "rate limit" in error_str.lower()):
                wait_time = (attempt + 1) * 2
                time.sleep(wait_time)
                continue

            # Otherwise bubble up the error
            raise

    if last_error and not downloaded:
        raise last_error
    if not info:
        raise Exception("Failed to download video information")

    source_path = downloaded
    output_ext = "mp3" if audio_only else os.path.splitext(source_path)[1].lstrip(".")
    output_path = os.path.join(temp_dir, f"{base_name}.out.{output_ext}")

    # Trim if needed (same strategy: try stream-copy then re-encode)
    if start is not None and end is not None and end > start:
        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            source_path,
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            output_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                source_path,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                output_path,
            ]
            subprocess.check_call(cmd)
        final_path = output_path
    else:
        final_path = source_path

    return final_path, info


def generate_thumbnail(video_path: str, out_dir: str, t_seconds: float = 1.0) -> Optional[str]:
    import subprocess
    thumb_path = os.path.join(out_dir, uuid.uuid4().hex + ".jpg")
    try:
        subprocess.check_call([
            "ffmpeg",
            "-y",
            "-ss",
            str(t_seconds),
            "-i",
            video_path,
            "-frames:v",
            "1",
            thumb_path,
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return thumb_path
    except Exception:
        return None


@app.route("/api/inspect", methods=["POST"])
def api_inspect():
    err = require_yt_dlp()
    if err:
        return jsonify({"error": f"yt-dlp missing or broken: {err}"}), 500

    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    if not url:
        return jsonify({"error": "Missing url"}), 400

    url = clean_youtube_params(url)
    try:
        meta, formats = list_formats(url)
        return jsonify({"metadata": meta, "formats": formats, "cleanedUrl": url, "playback": meta.get("playback")})
    except Exception as exc:
        # Provide clearer message for common blocked cases
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        if plat == "youtube":
            if "signature" in error_str.lower() or "nsig" in error_str.lower() or "Precondition check failed" in error_str.lower():
                hint = " — YouTube signature extraction failed (Python 3.8 compatibility issue). SOLUTION: 1) Upgrade Python to 3.9+ (recommended), 2) Export browser cookies (VIDSLICER_COOKIES_FROM_BROWSER='firefox') as a temporary workaround, 3) Use VIDSLICER_UA with a Firefox User-Agent string."
            elif "403" in error_str or "Forbidden" in error_str or "HTTP Error 403" in error_str:
                hint = " — YouTube is blocking access. Try: 1) Export cookies from your browser and set VIDSLICER_COOKIES, 2) Update yt-dlp: pip install -U yt-dlp, 3) Wait a few minutes and try again."
            elif "Private video" in error_str or "Sign in" in error_str:
                hint = " — Video may be private or require sign-in. Export cookies from your browser and set VIDSLICER_COOKIES."
            elif "not available" in error_str.lower():
                hint = " — Video format not available. This usually means the video is geo-blocked, age-restricted, or has regional limitations. Try exporting cookies from a browser in the same region."
        elif plat in {"tiktok", "instagram", "facebook", "twitter"}:
            hint = " — site may require cookies/session. Set VIDSLICER_COOKIES or VIDSLICER_COOKIES_FROM_BROWSER."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/download", methods=["POST"])
def api_download():
    err = require_yt_dlp()
    if err:
        return jsonify({"error": f"yt-dlp missing or broken: {err}"}), 500

    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    format_id = body.get("format_id")
    audio_only = bool(body.get("audio_only"))
    start = body.get("start")
    end = body.get("end")

    if not url:
        return jsonify({"error": "Missing url"}), 400

    url = clean_youtube_params(url)
    
    # Check cache first
    cache_key = _cache_key(url, format_id, audio_only, start, end)
    output_ext = "mp3" if audio_only else "mp4"
    cached_path = _get_cached_path(cache_key, output_ext)
    
    try:
        if cached_path:
            # Serve from cache - need to get metadata for title
            try:
                import yt_dlp
                ydl_opts = _build_ydl_opts({"skip_download": True}, for_url=url)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                title = info.get("title") or "video"
            except Exception:
                title = "video"
            ascii_name, rfc5987_name = _safe_filename(title, output_ext)
            cd_header = f'attachment; filename="{ascii_name}"; filename*={rfc5987_name}'

            def generate():
                with open(cached_path, "rb") as f:
                    while True:
                        chunk = f.read(8192)  # 8KB chunks
                        if not chunk:
                            break
                        yield chunk

            response = Response(
                stream_with_context(generate()),
                mimetype="video/mp4" if not audio_only else "audio/mpeg",
                headers={
                    "Content-Disposition": cd_header,
                    "Content-Length": str(os.path.getsize(cached_path)),
                }
            )
            return response
        
        # Download and process
        file_path, info = download_media(url, format_id, audio_only, start, end)
        
        # Save to cache
        file_path = _save_to_cache(cache_key, file_path, output_ext)
        
        title = info.get("title") or "video"
        ext = os.path.splitext(file_path)[1]
        ascii_name, rfc5987_name = _safe_filename(title, ext.lstrip("."))
        cd_header = f'attachment; filename="{ascii_name}"; filename*={rfc5987_name}'

        # Stream file in chunks for better performance
        def generate():
            with open(file_path, "rb") as f:
                while True:
                    chunk = f.read(8192)  # 8KB chunks
                    if not chunk:
                        break
                    yield chunk

        response = Response(
            stream_with_context(generate()),
            mimetype="video/mp4" if not audio_only else "audio/mpeg",
            headers={
                "Content-Disposition": cd_header,
                "Content-Length": str(os.path.getsize(file_path)),
            }
        )
        return response
        
    except Exception as exc:
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        # Check for common YouTube blocking errors
        if plat == "youtube":
            if "403" in error_str or "Forbidden" in error_str or "HTTP Error 403" in error_str:
                hint = " — YouTube is blocking the download. Try: 1) Export cookies from your browser and set VIDSLICER_COOKIES, 2) Update yt-dlp: pip install -U yt-dlp, 3) Wait a few minutes and try again."
            elif "Private video" in error_str or "Sign in" in error_str:
                hint = " — Video may be private or require sign-in. Export cookies from your browser and set VIDSLICER_COOKIES."
        elif plat in {"tiktok", "instagram", "facebook", "twitter"}:
            hint = " — site may require cookies/session. Set VIDSLICER_COOKIES or VIDSLICER_COOKIES_FROM_BROWSER."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/clip", methods=["POST"])
def api_clip_save():
    body = request.get_json(silent=True) or {}
    # Simulate a fake clip ID (e.g., 1) and use the incoming data in the response.
    resp_clip = {
        "id": 1,
        "url": body.get("url"),
        "platform": detect_platform(body.get("url") or ""),
        "title": body.get("title"),
        "duration": body.get("duration"),
        "start_time": body.get("start_time"),
        "end_time": body.get("end_time"),
        "thumbnail": None,
        "created_at": datetime.utcnow().isoformat(),
    }
    return jsonify({"clip": resp_clip})


@app.route("/api/clips", methods=["GET"])
def api_clips_list():
    # Always return an empty list to simulate zero saved clips.
    return jsonify({"clips": []})


@app.route("/api/thumbnail/<int:clip_id>")
def api_thumbnail(clip_id: int):
    clip = Clip.query.get_or_404(clip_id)
    if not clip.thumbnail_path or not os.path.exists(clip.thumbnail_path):
        return jsonify({"error": "No thumbnail"}), 404
    return send_file(clip.thumbnail_path)


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


