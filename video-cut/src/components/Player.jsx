import React, { useEffect, useRef } from 'react'
import ReactPlayer from 'react-player'

// This Player supports direct mp4 and HLS (m3u8) via hls.js, with ReactPlayer fallback for sites it can embed
export default function Player({ url, playback, playing, onProgress, onDuration }) {
    const videoRef = useRef(null)
    const fbAppId = import.meta.env.VITE_FACEBOOK_APP_ID

    const type = playback?.type
    const playUrl = playback?.url || url

    // HLS handling
    useEffect(() => {
        if (!playUrl || type !== 'hls') return
        const video = videoRef.current
        if (!video) return
        const canNative = video.canPlayType('application/vnd.apple.mpegurl')
        let hls
        if (canNative) {
            video.src = playUrl
        } else {
            import('hls.js').then(({ default: Hls }) => {
                if (Hls.isSupported()) {
                    hls = new Hls({ lowLatencyMode: true })
                    hls.loadSource(playUrl)
                    hls.attachMedia(video)
                } else {
                    // Fallback to ReactPlayer if HLS unsupported
                }
            })
        }
        return () => {
            if (hls) {
                hls.destroy()
            }
        }
    }, [playUrl, type])

    // If we have a direct mp4 or native HLS, render native video tag
    if (type === 'mp4' || type === 'hls') {
        return (
            <div className="player-wrapper">
                <video
                    ref={videoRef}
                    src={type === 'mp4' ? playUrl : undefined}
                    controls
                    style={{ width: '100%', height: '100%' }}
                    autoPlay={!!playing}
                    onLoadedMetadata={(e) => onDuration?.(e.currentTarget.duration)}
                    onTimeUpdate={(e) => onProgress?.({ playedSeconds: e.currentTarget.currentTime })}
                />
            </div>
        )
    }

    // Else fallback to ReactPlayer for providers it supports
    const canPlay = ReactPlayer.canPlay(playUrl)
    if (!canPlay) {
        return (
            <div className="player-wrapper card" style={{ display: 'grid', placeItems: 'center', padding: '20px' }}>
                <div className="muted" style={{ textAlign: 'center' }}>
                    Preview not supported for this platform. You can still download using the controls below.
                </div>
            </div>
        )
    }

    return (
        <div className="player-wrapper">
            <ReactPlayer
                url={playUrl}
                playing={playing}
                controls
                width="100%"
                height="100%"
                onProgress={onProgress}
                onDuration={onDuration}
                config={{ facebook: fbAppId ? { appId: fbAppId } : {} }}
            />
        </div>
    )
}


