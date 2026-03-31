import { useMemo, useState } from 'react'
import './App.css'
import UrlInput from './components/UrlInput.jsx'
import Player from './components/Player.jsx'
import QualitySelect from './components/QualitySelect.jsx'
import RangeSelector from './components/RangeSelector.jsx'
import { inspectUrl, saveClip } from './api/client.js'

function App() {
  const [loading, setLoading] = useState(false)
  const [meta, setMeta] = useState(null)
  const [formats, setFormats] = useState([])
  const [url, setUrl] = useState('')
  const [playback, setPlayback] = useState(null)
  const [play, setPlay] = useState(false)
  const [duration, setDuration] = useState(0)
  const [range, setRange] = useState({ start: 0, end: 0 })
  const [quality, setQuality] = useState('')
  const [status, setStatus] = useState('')

  async function onSubmit(u) {
    setLoading(true)
    setStatus('Fetching metadata…')
    try {
      const data = await inspectUrl(u)
      setMeta(data.metadata)
      setFormats(data.formats)
      setUrl(data.cleanedUrl || u)
      setPlayback(data.playback || data.metadata?.playback || null)
      setPlay(true)
      setStatus('')
    } catch (e) {
      setStatus(e?.response?.data?.error || e.message)
    } finally {
      setLoading(false)
    }
  }

  function onDurationChange(d) {
    setDuration(d)
    setRange(r => ({ start: 0, end: d }))
  }

  const isAudio = quality === 'audio:mp3'

  async function onDownload() {
    if (!url) return
    setStatus('Downloading to browser…')

    const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'

    try {
      // Detect if user actually trimmed the video
      // Check if start was moved significantly OR end was moved significantly early
      const startTrimmed = range.start > 1.0  // More than 1 second from start
      const endTrimmed = duration && (duration - range.end) > 1.0  // More than 1 second cut from end
      const isTrimmed = startTrimmed || endTrimmed

      console.log(`[onDownload] duration=${duration}, start=${range.start}, end=${range.end}, isTrimmed=${isTrimmed}`)

      if (isTrimmed) {
        // Trimmed video - use /api/clip for backend processing (download + trim + send)
        const response = await fetch(`${API_BASE_URL}/api/clip`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            format_id: isAudio ? undefined : (quality || undefined),
            audio_only: isAudio,
            start_time: range.start,
            end_time: range.end,
            title: meta?.title,
          }),
        })

        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(errorData.error || `Server error: ${response.status}`)
        }

        // Extract filename from Content-Disposition header
        const disposition = response.headers.get('Content-Disposition') || ''
        let filename = 'video.mp4'

        const rfc5987Match = disposition.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
        if (rfc5987Match && rfc5987Match[1]) {
          try {
            filename = decodeURIComponent(rfc5987Match[1])
          } catch (_) { }
        }
        if (!rfc5987Match) {
          const simpleMatch = disposition.match(/filename="?([^";\n]+)"?/)
          if (simpleMatch && simpleMatch[1]) {
            filename = simpleMatch[1]
          }
        }

        // Convert response stream to blob and download
        const blob = await response.blob()
        const blobUrl = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = filename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        setTimeout(() => window.URL.revokeObjectURL(blobUrl), 100)
      } else {
        // Full video - fetch as blob and force browser download
        // Full video - backend proxies stream from CDN (no CORS issues)
        setStatus('Downloading from source…')
        const response = await fetch(`${API_BASE_URL}/api/download`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            url,
            format_id: isAudio ? undefined : (quality || undefined),
            audio_only: isAudio,
          }),
        })

        if (!response.ok) {
          const errorData = await response.json()
          throw new Error(errorData.error || `Server error: ${response.status}`)
        }

        // Extract filename from Content-Disposition header
        const disposition = response.headers.get('Content-Disposition') || ''
        let dlFilename = 'video.mp4'

        const rfc5987Match = disposition.match(/filename\*=UTF-8''(.+?)(?:;|$)/)
        if (rfc5987Match && rfc5987Match[1]) {
          try {
            dlFilename = decodeURIComponent(rfc5987Match[1])
          } catch (_) { }
        }
        if (!rfc5987Match) {
          const simpleMatch = disposition.match(/filename="?([^";\n]+)"?/)
          if (simpleMatch && simpleMatch[1]) {
            dlFilename = simpleMatch[1]
          }
        }

        // Convert response stream to blob and download
        const blob = await response.blob()
        const blobUrl = window.URL.createObjectURL(blob)
        const link = document.createElement('a')
        link.href = blobUrl
        link.download = dlFilename
        document.body.appendChild(link)
        link.click()
        document.body.removeChild(link)
        setTimeout(() => window.URL.revokeObjectURL(blobUrl), 100)
      }

      setStatus('✓ Download complete! Check your Downloads folder.')

      // Fire and forget - save clip to history (metadata only)
      try {
        saveClip({
          url,
          title: meta?.title,
          duration: meta?.duration,
          start_time: range.start,
          end_time: range.end,
        })
      } catch (_) { }
    } catch (e) {
      setStatus(`Download failed: ${e.message}`)
    }
  }

  const currentStep = !url ? 1 : !meta ? 2 : 3

  return (
    <div className="layout">
      <header className="header">
        <h1>🎬 Flawless VidSlicer</h1>
        <p className="subtitle">Paste a link, preview, trim, and download.</p>
      </header>

      {/* Step Progress Indicator */}
      <div className="steps-indicator">
        <div className={`step ${currentStep >= 1 ? 'active' : ''}`}>
          <div className="step-number">1</div>
          <div className="step-label">Paste URL</div>
        </div>
        <div className="step-line"></div>
        <div className={`step ${currentStep >= 2 ? 'active' : ''}`}>
          <div className="step-number">2</div>
          <div className="step-label">Preview</div>
        </div>
        <div className="step-line"></div>
        <div className={`step ${currentStep >= 3 ? 'active' : ''}`}>
          <div className="step-number">3</div>
          <div className="step-label">Trim & Quality</div>
        </div>
        <div className="step-line"></div>
        <div className={`step ${currentStep >= 3 ? 'active' : ''}`}>
          <div className="step-number">4</div>
          <div className="step-label">Download</div>
        </div>
      </div>

      <main className="flow-container">
        {/* Step 1: URL Input */}
        <section className="flow-step">
          <div className="step-header">
            <span className="step-title">Step 1: Paste URL</span>
            {url && <span className="step-badge">✓ Done</span>}
          </div>
          <UrlInput onSubmit={onSubmit} loading={loading} />
        </section>

        {/* Step 2: Preview */}
        {url ? (
          <section className="flow-step">
            <div className="step-header">
              <span className="step-title">Step 2: Preview</span>
              {meta && <span className="step-badge">✓ Loaded</span>}
            </div>
            <div className="card player-card">
              <Player url={url} playback={playback} playing={play} onDuration={onDurationChange} onProgress={() => { }} />
              <div className="meta">
                <div className="title" title={meta?.title}>{meta?.title}</div>
                <div className="platform">{meta?.platform}</div>
              </div>
            </div>
          </section>
        ) : null}

        {/* Step 3: Trim & Quality Controls */}
        {meta ? (
          <section className="flow-step">
            <div className="step-header">
              <span className="step-title">Step 3: Trim & Select Quality</span>
            </div>
            <div className="controls-grid">
              {formats?.length ? (
                <QualitySelect formats={formats} selected={quality} onChange={setQuality} />
              ) : null}

              {typeof meta?.duration === 'number' ? (
                <RangeSelector duration={meta.duration} start={range.start} end={range.end} onChange={setRange} />
              ) : null}
            </div>
          </section>
        ) : null}

        {/* Step 4: Download */}
        {meta ? (
          <section className="flow-step final-step">
            <div className="step-header">
              <span className="step-title">Step 4: Download</span>
            </div>
            <div className="download-section">
              <button className="btn primary large" onClick={onDownload}>
                ⬇️ Download Video
              </button>
              {status && (
                <div className={`status-message ${status.includes('✓') ? 'success' : status.includes('failed') ? 'error' : 'info'}`}>
                  {status}
                </div>
              )}
            </div>
          </section>
        ) : null}
      </main>

      <footer className="footer">Built with React + Flask + yt-dlp</footer>
    </div>
  )
}

export default App
