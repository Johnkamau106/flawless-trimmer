import { useMemo, useState } from 'react'
import './App.css'
import UrlInput from './components/UrlInput.jsx'
import Player from './components/Player.jsx'
import QualitySelect from './components/QualitySelect.jsx'
import RangeSelector from './components/RangeSelector.jsx'
import SavedClips from './components/SavedClips.jsx'
import { downloadMedia, inspectUrl, saveClip } from './api/client.js'

function App() {
  const [loading, setLoading] = useState(false)
  const [meta, setMeta] = useState(null)
  const [formats, setFormats] = useState([])
  const [url, setUrl] = useState('')
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
    setStatus('Preparing download…')
    const payload = {
      url,
      format_id: isAudio ? undefined : (quality || undefined),
      audio_only: isAudio,
      start: range.start,
      end: range.end,
    }
    try {
      const resp = await downloadMedia(payload)
      const blob = new Blob([resp.data])
      const a = document.createElement('a')
      const ext = isAudio ? 'mp3' : 'mp4'
      const title = (meta?.title || 'video').replace(/[^\w\-\s]/g, '').replace(/\s+/g, '_')
      a.href = URL.createObjectURL(blob)
      a.download = `${title}.${ext}`
      a.click()
      URL.revokeObjectURL(a.href)
      setStatus('Download started')
      // Fire and forget save clip
      try {
        await saveClip({
          url,
          title: meta?.title,
          duration: meta?.duration,
          start_time: range.start,
          end_time: range.end,
        })
      } catch (_) { }
    } catch (e) {
      setStatus(e?.response?.data?.error || e.message)
    }
  }

  return (
    <div className="layout">
      <header className="header">
        <h1>VidSlicer</h1>
        <div className="muted">Paste a link, preview, trim, and download.</div>
      </header>
      <main className="main">
        <div className="left">
          <UrlInput onSubmit={onSubmit} loading={loading} />

          {url ? (
            <div className="card">
              <Player url={url} playing={play} onDuration={onDurationChange} onProgress={() => { }} />
              <div className="meta">
                <div className="title" title={meta?.title}>{meta?.title}</div>
                <div className="platform">{meta?.platform}</div>
              </div>
            </div>
          ) : null}

          {formats?.length ? (
            <QualitySelect formats={formats} selected={quality} onChange={setQuality} />
          ) : null}

          {typeof meta?.duration === 'number' ? (
            <RangeSelector duration={meta.duration} start={range.start} end={range.end} onChange={setRange} />
          ) : null}

          <div className="actions card">
            <button className="btn primary" disabled={!url} onClick={onDownload}>Download</button>
            {status && <span className="status">{status}</span>}
          </div>
        </div>

        <SavedClips />
      </main>
      <footer className="footer">Built with React + Flask + yt-dlp</footer>
    </div>
  )
}

export default App
