import { useEffect, useMemo, useState } from 'react'

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

    const positions = useMemo(() => {
        const d = duration || 0
        const s = d ? (localStart / d) * 100 : 0
        const e = d ? (localEnd / d) * 100 : 0
        return { s, e }
    }, [localStart, localEnd, duration])

    return (
        <div className="range-selector card">
            <div className="range-track">
                <div className="range-line" />
                {duration ? (
                    <>
                        {/* Underlay showing selected segment */}
                        <div
                            className="range-selected"
                            style={{ left: `${positions.s}%`, width: `${Math.max(positions.e - positions.s, 0)}%` }}
                        />
                        {/* Two inputs overlaid to act like two handles on one line */}
                        <input
                            aria-label="Start"
                            className="range-input"
                            type="range"
                            min="0"
                            max={duration}
                            step="1"
                            value={localStart}
                            onChange={(e) => onStartChange(e.target.value)}
                        />
                        <input
                            aria-label="End"
                            className="range-input"
                            type="range"
                            min="0"
                            max={duration}
                            step="1"
                            value={localEnd}
                            onChange={(e) => onEndChange(e.target.value)}
                        />
                        {/* Time labels positioned above the line */}
                        <div className="range-label start" style={{ left: `${positions.s}%` }}>{format(localStart)}</div>
                        <div className="range-label end" style={{ left: `${positions.e}%` }}>{format(localEnd)}</div>
                    </>
                ) : null}
            </div>
            {fullSelected && <div className="tag">Full video selected</div>}
        </div>
    )
}


