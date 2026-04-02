import os
import re
import tempfile
import uuid
import hashlib
import threading
import requests
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode

from flask import Flask, jsonify, request, send_file, Response, stream_with_context
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy

# Load environment variables from .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass  # python-dotenv not installed, skip


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

# ========== TIMEOUT CONFIGURATION FOR LONG VIDEOS ==========
# Set to 0 to disable timeout (allow infinite transfer time)
# Videos can be 1+ hours, so we need very long timeout
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0  # Disable browser caching
# For Gunicorn/WSGI servers, set via environment or use send_file with timeout parameter
# For development (Flask dev server), set this in environment or use timeout param in send operations
BASE_DOWNLOAD_TIMEOUT = int(os.environ.get("VIDSLICER_DOWNLOAD_TIMEOUT", "300"))  # 5 minutes base, will auto-scale
DYNAMIC_TIMEOUT = BASE_DOWNLOAD_TIMEOUT  # Will be adjusted per download based on video duration

def calculate_timeout_for_duration(duration_seconds):
    """
    Calculate appropriate timeout based on video duration.
    Ensures downloads never timeout regardless of video length.
    Formula: (duration * 1.5) + 120 = generous buffer for slow networks
    """
    if not duration_seconds or duration_seconds <= 0:
        # Unknown duration - use a safe 2-hour default
        return 7200
    # 1.5x multiplier gives buffer for slow networks, +2min buffer
    calculated = int((duration_seconds * 1.5) + 120)
    # Ensure minimum of 5 minutes
    return max(calculated, 300)

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
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

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
    platform = detect_platform(for_url) if for_url else "unknown"
    
    # For YouTube, use fewer concurrent fragments to avoid rate limiting
    # For others, use very aggressive concurrency for maximum speed
    if is_youtube:
        default_concurrent = 2
    elif platform in {"tiktok", "instagram", "x", "twitter"}:
        default_concurrent = 32  # VERY aggressive for social media (was 16)
    else:
        default_concurrent = 24  # Increased from 12
    concurrent_fragments = int(os.environ.get("VIDSLICER_CONCURRENT_FRAGMENTS", str(default_concurrent)))
    
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        # Speed up downloads with very aggressive concurrency
        "concurrent_fragment_downloads": concurrent_fragments,
        # Increase fragment pool for parallel downloading
        "fragment_pool_size": 64 if not is_youtube else 8,
        # Connection optimization for speed - increased timeout for stability
        "socket_timeout": 60,
        # Aggressive: minimal retries (0 for non-YouTube)
        "retries": 0 if not is_youtube else 1,
        "fragment_retries": 0 if not is_youtube else 1,
        "file_access_retries": 0,
        # Reduce connection pool wait time
        "tcp_nodelay": True,
        # Use a realistic desktop UA to reduce blocking
        "http_headers": {
            "User-Agent": os.environ.get(
                "VIDSLICER_UA",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
            "Connection": "keep-alive",
        },
    }
    
    # YouTube-specific options
    if is_youtube:
        opts["extractor_args"] = {
            "youtube": {
                "player_client": ["android", "web"],
            }
        }
    # TikTok/Instagram specific - need browser cookies for better format access
    elif platform in {"tiktok", "instagram"}:
        opts["extractor_args"] = {
            platform: {
                "api_hostname": "api.tiktok.com",  # Use API for faster access
            }
        }
    
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
        # Prioritize progressive mp4, then HLS, then DASH - works for all platforms
        # IMPORTANT: Be more flexible for platforms like TikTok/X to allow video-only or audio-only
        proto = (f.get("protocol") or "").lower()
        ext = (f.get("ext") or "").lower()
        vcodec = f.get("vcodec")
        acodec = f.get("acodec")
        has_video = vcodec != "none" and vcodec is not None
        has_audio = acodec != "none" and acodec is not None
        if not has_video:
            return None
        # Try progressive MP4 with audio first (YouTube, TikTok, etc.)
        if has_audio and ext == "mp4" and proto in {"https", "http"}:
            return {"type": "mp4", "url": f.get("url")}
        # HLS streams work well for most platforms (Instagram, TikTok, etc.)
        # HLS is often faster even than MP4 for non-YouTube platforms
        if proto in {"m3u8", "m3u8_native", "hls"}:
            return {"type": "hls", "url": f.get("url")}
        # DASH for high-quality videos
        if proto in {"dash", "http_dash_segments"} or ext == "mpd":
            return {"type": "dash", "url": f.get("url")}
        # Progressive MP4 even without audio (TikTok, X videos often have video-only)
        if ext == "mp4" and proto in {"https", "http"}:
            return {"type": "mp4", "url": f.get("url")}
        # Fallback: any progressive format with http/https
        if ext in {"mp4", "mkv", "webm"} and proto in {"https", "http"}:
            return {"type": "mp4", "url": f.get("url")}
        # Last resort: accept video-only formats if nothing else works
        if has_video and proto in {"https", "http"}:
            return {"type": "mp4", "url": f.get("url")}
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

    # For TikTok/Twitter: prioritize HLS if available (more stable), otherwise add webpage fallback
    platform = detect_platform(url)
    webpage_url = info.get("webpage_url") or url
    
    if platform in {"tiktok", "x", "twitter"}:
        # HLS streams are more reliable for these platforms
        hls_playback = next(({"type": "hls", "url": f.get("url")} 
                           for f in info.get("formats", []) 
                           if (f.get("protocol") or "").lower() in {"m3u8", "m3u8_native", "hls"}), 
                          None)
        if hls_playback:
            best_playback = hls_playback
        elif not best_playback:
            # For TikTok, create embed URL; for Twitter use webpage URL
            if platform == "tiktok":
                video_id = info.get("id")
                if video_id:
                    # TikTok embed URL format
                    embed_url = f"https://www.tiktok.com/embed/v2/{video_id}"
                    best_playback = {"type": "webpage", "url": embed_url}
                else:
                    best_playback = {"type": "webpage", "url": webpage_url}
            else:
                # Twitter/X can use webpage URL directly
                best_playback = {"type": "webpage", "url": webpage_url}

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
    # Reduced retries for faster downloads on non-YouTube platforms
    max_retries = 1 if detect_platform(url) != "youtube" else 2
    last_error = None
    downloaded = None
    info = None
    fallback_formats = []
    
    # Define fallback format chains - optimized for speed per platform
    if not audio_only:
        platform = detect_platform(url)
        if platform == "youtube":
            fallback_formats = [
                "best[ext=mp4]/best",         # Fast: single-stream mp4
                "bestvideo+bestaudio/best",  # Merge if needed
                "best",                       # Fallback
            ]
        elif platform in {"tiktok", "instagram", "facebook"}:
            # These platforms: prioritize playable MP4 formats for speed
            # For TikTok: explicitly avoid formats with format_id that may contain ads/problematic codecs
            fallback_formats = [
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",  # Merge video+audio for TikTok (better quality)
                "best[ext=mp4]",              # FASTEST: best MP4 available (no merge)
                "best[height>=480]/best",    # Good quality MP4
                "best[height>=360]/best",    # Lower quality but plays
                "best",                       # Whatever is available
            ]
        elif platform in {"x", "twitter"}:
            # Twitter/X: similar to TikTok, prefer MP4
            fallback_formats = [
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",  # Merge for better compatibility
                "best[ext=mp4]",              # Best MP4 format
                "best[height>=480]/best",
                "best",
            ]
        else:
            fallback_formats = [
                "best[ext=mp4]/best",
                "best[height>=720]/best",
                "best",
            ]
    else:
        fallback_formats = [
            "bestaudio/best",
        ]

    for attempt in range(max_retries + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                # Don't rely on prepare_filename with postprocessors - find the actual downloaded file
                # List temp directory and find the downloaded file (should be base_name.*)
                actual_files = [f for f in os.listdir(temp_dir) if f.startswith(base_name)]
                if not actual_files:
                    raise Exception(f"No downloaded file found in {temp_dir} (base: {base_name})")
                # Use the first file found (should only be one primary file)
                downloaded = os.path.join(temp_dir, actual_files[0])
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
                        # Ensure merge_output_format is set for merges
                        if "bestvideo" in next_format or "video" in next_format.lower():
                            ydl_opts["merge_output_format"] = "mp4"
                        time.sleep(0.5)
                        continue
                # If no more fallbacks, raise the error
                raise

            # Don't retry on private/sign-in errors
            if "Private video" in error_str or "Sign in" in error_str or "not available" in error_str.lower():
                raise

            # Retry on rate-limit errors only (skip for most platforms to speed up)
            if attempt < max_retries and ("403" in error_str or "Forbidden" in error_str or "rate limit" in error_str.lower()):
                if detect_platform(url) == "youtube":
                    time.sleep((attempt + 1) * 0.5)
                    continue
                # For other platforms, don't retry on 403 - it's often permanent

            # Otherwise bubble up the error
            raise

    if last_error and not downloaded:
        raise last_error
    if not info:
        raise Exception("Failed to download video information")

    source_path = downloaded
    # Always use standard containers: mp3 for audio, mp4 for video
    # (don't use the downloaded file's extension, which may be unexpected like .mhtml)
    output_ext = "mp3" if audio_only else "mp4"
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
            # Re-encode on failure - use audio-only codec for audio, video+audio for video
            if audio_only:
                # Audio-only: encode to mp3 with libmp3lame
                cmd = [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(start),
                    "-to",
                    str(end),
                    "-i",
                    source_path,
                    "-c:a",
                    "libmp3lame",
                    "-b:a",
                    "192k",
                    "-q:a",
                    "4",
                    output_path,
                ]
            else:
                # Video: encode with both video and audio codecs
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
        # If no trimming needed, still need to ensure proper format
        # If source has unexpected extension, convert to mp4
        source_ext = os.path.splitext(source_path)[1].lstrip(".")
        if source_ext.lower() not in {"mp4", "mkv", "webm", "mov", "flv", "avi", "m4a", "m4v", "3gp", "opus"}:
            # Unexpected extension (e.g., .mhtml) - convert to mp4
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                source_path,
                "-c:v",
                "copy",
                "-c:a",
                "copy",
                output_path,
            ]
            try:
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                final_path = output_path
            except Exception:
                # If copy fails, re-encode - use audio-only codec for audio, video+audio for video
                if audio_only:
                    # Audio-only: encode to mp3 with libmp3lame
                    cmd = [
                        "ffmpeg",
                        "-y",
                        "-i",
                        source_path,
                        "-c:a",
                        "libmp3lame",
                        "-b:a",
                        "192k",
                        "-q:a",
                        "4",
                        output_path,
                    ]
                else:
                    # Video: encode with both video and audio codecs
                    cmd = [
                        "ffmpeg",
                        "-y",
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
                hint = " — YouTube signature extraction failed. Solution: Use Python 3.9+ or export browser cookies."
            elif "403" in error_str or "Forbidden" in error_str or "HTTP Error 403" in error_str:
                hint = " — YouTube is blocking access. Try: 1) Export cookies from your browser, 2) Update yt-dlp, 3) Try again after a few minutes."
            elif "Private video" in error_str or "Sign in" in error_str:
                hint = " — Video may be private or require sign-in."
            elif "not available" in error_str.lower():
                hint = " — Video format not available (geo-blocked or age-restricted)."
        elif plat == "tiktok":
            hint = " — TikTok requires authentication. Either: 1) Export cookies from TikTok in your browser (VIDSLICER_COOKIES_FROM_BROWSER='firefox'), 2) Some TikTok videos may not be downloadable due to platform restrictions."
        elif plat == "instagram":
            hint = " — Instagram requires authentication. Export cookies from Instagram (VIDSLICER_COOKIES_FROM_BROWSER='firefox') for access."
        elif plat in {"x", "twitter", "facebook"}:
            hint = " — Platform may require authentication. Export browser cookies for better access."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/get-download-url", methods=["POST"])
def api_get_download_url():
    """Get direct download URL from video source - browser downloads directly, not through our server."""
    err = require_yt_dlp()
    if err:
        return jsonify({"error": f"yt-dlp missing or broken: {err}"}), 500

    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    format_id = body.get("format_id")
    audio_only = bool(body.get("audio_only"))

    if not url:
        return jsonify({"error": "Missing url"}), 400

    url = clean_youtube_params(url)
    
    try:
        # Get video info
        import yt_dlp
        ydl_opts = _build_ydl_opts({"skip_download": True}, for_url=url)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        
        # Get the format we want
        if audio_only:
            # Find best audio format
            best_audio = None
            for f in info.get("formats", []):
                if f.get("acodec") != "none" and f.get("vcodec") == "none":
                    if not best_audio or f.get("abr", 0) > best_audio.get("abr", 0):
                        best_audio = f
            if not best_audio:
                raise Exception("No audio format available")
            format_url = best_audio.get("url")
            filename = f"{(info.get('title') or 'audio')}.m4a"
        else:
            # Find specific format or best video
            target_format = None
            if format_id:
                for f in info.get("formats", []):
                    if f.get("format_id") == format_id:
                        target_format = f
                        break
            else:
                # Default to best video+audio combined
                for f in info.get("formats", []):
                    if f.get("vcodec") != "none" and f.get("acodec") != "none":
                        if not target_format or f.get("height", 0) > target_format.get("height", 0):
                            target_format = f
            
            if not target_format:
                raise Exception("Format not available")
            
            format_url = target_format.get("url")
            filename = f"{(info.get('title') or 'video')}.mp4"
        
        if not format_url:
            raise Exception("Could not get download URL from video source")
        
        # Return the direct URL
        return jsonify({
            "download_url": format_url,
            "filename": filename,
            "title": info.get("title")
        })
        
    except Exception as exc:
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        if plat == "youtube":
            if "signature" in error_str.lower() or "nsig" in error_str.lower():
                hint = " — YouTube signature extraction failed. Try exporting cookies from your browser."
            elif "403" in error_str or "Forbidden" in error_str:
                hint = " — YouTube is blocking access. Try exporting cookies or retry later."
        elif plat in {"tiktok", "instagram"}:
            hint = " — Platform requires authentication. Export browser cookies for access."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/download", methods=["POST"])
def api_download():
    """Download video with proper format merging support for all platforms."""
    err = require_yt_dlp()
    if err:
        return jsonify({"error": f"yt-dlp missing or broken: {err}"}), 500

    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    format_id = body.get("format_id", "").strip() or None
    audio_only = bool(body.get("audio_only"))

    if not url:
        return jsonify({"error": "Missing url"}), 400

    url = clean_youtube_params(url)
    
    try:
        # Use download_media which handles all format merging and fallback logic
        # This supports platforms like Reddit that have separate video/audio streams
        downloaded_file, info = download_media(url, format_id, audio_only, None, None)
        
        if not downloaded_file or not os.path.exists(downloaded_file):
            raise Exception("Failed to download media file")
        
        # Get filename from the downloaded file
        filename = os.path.basename(downloaded_file)
        ext = os.path.splitext(filename)[1].lstrip('.')
        
        # Get video title for better filename
        title = info.get("title") or "video" if info else "video"
        
        # Generate filename with proper encoding
        ascii_name, rfc5987_name = _safe_filename(title, ext)
        cd_header = f'attachment; filename="{ascii_name}"; filename*={rfc5987_name}'
        
        # Calculate timeout based on file size
        file_size = os.path.getsize(downloaded_file)
        # Estimate: 2 MiB/s minimum, so timeout = (size_bytes / 2MB) + 30s buffer
        timeout = max(int((file_size / (2 * 1024 * 1024)) + 30), 30)
        
        def cleanup_and_serve():
            """Serve file and clean up temp directory."""
            try:
                with open(downloaded_file, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            finally:
                # Clean up temp directory after serving
                import shutil
                temp_dir = os.path.dirname(downloaded_file)
                try:
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except:
                    pass
        
        response = Response(
            stream_with_context(cleanup_and_serve()),
            mimetype="video/mp4" if not audio_only else "audio/mp4",
            headers={
                "Content-Disposition": cd_header,
                "Cache-Control": "no-cache, no-store, must-revalidate",
            }
        )
        response.timeout = timeout
        return response
        
    except Exception as exc:
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        if plat == "youtube":
            if "403" in error_str or "Forbidden" in error_str:
                hint = " — YouTube is blocking. Try exporting cookies from your browser."
            elif "Private video" in error_str or "Sign in" in error_str:
                hint = " — Video is private or requires sign-in."
        elif plat == "reddit":
            hint = " — Reddit video format not supported by yt-dlp. Try a different video or check Reddit upload settings."
        elif plat in {"tiktok", "instagram"}:
            hint = " — Platform requires authentication. Export browser cookies."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/clip", methods=["POST"])
def api_clip_save():
    """Download, trim, and save video clip. Returns the trimmed video file."""
    err = require_yt_dlp()
    if err:
        return jsonify({"error": f"yt-dlp missing or broken: {err}"}), 500
    
    body = request.get_json(silent=True) or {}
    url = body.get("url", "").strip()
    format_id = body.get("format_id", "").strip() or None
    audio_only = bool(body.get("audio_only"))
    start_time = body.get("start_time")
    end_time = body.get("end_time")
    title = body.get("title")
    
    if not url:
        return jsonify({"error": "Missing url"}), 400
    
    url = clean_youtube_params(url)
    
    try:
        # Download and trim the video
        if start_time is not None and end_time is not None and end_time > start_time:
            final_path, info = download_media(url, format_id, audio_only, start_time, end_time)
        else:
            # Download without trimming (full video)
            final_path, info = download_media(url, format_id, audio_only, None, None)
        
        filename = title or info.get("title") or "clip"
        ext = "mp3" if audio_only else "mp4"
        filename = f"{filename}.{ext}"
        
        # Read file and send it
        def generate():
            chunk_size = 8192
            try:
                with open(final_path, 'rb') as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        yield chunk
            finally:
                # Cleanup temp files
                try:
                    import shutil
                    temp_dir = os.path.dirname(final_path)
                    shutil.rmtree(temp_dir, ignore_errors=True)
                except Exception:
                    pass
        
        ascii_name, rfc5987_name = _safe_filename(filename.replace(".mp4", "").replace(".m4a", ""), 
                                                    ext)
        cd_header = f'attachment; filename="{ascii_name}"; filename*={rfc5987_name}'
        
        response = Response(
            stream_with_context(generate()),
            mimetype="video/mp4" if not audio_only else "audio/mp4",
            headers={
                "Content-Disposition": cd_header,
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Connection": "keep-alive",
            }
        )
        response.timeout = None
        return response
        
    except Exception as exc:
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        if plat == "youtube":
            if "403" in error_str or "Forbidden" in error_str:
                hint = " — YouTube is blocking the download. Try exporting cookies from your browser."
            elif "Private video" in error_str or "Sign in" in error_str:
                hint = " — Video is private or requires sign-in."
        elif plat in {"tiktok", "instagram"}:
            hint = " — Platform requires authentication. Export cookies from your browser."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


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


