import { useEffect, useState } from 'react'

export default function RangeSelector({ duration, start, end, onChange }) {
    const [localStart, setLocalStart] = useState(start ?? 0)
    const [localEnd, setLocalEnd] = useState(end ?? duration ?? 0)

    useEffect(() => {
        setLocalStart(start ?? 0)
    }, [start])
    useEffect(() => {
        setLocalEnd(end ?? duration ?? 0)
    }, [end, duration])

    function clamp(val) {
        if (!duration) return 0
        return Math.max(0, Math.min(duration, val))
    }

    function onStartChange(val) {
        const v = clamp(Number(val))
        const newStart = Math.min(v, localEnd)
        setLocalStart(newStart)
        onChange({ start: newStart, end: localEnd })
    }

    function onEndChange(val) {
        const v = clamp(Number(val))
        const newEnd = Math.max(v, localStart)
        setLocalEnd(newEnd)
        onChange({ start: localStart, end: newEnd })
    }

    function format(t) {
        const s = Math.floor(t || 0)
        const h = Math.floor(s / 3600)
        const m = Math.floor((s % 3600) / 60)
        const sec = s % 60
        if (h > 0) return `${h}:${m.toString().padStart(2, '0')}:${sec.toString().padStart(2, '0')}`
        return `${m}:${sec.toString().padStart(2, '0')}`
    }

    const fullSelected = duration && Math.abs((localEnd ?? 0) - (localStart ?? 0) - duration) < 0.001

    return (
        <div className="range-selector card">
            <div className="row">
                <div className="col">
                    <label className="label">Start</label>
                    <input type="number" min="0" max={duration || 0} step="1" className="input"
                        value={Math.floor(localStart)} onChange={(e) => onStartChange(e.target.value)} />
                    <div className="hint">{format(localStart)}</div>
                </div>
                <div className="col">
                    <label className="label">End</label>
                    <input type="number" min="0" max={duration || 0} step="1" className="input"
                        value={Math.floor(localEnd)} onChange={(e) => onEndChange(e.target.value)} />
                    <div className="hint">{format(localEnd)}</div>
                </div>
            </div>
            {fullSelected && <div className="tag">Full video selected</div>}
            {duration ? (
                <input type="range" min="0" max={duration} step="1" value={localStart}
                    onChange={(e) => onStartChange(e.target.value)} />
            ) : null}
            {duration ? (
                <input type="range" min="0" max={duration} step="1" value={localEnd}
                    onChange={(e) => onEndChange(e.target.value)} />
            ) : null}
        </div>
    )
}


