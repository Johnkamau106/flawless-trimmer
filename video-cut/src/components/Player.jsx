import ReactPlayer from 'react-player'

export default function Player({ url, playing, onProgress, onDuration, seekTo }) {
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
                onReady={() => { if (seekTo != null) {/* noop to trigger render */ } }}
            />
        </div>
    )
}


