import os
import re
import tempfile
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, parse_qsl, urlunparse, urlencode

from flask import Flask, jsonify, request, send_file
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy


DB_PATH = os.environ.get("VIDSLICER_DB", os.path.join(os.path.dirname(__file__), "vidslicer.db"))


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
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "geo_bypass": True,
        # Use a realistic desktop UA to reduce blocking
        "http_headers": {
            "User-Agent": os.environ.get(
                "VIDSLICER_UA",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
            )
        },
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
    import yt_dlp

    ydl_opts = _build_ydl_opts({
        "skip_download": True,
    }, for_url=url)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    formats = []
    for f in info.get("formats", []):
        if f.get("vcodec") != "none" and f.get("acodec") == "none":
            # video-only; still allow but label
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
        })

    meta = {
        "id": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "webpage_url": info.get("webpage_url") or url,
        "platform": detect_platform(url),
    }
    return meta, formats


def download_media(url: str, format_id: Optional[str], audio_only: bool, start: Optional[float], end: Optional[float]):
    import yt_dlp
    import subprocess

    temp_dir = tempfile.mkdtemp(prefix="vidslicer_")
    base_name = uuid.uuid4().hex
    download_path = os.path.join(temp_dir, base_name + ".%(ext)s")

    ydl_opts = _build_ydl_opts({
        "outtmpl": download_path,
    }, for_url=url)

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
    elif format_id:
        ydl_opts["format"] = format_id
    else:
        # Default to best merged video+audio; fallback to best single stream
        ydl_opts.update({
            "format": "bestvideo*+bestaudio/best",
            "merge_output_format": "mp4",
        })

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        downloaded = ydl.prepare_filename(info)

    source_path = downloaded
    output_ext = "mp3" if audio_only else os.path.splitext(source_path)[1].lstrip(".")
    output_path = os.path.join(temp_dir, f"{base_name}.out.{output_ext}")

    # Trim if needed
    if start is not None and end is not None and end > start:
        # Use ffmpeg to trim without re-encoding when possible
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
            output_path,
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            # Fallback to re-encode for odd containers
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start),
                "-to",
                str(end),
                "-i",
                source_path,
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
        return jsonify({"metadata": meta, "formats": formats, "cleanedUrl": url})
    except Exception as exc:
        # Provide clearer message for common blocked cases
        hint = ""
        plat = detect_platform(url)
        if plat in {"tiktok", "instagram", "facebook", "twitter"}:
            hint = " — site may require cookies/session. Set VIDSLICER_COOKIES or VIDSLICER_COOKIES_FROM_BROWSER."
        return jsonify({"error": f"{str(exc)}{hint}"}), 400


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
    try:
        file_path, info = download_media(url, format_id, audio_only, start, end)
        title = info.get("title") or "video"
        safe_title = re.sub(r"[^\w\-\s]", "", title).strip().replace(" ", "_") or "video"
        ext = os.path.splitext(file_path)[1]
        as_attachment_name = f"{safe_title}{ext}"
        return send_file(file_path, as_attachment=True, download_name=as_attachment_name)
    except Exception as exc:
        hint = ""
        plat = detect_platform(url)
        if plat in {"tiktok", "instagram", "facebook", "twitter"}:
            hint = " — site may require cookies/session. Set VIDSLICER_COOKIES or VIDSLICER_COOKIES_FROM_BROWSER."
        return jsonify({"error": f"{str(exc)}{hint}"}), 400


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


