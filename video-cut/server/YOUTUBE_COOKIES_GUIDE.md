# YouTube Cookies Export Guide

## The Problem

YouTube blocks bot-like requests with the error:
```
ERROR: [youtube] {VIDEO_ID}: Sign in to confirm you're not a bot
```

This happens because YouTube detects automated downloads and requires authentication to proceed.

## The Solution

The app needs your browser's YouTube authentication cookies to download videos. Here's how to export them:

### For Windows Users

#### Option 1: Using Chrome (Recommended)

1. **Open Chrome Settings:**
   - Go to `Settings` → `Privacy and Security` → `Cookies and other site data`
   - Or navigate to `chrome://settings/cookies`

2. **Export Cookies:**
   - You don't need to manually export! The app auto-detects Chrome cookies on Windows.
   - Just make sure you're logged in to YouTube in Chrome.

3. **Start the app:**
   - The app will automatically use your Chrome cookies.

#### Option 2: Using Firefox (Alternative)

1. **Make sure you're logged in to YouTube in Firefox**

2. **Start the app with Firefox cookies:**
   ```batch
   set VIDSLICER_COOKIES_FROM_BROWSER=firefox
   python server/app.py
   ```
   
   Or on Windows (PowerShell):
   ```powershell
   $env:VIDSLICER_COOKIES_FROM_BROWSER = "firefox"
   python server/app.py
   ```

### For Linux/WSL Users

#### Using Chrome/Chromium

```bash
export VIDSLICER_COOKIES_FROM_BROWSER=chrome
python server/app.py
```

#### Using Chromium

```bash
export VIDSLICER_COOKIES_FROM_BROWSER=chromium
python server/app.py
```

#### Using Firefox

```bash
export VIDSLICER_COOKIES_FROM_BROWSER=firefox
python server/app.py
```

## Manual Cookie Export (If Auto-Detection Doesn't Work)

If the auto-detection fails, you can manually export and provide cookies:

### Step 1: Install Cookie Export Extension

1. For **Chrome**:
   - Install [Edit This Cookie](https://chrome.google.com/webstore/detail/editthiscookie/fngmhnnpilhplaeedifhccceomclgfbg) extension
   - Or [Cookie Editor](https://chrome.google.com/webstore/detail/cookie-editor/hlkenndednhfkekhgcdicdfddnkalmdm)

2. For **Firefox**:
   - Install [Cookie-Editor](https://addons.mozilla.org/en-US/firefox/addon/cookie-editor/) addon

### Step 2: Export Cookies

1. Go to `https://youtube.com`
2. Open the cookie extension
3. Click "Export As JSON" or similar option
4. Save the file as `cookies.json`

### Step 3: Use the Cookie File

```bash
# On Windows (Command Prompt)
set VIDSLICER_COOKIES=C:\path\to\cookies.json
python server/app.py

# On Windows (PowerShell)
$env:VIDSLICER_COOKIES = "C:\path\to\cookies.json"
python server/app.py

# On Linux/WSL
export VIDSLICER_COOKIES=/path/to/cookies.json
python server/app.py
```

## Automatic Cookie Detection

The app automatically detects cookies from:

**Windows:**
- `%APPDATA%\Local\Google\Chrome\User Data` (Chrome)
- `%APPDATA%\Roaming\.mozilla\firefox` (Firefox)

**Linux/WSL:**
- `~/.config/google-chrome` (Chrome)
- `~/.config/chromium` (Chromium)
- `~/.mozilla/firefox` (Firefox)

## Troubleshooting

### Still Getting "Sign in to confirm you're not a bot"?

1. **Make sure you're logged in:**
   - Open your browser and go to `youtube.com`
   - Make sure you're logged into your YouTube account
   - The cookies must be fresh and valid

2. **Check cookie expiration:**
   - Some cookies expire frequently
   - Re-export or log in again in your browser

3. **Update yt-dlp:**
   ```bash
   pip install --upgrade yt-dlp
   ```

4. **Try a different browser:**
   - If Chrome doesn't work, try Firefox
   - Set `VIDSLICER_COOKIES_FROM_BROWSER=firefox`

5. **Wait a bit:**
   - YouTube might rate-limit you
   - Wait 10-15 minutes and try again

6. **Check your firewall/VPN:**
   - Some VPNs/proxies can interfere
   - Try disabling VPN temporarily

### "cookiesfrombrowser" not recognized?

- Ensure yt-dlp is updated: `pip install --upgrade yt-dlp`
- Your yt-dlp version must be from 2023 or newer

### Still having issues?

1. Verify the browser is open to YouTube.com while the app runs (sometimes helps)
2. Try the manual cookie export method
3. Check that your internet connection is stable
4. Restart your computer

## Age-Restricted Videos

Some videos are age-restricted on YouTube. The cookies help bypass this as they show YouTube you're an authenticated user:

1. Make sure your YouTube account is set to allow viewing age-restricted content
2. Log in to YouTube in your browser before running the app
3. Export cookies as described above

## Privacy Notice

⚠️ **Important:** 
- Cookies contain your session authentication
- Only use them on trusted systems
- The app never uploads or shares your cookies
- Cookies are used only locally to download videos

## FAQ

**Q: Do I need to be logged in every time I use the app?**
A: You need to be logged in at least once so the browser stores the cookies. After that, the app will use stored cookies unless they expire.

**Q: Will the cookies work on a different computer?**
A: No, cookies are stored locally in your browser's profile. You'll need to export and provide them if using a different computer.

**Q: How often do I need to re-export cookies?**
A: Typically not often. If you get "Sign in" errors, try logging in to YouTube again in your browser and re-exporting.

**Q: Is this safe?**
A: Yes. The app only uses cookies to authenticate to YouTube. Cookies are stored locally and never uploaded anywhere.

## Alternative: Using Download Button Instead

If you don't want to deal with cookies, you can:

1. Go to YouTube directly
2. Use the browser's network inspection tools to download the video
3. Use dedicated tools like `youtube-dl` on the command line with authentication

But this app with auto-detected cookies is much simpler!
