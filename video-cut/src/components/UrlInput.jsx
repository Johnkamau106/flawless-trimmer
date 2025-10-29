import { useState } from 'react'

export default function UrlInput({ onSubmit, loading }) {
    const [url, setUrl] = useState('')

    function handleSubmit(e) {
        e.preventDefault()
        if (!url.trim()) return
        onSubmit(url.trim())
    }

    return (
        <form onSubmit={handleSubmit} className="card url-input">
            <input
                className="input"
                type="url"
                placeholder="Paste video URL (YouTube, Instagram, TikTok, etc.)"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
            />
            <button className="btn" type="submit" disabled={loading}>
                {loading ? 'Inspecting…' : 'Fetch'}
            </button>
        </form>
    )
}


