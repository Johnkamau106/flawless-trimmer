# Long Video Download Support - Configuration Guide

Your app now supports downloading **videos longer than 1 hour** without timing out!

## 🎬 What Was Fixed

### Problem
- Downloads over 5 minutes (300 seconds) were timing out
- Large videos (1+ hour) couldn't be downloaded
- Error: `timeout of 300000ms exceeded`

### Solution
- ✅ Increased request timeout to **1 hour (3600 seconds)** by default
- ✅ Optimized streaming with **2MB chunks** (was 8KB)
- ✅ Added connection keep-alive headers
- ✅ Configurable timeout via environment variable

## ⚙️ Configuration

### Default Timeout: 1 Hour (3600 seconds)

For **videos longer than 1 hour**, set a longer timeout:

#### Linux/Mac
```bash
# Download 2-hour video
export VIDSLICER_DOWNLOAD_TIMEOUT=7200
./run.sh

# Download 3-hour video
export VIDSLICER_DOWNLOAD_TIMEOUT=10800
./run.sh
```

#### Windows (PowerShell)
```powershell
# Download 2-hour video
$env:VIDSLICER_DOWNLOAD_TIMEOUT = "7200"
.\run.bat

# Download 3-hour video
$env:VIDSLICER_DOWNLOAD_TIMEOUT = "10800"
.\run.bat
```

#### Windows (Command Prompt)
```cmd
# Download 2-hour video
set VIDSLICER_DOWNLOAD_TIMEOUT=7200
run.bat

# Download 3-hour video
set VIDSLICER_DOWNLOAD_TIMEOUT=10800
run.bat
```

### Or Edit .env File

Create/edit `server/.env`:
```env
# Download timeout in seconds
# 1 hour = 3600, 2 hours = 7200, 3 hours = 10800
VIDSLICER_DOWNLOAD_TIMEOUT=7200
```

## 🚀 Recommended Settings

| Video Length | Timeout Setting | Command |
|---|---|---|
| Up to 1 hour | 3600 (default) | `./run.sh` |
| 1-2 hours | 7200 | `export VIDSLICER_DOWNLOAD_TIMEOUT=7200 && ./run.sh` |
| 2-3 hours | 10800 | `export VIDSLICER_DOWNLOAD_TIMEOUT=10800 && ./run.sh` |
| 3+ hours | 14400+ | `export VIDSLICER_DOWNLOAD_TIMEOUT=14400 && ./run.sh` |

## 📊 Timeout Reference

```
Time             Seconds
5 minutes        300
10 minutes       600
15 minutes       900
30 minutes       1800
1 hour           3600
2 hours          7200
3 hours          10800
4 hours          14400
6 hours          21600
```

## ✅ What's Optimized

### Streaming Performance
- **Chunk Size**: Increased from 8KB to 2MB
- **Connection**: Keep-alive enabled
- **Caching**: Bypassed for faster delivery
- **Headers**: Cache-Control prevents timeout

### Expected Download Speeds

For a **2-hour video (~2.5 GB)**:
- At 500 KiB/s: ~1 hour 20 minutes
- At 1 MiB/s: ~40 minutes
- At 2 MiB/s: ~20 minutes

Just ensure timeout is set higher than expected download time!

## 🔧 Troubleshooting

### Still getting timeout?

1. **Check your timeout setting**:
   ```bash
   echo $VIDSLICER_DOWNLOAD_TIMEOUT  # Linux/Mac
   echo %VIDSLICER_DOWNLOAD_TIMEOUT%  # Windows
   ```

2. **Increase timeout further**:
   - For very large files: `export VIDSLICER_DOWNLOAD_TIMEOUT=21600` (6 hours)

3. **Check download speed**:
   - Very slow speeds (< 50 KiB/s) may timeout for large files
   - Look at Flask server logs for network issues

4. **Use VPN/Proxy for better connectivity**:
   - Unstable connections may cause slow downloads
   - A faster connection = faster completion = no timeout

### Browser Timeout

If your **browser** times out before the file finishes downloading:
- Most browsers have their own timeout (usually 30 minutes)
- For very large files, consider:
  - Using curl to download: `curl -o output.mp4 http://localhost:5000/api/download?url=...`
  - Using a download manager (IDM, aria2, etc.)

## 💡 Tips for Large Videos

1. **Test with smaller videos first**
   - Verify connectivity works before attempting 1+ hour videos

2. **Monitor the Flask logs**
   - Watch for network errors or slowdowns

3. **Use stable networks**
   - Wired connection > WiFi for long downloads
   - VPN may slow down; add extra buffer time

4. **Check available disk space**
   - 1-hour video ≈ 500MB to 2GB (depending on quality)
   - Ensure `/tmp` (Linux) or temp directory has space

## 📝 Example Usage

### Download a 2-hour YouTube video

```bash
# 1. Set timeout for 2 hours
export VIDSLICER_DOWNLOAD_TIMEOUT=7200

# 2. Start server
cd video-cut/server
./run.sh

# 3. In browser or API call:
# POST to /api/download with your 2-hour YouTube URL
# Or manually: curl with video URL
```

### Download a 3-hour video with progress tracking

```bash
# Terminal 1: Start server
export VIDSLICER_DOWNLOAD_TIMEOUT=10800
./run.sh

# Terminal 2: Download with curl (shows progress)
curl -C - --progress-bar \
  -X POST "http://localhost:5000/api/download" \
  -H "Content-Type: application/json" \
  -d '{"url":"YOUR_VIDEO_URL"}' \
  -o video.mp4
```

## 🔍 Server Configuration (Advanced)

If you're using **Gunicorn** or **production WSGI server**:

```bash
# Gunicorn with 2-hour timeout
gunicorn --timeout 7200 app:app

# uWSGI with 2-hour timeout
uwsgi --http :5000 --wsgi-file app.py --socket-timeout 7200
```

---

**You're all set to download long videos! 🎬**

Set your timeout, start the server, and download away!
