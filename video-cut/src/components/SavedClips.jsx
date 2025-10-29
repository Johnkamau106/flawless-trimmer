import { useEffect, useState } from 'react'
import { listClips } from '../api/client'

export default function SavedClips() {
    const [clips, setClips] = useState([])
    const [loading, setLoading] = useState(false)

    async function load() {
        setLoading(true)
        try {
            const data = await listClips()
            setClips(data.clips || [])
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => { load() }, [])

    return (
        <aside className="sidebar">
            <div className="sidebar-header">
                <h3>Saved Clips</h3>
                <button className="btn small" onClick={load} disabled={loading}>{loading ? '…' : 'Refresh'}</button>
            </div>
            <div className="clips-list">
                {clips.length === 0 && <div className="muted">No saved clips yet</div>}
                {clips.map(c => (
                    <a key={c.id} className="clip-item" href={c.url} target="_blank" rel="noreferrer">
                        {c.thumbnail ? (
                            <img src={c.thumbnail} alt={c.title || 'thumbnail'} />
                        ) : (
                            <div className="thumb-placeholder">{c.platform || 'video'}</div>
                        )}
                        <div className="clip-meta">
                            <div className="clip-title" title={c.title}>{c.title || 'Untitled'}</div>
                            <div className="clip-times">
                                {c.start_time != null && c.end_time != null ? `${Math.floor(c.start_time)}s – ${Math.floor(c.end_time)}s` : 'Full'}
                            </div>
                        </div>
                    </a>
                ))}
            </div>
        </aside>
    )
}


