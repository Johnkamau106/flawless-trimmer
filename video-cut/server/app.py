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
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode, urljoin

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


app = Flask(
    __name__,
    static_folder=os.path.join(os.path.dirname(__file__), "..", "dist", "assets"),
    static_url_path="/assets"
)
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
    
    # For Twitter/X, use aggressive concurrency since fragments are the only option
    # For YouTube, use fewer concurrent fragments to avoid rate limiting
    # For others, use reasonable concurrency
    if is_youtube:
        default_concurrent = 2
    elif platform in {"x", "twitter"}:
        default_concurrent = 16  # Aggressive for Twitter fragments (no rate limit concerns for fragments)
    elif platform in {"tiktok", "instagram"}:
        default_concurrent = 8
    else:
        default_concurrent = 6
    concurrent_fragments = int(os.environ.get("VIDSLICER_CONCURRENT_FRAGMENTS", str(default_concurrent)))
    
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        # Aggressive concurrency for fragments - Twitter uses HLS which is fragment-based
        "concurrent_fragment_downloads": concurrent_fragments,
        # Large pool for many concurrent fragments
        "fragment_pool_size": 64,
        # HTTP connection pool settings for faster downloads
        "http_chunk_size": 1048576,  # 1MB chunks for HTTP requests
        # Reasonable timeout
        "socket_timeout": 45,
        # Minimal retries - fragments are reliable once stream is obtained
        "retries": 2,
        "fragment_retries": 2,
        "file_access_retries": 1,
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
    # Twitter/X specific options
    elif platform in {"x", "twitter"}:
        opts["http_headers"]["Referer"] = "https://twitter.com/"
        opts["http_headers"]["Origin"] = "https://twitter.com"
    # TikTok/Instagram specific
    elif platform in {"tiktok", "instagram"}:
        opts["extractor_args"] = {
            platform: {
                "api_hostname": "api.tiktok.com",
            }
        }
    
    cookiefile = os.environ.get("VIDSLICER_COOKIES")
    if cookiefile and os.path.exists(cookiefile):
        opts["cookiefile"] = cookiefile
    
    # Use browser cookies automatically if available
    cookies_from_browser = os.environ.get("VIDSLICER_COOKIES_FROM_BROWSER")
    if not cookies_from_browser:
        import platform as py_platform
        is_windows = py_platform.system() == "Windows"
        
        if is_windows:
            chrome_cookie_dir = os.path.expanduser("~\\AppData\\Local\\Google\\Chrome\\User Data")
            firefox_cookie_dir = os.path.expanduser("~\\.mozilla\\firefox")
            
            if os.path.isdir(chrome_cookie_dir):
                cookies_from_browser = "chrome"
            elif os.path.isdir(firefox_cookie_dir):
                cookies_from_browser = "firefox"
        else:
            chrome_cookie_dir = os.path.expanduser("~/.config/google-chrome")
            chromium_cookie_dir = os.path.expanduser("~/.config/chromium")
            firefox_cookie_dir = os.path.expanduser("~/.mozilla/firefox")
            
            if os.path.isdir(chrome_cookie_dir):
                cookies_from_browser = "chrome"
            elif os.path.isdir(chromium_cookie_dir):
                cookies_from_browser = "chromium"
            elif os.path.isdir(firefox_cookie_dir):
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
        url = f.get("url", "")
        
        if not has_video:
            return None
        
        # Ensure URL is absolute (handle relative paths from Twitter/other platforms)
        if url and url.startswith("/"):
            if "ext_tw_video" in url or "pbs.twimg" in url:
                url = f"https://video.twimg.com{url}" if url.startswith("/ext_tw_video") else f"https://pbs.twimg.com{url}"
            else:
                # Unknown relative path - skip
                return None
        
        # Try progressive MP4 with audio first (YouTube, TikTok, etc.)
        if has_audio and ext == "mp4" and proto in {"https", "http"}:
            return {"type": "mp4", "url": url}
        # HLS streams work well for most platforms (Instagram, TikTok, etc.)
        # HLS is often faster even than MP4 for non-YouTube platforms
        if proto in {"m3u8", "m3u8_native", "hls"}:
            return {"type": "hls", "url": url}
        # DASH for high-quality videos
        if proto in {"dash", "http_dash_segments"} or ext == "mpd":
            return {"type": "dash", "url": url}
        # Progressive MP4 even without audio (TikTok, X videos often have video-only)
        if ext == "mp4" and proto in {"https", "http"}:
            return {"type": "mp4", "url": url}
        # Fallback: any progressive format with http/https
        if ext in {"mp4", "mkv", "webm"} and proto in {"https", "http"}:
            return {"type": "mp4", "url": url}
        # Last resort: accept video-only formats if nothing else works
        if has_video and proto in {"https", "http"}:
            return {"type": "mp4", "url": url}
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

    # For TikTok: use embed format if available
    # For Twitter/X: use direct video URLs (yt-dlp extracts working ones)
    platform = detect_platform(url)
    webpage_url = info.get("webpage_url") or url
    
    # Twitter/X: use the extracted video URL directly (yt-dlp finds working URLs)
    # This avoids the broken Twitter embed widget and plays the video directly
    if platform in {"x", "twitter"}:
        # If we found a working video format, use it directly
        # yt-dlp extracts HLS or MP4 URLs that bypass the widget requirement
        if not best_playback:
            best_playback = {"type": "webpage", "url": webpage_url}
        # else: use the best_playback found from format selection
    elif platform == "tiktok":
        # TikTok: prefer embed format, fallback to webpage
        video_id = info.get("id")
        if video_id:
            # TikTok embed URL format
            embed_url = f"https://www.tiktok.com/embed/v2/{video_id}"
            best_playback = {"type": "webpage", "url": embed_url}
        elif not best_playback:
            best_playback = {"type": "webpage", "url": webpage_url}

    # Wrap video URLs with proxy endpoint for platforms that block direct access
    # (Twitter, Instagram, etc.)
    if best_playback and best_playback.get("type") in {"hls", "mp4", "dash"}:
        video_url = best_playback.get("url", "")
        # Check if URL needs proxying (Twitter, Instagram, etc.) or is malformed (missing domain)
        needs_proxy = False
        
        # Check if it's a known CDN that needs proxying
        if any(host in video_url.lower() for host in ["twimg.com", "pbs.twimg.com", "video.twimg.com", "instagram.com", "scontent-", "fbcdn.net"]):
            needs_proxy = True
        # Check if URL is missing scheme/domain (e.g., starts with /ext_tw_video)
        elif video_url.startswith("/") and not video_url.startswith("//"):
            # Relative path - likely a Twitter video URL missing the domain
            # Reconstruct it as a full Twitter URL
            if "ext_tw_video" in video_url:
                video_url = f"https://video.twimg.com{video_url}"
                needs_proxy = True
        
        if needs_proxy:
            import base64
            url_encoded = base64.b64encode(video_url.encode()).decode("utf-8")
            proxy_url = f"/api/stream?url={url_encoded}&type={best_playback.get('type')}"
            best_playback = best_playback.copy()
            best_playback["url"] = proxy_url

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
    # More retries for Twitter/X to handle network issues
    max_retries = 3 if detect_platform(url) in {"x", "twitter"} else 1
    last_error = None
    downloaded = None
    info = None
    fallback_formats = []
    
    # Define fallback format chains - optimized for speed per platform
    if not audio_only:
        platform = detect_platform(url)
        if platform == "youtube":
            fallback_formats = [
                "best[ext=mp4]/best",
                "bestvideo+bestaudio/best",
                "best",
            ]
        elif platform in {"x", "twitter"}:
            # Twitter/X: Use best available - typically HLS fragments
            # Don't try to avoid fragments - they're the only option, but we can optimize download
            fallback_formats = [
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",  # Prefer merged video+audio
                "bestvideo+bestaudio/best",  # Let ffmpeg merge any format
                "best",                       # Fallback to whatever works
            ]
        elif platform in {"tiktok", "instagram", "facebook"}:
            fallback_formats = [
                "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
                "best[ext=mp4]",
                "best[height>=480]/best",
                "best[height>=360]/best",
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

            # Retry on network/rate-limit errors with exponential backoff
            if attempt < max_retries:
                is_network_error = "Network is unreachable" in error_str or "timeout" in error_str.lower() or "timed out" in error_str.lower()
                is_rate_limit = "403" in error_str or "Forbidden" in error_str or "rate limit" in error_str.lower()
                
                if is_network_error or is_rate_limit:
                    # For Twitter, use exponential backoff: 2s, 4s, 8s
                    if detect_platform(url) in {"x", "twitter"}:
                        wait_time = 2 ** (attempt + 1)
                        time.sleep(wait_time)
                        continue
                    elif detect_platform(url) == "youtube" and is_rate_limit:
                        time.sleep((attempt + 1) * 0.5)
                        continue

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
            # Use ultrafast preset for Twitter/X to speed up encoding
            is_twitter = detect_platform(url) in {"x", "twitter"}
            preset = "ultrafast" if is_twitter else "veryfast"
            crf = "28" if is_twitter else "23"  # Lower quality for speed on Twitter
            
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
                    preset,
                    "-crf",
                    crf,
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
                is_twitter = detect_platform(url) in {"x", "twitter"}
                preset = "ultrafast" if is_twitter else "veryfast"
                crf = "28" if is_twitter else "23"
                
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
                        preset,
                        "-crf",
                        crf,
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
        playback = meta.get("playback")
        # Log playback URL for Twitter debugging
        if detect_platform(url) in {"x", "twitter"}:
            print(f"[Twitter Playback] URL: {url}")
            print(f"[Twitter Playback] Type: {playback.get('type') if playback else 'None'}")
            print(f"[Twitter Playback] Video URL: {playback.get('url') if playback else 'None'}")
        return jsonify({"metadata": meta, "formats": formats, "cleanedUrl": url, "playback": playback})
    except Exception as exc:
        # Provide clearer message for common blocked cases
        error_str = str(exc)
        hint = ""
        plat = detect_platform(url)
        
        if plat == "youtube":
            if "signature" in error_str.lower() or "nsig" in error_str.lower() or "Precondition check failed" in error_str.lower():
                hint = " — YouTube signature extraction failed. Solution: Export cookies from your browser using: VIDSLICER_COOKIES_FROM_BROWSER=chrome (or firefox). See YOUTUBE_COOKIES_GUIDE.md"
            elif "Sign in" in error_str or "bot" in error_str.lower():
                hint = " — YouTube is blocking this video, likely due to bot detection or age restriction. SOLUTION: Export your browser cookies! Steps: 1) Install the 'Open in Browser' extension, 2) Export cookies with VIDSLICER_COOKIES_FROM_BROWSER=chrome (Windows) or chrome/firefox (Linux), 3) Restart the app. See YOUTUBE_COOKIES_GUIDE.md"
            elif "403" in error_str or "Forbidden" in error_str or "HTTP Error 403" in error_str:
                hint = " — YouTube is blocking access (403 Forbidden). Try: 1) Export cookies from your browser (VIDSLICER_COOKIES_FROM_BROWSER=chrome), 2) Update yt-dlp, 3) Try again after a few minutes."
            elif "Private video" in error_str:
                hint = " — This video is private and cannot be downloaded."
            elif "not available" in error_str.lower():
                hint = " — Video format not available (geo-blocked, age-restricted, or removed)."
        elif plat == "tiktok":
            hint = " — TikTok requires authentication. Solution: 1) Export cookies from TikTok in your browser (VIDSLICER_COOKIES_FROM_BROWSER=firefox), 2) Some TikTok videos may not be downloadable due to platform restrictions."
        elif plat == "instagram":
            hint = " — Instagram requires authentication. Solution: Export cookies from Instagram using VIDSLICER_COOKIES_FROM_BROWSER=firefox"
        elif plat in {"x", "twitter"}:
            if "Network is unreachable" in error_str or "401" in error_str or "403" in error_str or "Forbidden" in error_str:
                hint = " — Twitter/X is blocking access. Solution: Export cookies from Twitter using VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox. See YOUTUBE_COOKIES_GUIDE.md for instructions."
            else:
                hint = " — Platform may require authentication. Export browser cookies (VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox) for better access."
        else:
            hint = " — Platform may require authentication. Export browser cookies (VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox) for better access."
        
        return jsonify({"error": f"{error_str}{hint}"}), 400


@app.route("/api/stream", methods=["GET"])
def api_stream():
    """
    Proxy endpoint for streaming video content from external sources.
    Handles Twitter/X HLS streams, YouTube, etc. with proper headers.
    Rewrites HLS playlists to use absolute URLs for segment requests.
    Required params: url (base64-encoded), type (hls/mp4/dash)
    """
    import base64
    import re
    
    url_b64 = request.args.get("url", "")
    stream_type = request.args.get("type", "hls")
    
    if not url_b64:
        return jsonify({"error": "Missing url parameter"}), 400
    
    try:
        stream_url = base64.b64decode(url_b64).decode("utf-8")
    except Exception as e:
        return jsonify({"error": f"Invalid URL encoding: {e}"}), 400
    
    # Validate that URL is from allowed streaming sources
    allowed_hosts = {
        "video.twimg.com",  # Twitter
        "pbs.twimg.com",    # Twitter media
        "r.twimg.com",      # Twitter
        "manifest.googlevideo.com",  # YouTube
        "rr.prod.svcs.gstatic.com",  # YouTube
        "cdn-fqdn.fyp.tiktok.com",   # TikTok
        "instagram.com",    # Instagram
        "scontent-",        # Instagram CDN
        "v",                # Generic video CDN
        "cdn",              # Generic CDN
    }
    
    parsed = urlparse(stream_url)
    hostname = parsed.netloc.lower()
    
    # Check if hostname is allowed
    if not any(allowed in hostname for allowed in allowed_hosts):
        return jsonify({"error": "URL not from allowed streaming source"}), 403
    
    try:
        # Fetch video stream with proper headers for Twitter/X
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://twitter.com/",
            "Origin": "https://twitter.com",
            "Accept": "*/*",
        }
        
        response = requests.get(
            stream_url,
            headers=headers,
            timeout=30,
            stream=True,
            verify=True
        )
        
        if response.status_code == 403:
            # Try without Referer if Twitter blocks it
            headers.pop("Referer", None)
            response = requests.get(
                stream_url,
                headers=headers,
                timeout=30,
                stream=True,
                verify=True
            )
        
        if response.status_code != 200:
            return jsonify({"error": f"Failed to fetch stream: {response.status_code}"}), response.status_code
        
        # For HLS playlists, rewrite relative URLs to absolute ones
        if stream_type == "hls" and "m3u8" in stream_url.lower():
            # Read entire playlist content
            playlist_content = response.text
            
            # Get the base URL for resolving relative paths
            base_url = stream_url.rsplit('/', 1)[0] + '/'
            
            # Rewrite relative URLs to absolute URLs
            # Pattern: lines that don't start with # or http (these are segment URLs)
            lines = playlist_content.split('\n')
            rewritten_lines = []
            
            for line in lines:
                line = line.rstrip()
                # If it's a relative URL (doesn't start with # and doesn't start with http)
                if line and not line.startswith('#') and not line.startswith('http'):
                    # Make it absolute
                    absolute_url = urljoin(base_url, line)
                    rewritten_lines.append(absolute_url)
                else:
                    # Keep comments and other content as-is
                    rewritten_lines.append(line)
            
            rewritten_content = '\n'.join(rewritten_lines)
            
            return Response(
                rewritten_content,
                mimetype="application/vnd.apple.mpegurl",
                headers={
                    "Cache-Control": "public, max-age=3600",
                    "Connection": "keep-alive",
                }
            )
        
        # For non-HLS or if we can't modify, stream as-is
        content_type = response.headers.get("content-type", "application/octet-stream")
        if stream_type == "hls":
            content_type = "application/vnd.apple.mpegurl"
        elif stream_type == "mp4":
            content_type = "video/mp4"
        elif stream_type == "dash":
            content_type = "application/dash+xml"
        
        def stream_generator():
            """Stream video in chunks"""
            chunk_size = 65536  # 64KB chunks
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk
        
        return Response(
            stream_generator(),
            mimetype=content_type,
            headers={
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=3600",
                "Connection": "keep-alive",
            }
        )
    
    except requests.exceptions.Timeout:
        return jsonify({"error": "Stream request timed out"}), 504
    except requests.exceptions.RequestException as e:
        return jsonify({"error": f"Failed to fetch stream: {str(e)}"}), 502
    except Exception as e:
        return jsonify({"error": f"Stream proxy error: {str(e)}"}), 500


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
                hint = " — YouTube signature extraction failed. Export browser cookies using VIDSLICER_COOKIES_FROM_BROWSER=chrome"
            elif "Sign in" in error_str or "bot" in error_str.lower():
                hint = " — YouTube is blocking bot-like access. Export your browser cookies! Set VIDSLICER_COOKIES_FROM_BROWSER=chrome (Windows) or chrome/firefox (Linux)"
            elif "403" in error_str or "Forbidden" in error_str:
                hint = " — YouTube is blocking access (403 Forbidden). Export browser cookies or try again later."
        elif plat in {"tiktok", "instagram"}:
            hint = " — Platform requires authentication. Export browser cookies (VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox) for access."
        
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
        elif plat in {"x", "twitter"}:
            if "Network is unreachable" in error_str or "401" in error_str or "403" in error_str:
                hint = " — Twitter/X is blocking access. Try exporting cookies from your browser (VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox), or try again in a few minutes."
            elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                hint = " — Download timed out. This can happen with slow network connections. Try again or export browser cookies for better access."
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
            chunk_size = 65536  # 64KB chunks for faster streaming
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
                "Transfer-Encoding": "chunked",
                "X-Accel-Buffering": "no",  # Disable nginx buffering for streaming
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
        elif plat in {"x", "twitter"}:
            if "Network is unreachable" in error_str or "401" in error_str or "403" in error_str:
                hint = " — Twitter/X is blocking access. Try exporting cookies from your browser (VIDSLICER_COOKIES_FROM_BROWSER=chrome or firefox), or try again later."
            elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                hint = " — Download timed out. Try again or export browser cookies for better access."
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


# ========== FRONTEND SERVING ==========
# Serve static files (JS, CSS, etc.) from dist/assets
# This is handled automatically by Flask's static_folder configuration

# Serve index.html for root path
@app.route("/")
def serve_index():
    dist_path = os.path.join(os.path.dirname(__file__), "..", "dist", "index.html")
    if os.path.exists(dist_path):
        return send_file(dist_path)
    return jsonify({"error": "Frontend not built. Run 'npm run build' in the project root."}), 500


# Catch-all route: serve index.html for React Router client-side routing
# This must be last so it doesn't interfere with API routes
@app.route("/<path:path>")
def serve_frontend(path):
    # If the path is an API route, let Flask handle it normally (will 404)
    if path.startswith("api/"):
        return jsonify({"error": f"API endpoint not found: /{path}"}), 404
    
    # Otherwise serve index.html so React Router can handle the path
    dist_path = os.path.join(os.path.dirname(__file__), "..", "dist", "index.html")
    if os.path.exists(dist_path):
        return send_file(dist_path)
    return jsonify({"error": "Frontend not built. Run 'npm run build' in the project root."}), 500


if __name__ == "__main__":
    ensure_db()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))


