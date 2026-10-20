import { useEffect, useRef, useState } from 'react'

export default function AudioRecorder() {
  const [status, setStatus] = useState<'idle' | 'starting' | 'recording' | 'stopping'>('idle')
  const [audioUrl, setAudioUrl] = useState('')
  const [error, setError] = useState('')
  const [transcript, setTranscript] = useState('')
  const [partial, setPartial] = useState('')
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioUrlRef = useRef('')
  const mountedRef = useRef(false)
  const disconnectRef = useRef<() => void>(() => {})

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      disconnectRef.current()
      const recorder = recorderRef.current
      if (recorder) {
        recorder.onstart = null
        recorder.ondataavailable = null
        recorder.onstop = null
        recorder.onerror = null
      }
      if (recorder && recorder.state !== 'inactive') recorder.stop()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      URL.revokeObjectURL(audioUrlRef.current)
    }
  }, [])

  async function startRecording() {
    setError('')
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setError('Recording requires a supported browser on localhost or HTTPS.')
      return
    }

    setStatus('starting')
    setTranscript('')
    setPartial('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      if (!mountedRef.current) {
        stream.getTracks().forEach((track) => track.stop())
        return
      }
      streamRef.current = stream
      const recorder = new MediaRecorder(stream)
      recorderRef.current = recorder
      const chunks: Blob[] = []
      // Vite proxies this path to the local backend during development.
      const endpoint = new URL(
        import.meta.env.VITE_TRANSCRIPTION_WS_URL || '/tranws/scribe',
        window.location.href,
      )
      if (endpoint.protocol === 'http:') endpoint.protocol = 'ws:'
      if (endpoint.protocol === 'https:') endpoint.protocol = 'wss:'
      const socket = new WebSocket(endpoint)
      let failed = false
      let stopping = false
      let timer: ReturnType<typeof setTimeout>

      function disconnect() {
        clearTimeout(timer)
        socket.onopen = null
        socket.onmessage = null
        socket.onerror = null
        socket.onclose = null
        socket.close()
      }
      disconnectRef.current = disconnect

      function fail(message: string) {
        if (failed) return
        failed = true
        disconnect()
        setError(message)
        setPartial('')
        if (recorder.state !== 'inactive') {
          setStatus('stopping')
          recorder.stop()
        } else {
          setStatus('idle')
        }
        stream.getTracks().forEach((track) => track.stop())
      }

      timer = setTimeout(() => fail('The transcription server did not connect. Please try again.'), 10_000)
      socket.onopen = () => {
        clearTimeout(timer)
        try {
          // Chunks belong to one continuous recording, not separate audio files.
          recorder.start(250)
        } catch {
          fail('Could not start streaming audio. Please try again.')
        }
      }
      socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data)
          if (message.type === 'transcript' && typeof message.text === 'string'
            && typeof message.isFinal === 'boolean') {
            if (message.isFinal) {
              setTranscript((previous) => [previous, message.text].filter(Boolean).join(' '))
              setPartial('')
            } else {
              setPartial(message.text)
            }
          } else if (message.type === 'done' && stopping) {
            disconnect()
            setPartial('')
            setStatus('idle')
          } else if (message.type === 'error') {
            fail(typeof message.message === 'string' ? message.message : 'Transcription failed.')
          } else {
            fail('The transcription server sent an unexpected message.')
          }
        } catch {
          fail('The transcription server sent an invalid message.')
        }
      }
      socket.onerror = () => fail('Cannot connect to the transcription server. Check that the backend is running.')
      socket.onclose = () => fail('The transcription connection closed before it finished. Please try again.')

      recorder.onstart = () => {
        if (failed || !mountedRef.current) return
        try {
          // The browser has selected its actual format by the start event.
          // Send this control message before the first dataavailable event.
          socket.send(JSON.stringify({ type: 'start', mimeType: recorder.mimeType }))
          setStatus('recording')
        } catch {
          fail('Could not start streaming audio. Please try again.')
        }
      }
      recorder.ondataavailable = (event) => {
        if (event.data.size === 0) return
        chunks.push(event.data)
        if (!failed && socket.readyState === WebSocket.OPEN) {
          // Stop instead of accumulating unbounded audio on a slow connection.
          if (socket.bufferedAmount > 1024 * 1024) {
            fail('The connection is too slow to stream audio. Please try again.')
          } else {
            socket.send(event.data)
          }
        }
      }
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop())
        if (!mountedRef.current) return

        if (chunks.length > 0) {
          const audio = new Blob(chunks, { type: recorder.mimeType })
          URL.revokeObjectURL(audioUrlRef.current)
          audioUrlRef.current = URL.createObjectURL(audio)
          setAudioUrl(audioUrlRef.current)
        } else if (!failed) {
          setError('No audio was captured. Please try again.')
        }
        if (failed) {
          setStatus('idle')
        } else if (socket.readyState === WebSocket.OPEN) {
          stopping = true
          // The final dataavailable event is sent before this end marker.
          socket.send(JSON.stringify({ type: 'stop' }))
          timer = setTimeout(() => {
            fail('Timed out waiting for the final transcript.')
            setStatus('idle')
          }, 30_000)
        } else {
          fail('The transcription connection is no longer available.')
        }
      }
      recorder.onerror = () => {
        fail('Recording failed. Please try again.')
      }
    } catch {
      disconnectRef.current()
      streamRef.current?.getTracks().forEach((track) => track.stop())
      if (mountedRef.current) {
        setError('Could not access your microphone. Check your browser permissions and try again.')
        setStatus('idle')
      }
    }
  }

  function stopRecording() {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') {
      setStatus('stopping')
      recorder.stop()
    }
  }

  return (
    <section className="mx-auto max-w-lg space-y-4 p-6">
      <h1 className="text-2xl font-semibold">Record your meeting</h1>
      <div className="flex gap-3">
        <button
          className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
          onClick={startRecording}
          disabled={status !== 'idle'}
        >
          Start recording
        </button>
        <button
          className="rounded border px-4 py-2 disabled:opacity-50"
          onClick={stopRecording}
          disabled={status !== 'recording'}
        >
          Stop recording
        </button>
      </div>
      <p role="status">
        {status === 'starting' ? 'Connecting microphone and transcription server…'
          : status === 'recording' ? 'Recording…'
          : status === 'stopping' ? 'Finishing transcript…'
          : 'Ready to record'}
      </p>
      {error && <p role="alert" className="text-red-700">{error}</p>}
      <div className="space-y-2 rounded border border-gray-300 p-4">
        <h2 className="text-lg font-semibold">Live transcript</h2>
        <p aria-live="polite" aria-atomic="true" className="whitespace-pre-wrap">
          {transcript}{transcript && partial ? ' ' : ''}
          <span className="text-gray-500">{partial}</span>
          {!transcript && !partial && 'Your words will appear here when the transcription server is connected.'}
        </p>
      </div>
      {audioUrl && (
        <div className="space-y-2">
          <h2 className="text-lg font-semibold">Your recording</h2>
          <audio className="w-full" key={audioUrl} controls src={audioUrl} aria-label="Your meeting recording" />
        </div>
      )}
    </section>
  )
}
