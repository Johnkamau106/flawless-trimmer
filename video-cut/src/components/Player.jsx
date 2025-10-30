import ReactPlayer from 'react-player'

export default function Player({ url, playing, onProgress, onDuration }) {
    const canPlay = ReactPlayer.canPlay(url)
    const fbAppId = import.meta.env.VITE_FACEBOOK_APP_ID

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
                url={url}
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


