import React, { useEffect, useRef, useState } from 'react'
import ReactPlayer from 'react-player'

// This Player supports direct mp4 and HLS (m3u8) via hls.js, with ReactPlayer fallback for sites it can embed
export default function Player({ url, playback, playing, onProgress, onDuration }) {
    const videoRef = useRef(null)
    const twitterContainerRef = useRef(null)
    const [twitterLoaded, setTwitterLoaded] = useState(false)
    const fbAppId = import.meta.env.VITE_FACEBOOK_APP_ID

    const type = playback?.type
    const rawPlayUrl = playback?.url || url

    // Handle proxy URLs - ensure they're absolute if API is on different host
    const getPlayUrl = () => {
        if (!rawPlayUrl) return rawPlayUrl
        // If URL starts with /, prepend API base URL (handles both /api and other relative paths)
        if (rawPlayUrl.startsWith('/')) {
            const apiBase = import.meta.env.VITE_API_BASE_URL || 'http://localhost:5000'
            return apiBase + rawPlayUrl
        }
        return rawPlayUrl
    }
    const playUrl = getPlayUrl()

    // Load Twitter widgets script when component mounts
    useEffect(() => {
        if (typeof window === 'undefined') return

        // Only load Twitter script if not already loaded
        if (window.__twitterWidgetsLoaded) {
            console.log('Twitter widgets script already loaded')
            return
        }

        window.__twitterWidgetsLoaded = true

        console.log('Injecting Twitter widgets script...')
        const script = document.createElement('script')
        script.src = 'https://platform.twitter.com/widgets.js'
        script.async = true
        script.charset = 'utf-8'
        script.crossOrigin = 'anonymous'

        script.onload = () => {
            console.log('✓ Twitter widgets script loaded successfully')
            window.__twitterWidgetsReady = true
            // Trigger any pending widget loads
            if (window.twttr?.widgets?.load) {
                console.log('Twitter widgets immediately available after script load')
            }
        }

        script.onerror = () => {
            console.error('✗ Failed to load Twitter widgets script from CDN')
            window.__twitterWidgetsFailed = true
        }

        document.head.appendChild(script)
        console.log('Twitter script injection initiated')

        return () => {
            // Don't remove script as it's global and may be needed for other instances
        }
    }, [])

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

    // Process Twitter/X embeds
    useEffect(() => {
        if (!playUrl || type !== 'webpage' || (!playUrl.includes('twitter.com') && !playUrl.includes('x.com'))) return
        if (!twitterContainerRef.current) return

        setTwitterLoaded(false)  // Reset on URL change
        console.log('🐦 Twitter URL:', playUrl)
        console.log('🐦 Widget ready:', !!window.twttr)

        let timeoutId = null
        let loadAttempts = 0
        const maxLoadAttempts = 50

        const attemptLoad = () => {
            // Check if widget is available
            if (!window.twttr?.widgets?.load) {
                loadAttempts++
                if (loadAttempts < maxLoadAttempts) {
                    // Wait and retry
                    timeoutId = setTimeout(attemptLoad, 200)
                } else {
                    console.error('🐦 Twitter widget failed to load after max retries')
                    setTwitterLoaded(true)  // Show fallback
                }
                return
            }

            try {
                const container = twitterContainerRef.current
                if (!container) return

                console.log('🐦 Twitter widget available, calling load()...')
                window.twttr.widgets.load(container)
                console.log('✓ 🐦 Twitter widget.load() called successfully')

                // Set a timeout to show fallback if widget doesn't render
                const fallbackTimeout = setTimeout(() => {
                    if (!container.querySelector('iframe')) {
                        console.warn('🐦 Twitter widget did not render iframe, showing fallback')
                        setTwitterLoaded(true)
                    }
                }, 5000)

                return () => clearTimeout(fallbackTimeout)
            } catch (err) {
                console.error('🐦 Error calling twitter widget load:', err)
                setTwitterLoaded(true)  // Show fallback
            }
        }

        // Start loading with initial delay
        timeoutId = setTimeout(attemptLoad, 300)

        return () => {
            if (timeoutId) clearTimeout(timeoutId)
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

    // For webpage embeds (Twitter, TikTok), use iframe or embedded blockquote
    if (type === 'webpage') {
        // Twitter/X embeds - use blockquote with Twitter's embed script
        if (playUrl && (playUrl.includes('twitter.com') || playUrl.includes('x.com'))) {
            return (
                <div
                    className="player-wrapper"
                    ref={twitterContainerRef}
                    style={{
                        minHeight: '400px',
                        display: 'flex',
                        justifyContent: 'center',
                        alignItems: 'center',
                        padding: '20px',
                        backgroundColor: '#f5f5f5'
                    }}
                >
                    {twitterLoaded && (
                        <div style={{
                            textAlign: 'center',
                            padding: '40px 20px'
                        }}>
                            <p style={{ marginBottom: '20px', color: '#666' }}>
                                Twitter widget couldn't load. Open the tweet here:
                            </p>
                            <a
                                href={playUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                style={{
                                    display: 'inline-block',
                                    padding: '12px 24px',
                                    backgroundColor: '#1DA1F2',
                                    color: 'white',
                                    textDecoration: 'none',
                                    borderRadius: '24px',
                                    fontWeight: 'bold',
                                    fontSize: '16px'
                                }}
                            >
                                📱 Open on Twitter/X
                            </a>
                        </div>
                    )}
                    {!twitterLoaded && (
                        <blockquote
                            className="twitter-tweet"
                            data-width="550"
                            data-dnt="true"
                            data-theme="light"
                        >
                            <a href={playUrl}>Loading tweet...</a>
                        </blockquote>
                    )}
                </div>
            )
        }
        // TikTok embeds need special iframe handling
        if (playUrl && playUrl.includes('tiktok.com')) {
            return (
                <div className="player-wrapper">
                    <iframe
                        src={playUrl}
                        width="100%"
                        height="100%"
                        frameBorder="0"
                        allow="autoplay; encrypted-media"
                        allowFullScreen
                        style={{ border: 'none' }}
                    />
                </div>
            )
        }
        // All other webpage embeds use ReactPlayer
        return (
            <div className="player-wrapper">
                <ReactPlayer
                    url={playUrl}
                    playing={playing}
                    controls
                    width="100%"
                    height="100%"
                    onProgress={onProgress}
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


