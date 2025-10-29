export default function QualitySelect({ formats, selected, onChange }) {
    const videoFormats = (formats || [])
        .filter(f => f.kind !== 'audio')
        .sort((a, b) => (b.height || 0) - (a.height || 0))

    const audioOnly = (formats || []).filter(f => f.kind === 'audio')

    return (
        <div className="quality-select card">
            <label className="label">Video Quality</label>
            <select className="select" value={selected || ''} onChange={(e) => onChange(e.target.value)}>
                <option value="">Best Available</option>
                {videoFormats.map(f => (
                    <option key={f.format_id} value={f.format_id}>
                        {(f.height ? `${f.height}p` : f.label)} {f.fps ? `${f.fps}fps` : ''}
                    </option>
                ))}
            </select>
            <div className="audio-row">
                <button type="button" className="btn secondary" onClick={() => onChange('audio:mp3')}>
                    Audio Only (MP3)
                </button>
                {audioOnly.length > 0 && <span className="hint">High quality audio available</span>}
            </div>
        </div>
    )
}


