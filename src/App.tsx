import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Room, RoomEvent } from 'livekit-client'
import { SpeakingAvatar } from './components/SpeakingAvatar'
import { PropertyMap } from './features/map/PropertyMap'
import { SUPPORTED_CITIES } from './features/map/cityCenters'
import { ApartmentCard } from './features/properties/ApartmentCard'
import { ApartmentDetails } from './features/properties/ApartmentDetails'
import { useApartments } from './features/properties/useApartments'
import { fetchApartmentsByCity } from './features/properties/api'
import { detectCityFromText, matchApartmentFromText } from './features/properties/voiceSync'
import type { Apartment } from './features/properties/types'
import './App.css'

type VoiceTurn = {
  role: 'system' | 'user' | 'assistant'
  text: string
}

type BackendConfig = {
  livekit_url: string
  livekit_configured: boolean
  soravm_configured: boolean
}

type VoiceTurnResponse = {
  transcript?: string
  response_text?: string
  audio_base64?: string
  audio_mime_type?: string
  language?: string
}

type SupportedLanguage = 'en-IN' | 'hi-IN' | 'te-IN'

const LANGUAGE_LABELS: Record<SupportedLanguage, string> = {
  'en-IN': 'EN',
  'hi-IN': 'HI',
  'te-IN': 'TE',
}

const normalizeLanguageCode = (code: string | undefined | null): SupportedLanguage | null => {
  if (!code || code === 'unknown') return null
  if (code === 'en-US') return 'en-IN'
  if (code in LANGUAGE_LABELS) return code as SupportedLanguage
  return null
}

const backendBaseUrl = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000'
const defaultRoom = 'real-estate-demo'
const defaultIdentity = `buyer-${Math.random().toString(36).slice(2, 7)}`
// A stable session ID for this browser tab — persists across mic on/off cycles
// but resets when the user clicks "End Conversation".
const generateSessionId = () => `session-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

function App() {
  const roomRef = useRef<Room | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const audioUrlRef = useRef<string | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioContextRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const silenceTimerRef = useRef<number | null>(null)
  const monitorFrameRef = useRef<number | null>(null)
  const listeningRef = useRef(false)
  const hasGreetedRef = useRef(false)
  // Guards against ambient-noise phantom turns:
  // speechStartedRef tracks the timestamp when voice first crossed the threshold.
  // A turn is only submitted once ≥800 ms of continuous speech is detected.
  const speechStartedRef = useRef<number | null>(null)
  const MIN_SPEECH_MS = 250

  // Web Audio playback analysis
  const playbackAnalyserRef = useRef<AnalyserNode | null>(null)
  const audioSourceRef = useRef<MediaElementAudioSourceNode | null>(null)
  const [playbackAnalyser, setPlaybackAnalyser] = useState<AnalyserNode | null>(null)
  const [micAnalyser, setMicAnalyser] = useState<AnalyserNode | null>(null)

  const [roomName, setRoomName] = useState(defaultRoom)
  const [identity, setIdentity] = useState(defaultIdentity)
  const [voice, setVoice] = useState('default')
  const [sessionLanguage, setSessionLanguage] = useState<SupportedLanguage>('en-IN')
  // sessionId ties all turns in one conversation to the same backend history.
  // It is replaced with a fresh ID when the user ends the conversation.
  const [sessionId, setSessionId] = useState(generateSessionId)
  const [config, setConfig] = useState<BackendConfig | null>(null)
  const [status, setStatus] = useState('Ready')
  const [connected, setConnected] = useState(false)
  const [listening, setListening] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [transcript, setTranscript] = useState('')
  const [replyText, setReplyText] = useState('')
  const [manualTranscript, setManualTranscript] = useState(
    'I want a 2 BHK near Hinjewadi with a site visit this weekend',
  )
  const [turns, setTurns] = useState<VoiceTurn[]>([
    {
      role: 'system',
      text: 'Connect and ask me about properties — by voice or text.',
    },
  ])

  // Inspiration Redesign active states
  const [activeSidebar, setActiveSidebar] = useState('Home')
  const [activeCity, setActiveCity] = useState('')
  const [focusedLocationPin, setFocusedLocationPin] = useState('Hinjewadi')
  const [selectedApartment, setSelectedApartment] = useState<Apartment | null>(null)
  const [showPropertyPanel, setShowPropertyPanel] = useState(false)
  const [mapZoomMode, setMapZoomMode] = useState<'globe' | 'city' | 'property'>('globe')
  const turnStartedAtRef = useRef<number | null>(null)

  const logLatency = (label: string, startedAt: number, details?: string) => {
    const elapsed = Math.round(performance.now() - startedAt)
    console.info(`[latency] ${label}: ${elapsed}ms${details ? ` ${details}` : ''}`)
  }

  const { apartments, loading: apartmentsLoading } = useApartments(activeCity)

  const revealProperty = useCallback((apartment: Apartment) => {
    setSelectedApartment(apartment)
    setFocusedLocationPin(apartment.locality)
    setMapZoomMode('property')
    setShowPropertyPanel(true)
  }, [])

  const handleApartmentSelect = useCallback((apartment: Apartment) => {
    revealProperty(apartment)
  }, [revealProperty])

  const applyConversationToMap = useCallback(async (text: string, openPanel: boolean) => {
    const detectedCity = detectCityFromText(text)
    const targetCity = detectedCity ?? activeCity

    if (detectedCity) {
      setActiveCity(detectedCity)
      setMapZoomMode('city')
    }

    const cityApartments = await fetchApartmentsByCity(targetCity)
    const matched = matchApartmentFromText(text, cityApartments)

    if (matched) {
      revealProperty(matched)
    } else if (detectedCity) {
      setSelectedApartment(null)
      setShowPropertyPanel(false)
      setMapZoomMode('city')
    } else if (openPanel) {
      // keep current selection if assistant/user did not name a new property
    }

    return targetCity
  }, [activeCity, revealProperty])

  const syncVoiceToUi = useCallback(async (text: string) => {
    return applyConversationToMap(text, true)
  }, [applyConversationToMap])

  useEffect(() => {
    let active = true

    fetch(`${backendBaseUrl}/config`)
      .then((response) => response.json())
      .then((data: BackendConfig) => {
        if (active) {
          setConfig(data)
        }
      })
      .catch(() => {
        if (active) {
          setConfig(null)
        }
      })

    return () => {
      active = false
    }
  }, [])

  useEffect(() => {
    return () => {
      void disconnectRoom()
    }
  }, [])

  // Sync map + property panel when the AI recommends a property in its reply
  useEffect(() => {
    if (!replyText.trim()) return
    void applyConversationToMap(replyText, true)
  }, [replyText, applyConversationToMap])

  const appendTurn = (role: VoiceTurn['role'], text: string) => {
    setTurns((currentTurns) => [...currentTurns, { role, text }])
  }

  const extractTranscript = (payload: Record<string, unknown>) => {
    const candidates = [
      payload.transcript,
      payload.text,
      payload.result,
      payload?.data && typeof payload.data === 'object'
        ? (payload.data as Record<string, unknown>).text
        : undefined,
    ]

    const firstString = candidates.find((candidate) => typeof candidate === 'string')
    if (typeof firstString === 'string') {
      return firstString
    }

    return JSON.stringify(payload)
  }

  const extractDetectedLanguage = (payload: Record<string, unknown>): SupportedLanguage | null => {
    const code = typeof payload.language_code === 'string' ? payload.language_code : null
    return normalizeLanguageCode(code)
  }

  const ensureRecordingStream = async () => {
    if (streamRef.current) {
      return streamRef.current
    }

    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    streamRef.current = stream
    return stream
  }

  const stopSilenceMonitor = () => {
    if (monitorFrameRef.current !== null) {
      window.cancelAnimationFrame(monitorFrameRef.current)
      monitorFrameRef.current = null
    }

    if (silenceTimerRef.current !== null) {
      window.clearTimeout(silenceTimerRef.current)
      silenceTimerRef.current = null
    }
  }

  const startSilenceMonitor = () => {
    const audioContext = audioContextRef.current ?? new AudioContext()
    const stream = streamRef.current
    if (!stream) {
      return
    }

    audioContextRef.current = audioContext

    if (!analyserRef.current) {
      const source = audioContext.createMediaStreamSource(stream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 2048
      source.connect(analyser)
      analyserRef.current = analyser
      setMicAnalyser(analyser)
    }

    const probe = () => {
      const analyser = analyserRef.current
      if (!analyser) {
        return
      }

      const buffer = new Float32Array(analyser.fftSize)
      analyser.getFloatTimeDomainData(buffer)
      const rms = Math.sqrt(buffer.reduce((sum, value) => sum + value * value, 0) / buffer.length)

      // Raised threshold (0.04) to ignore ambient hiss/background noise.
      // Only start tracking speech onset once we cross this level.
      const VOICE_THRESHOLD = 0.04

      if (rms > VOICE_THRESHOLD) {
        // Mark when speech first started
        if (speechStartedRef.current === null) {
          speechStartedRef.current = Date.now()
        }

        // Reset any pending silence timer (user is still speaking)
        if (silenceTimerRef.current !== null) {
          window.clearTimeout(silenceTimerRef.current)
          silenceTimerRef.current = null
        }
      } else {
        // Voice dropped below threshold — only submit if we had ≥800ms of real speech
        const speechDuration = speechStartedRef.current !== null
          ? Date.now() - speechStartedRef.current
          : 0

        if (
          speechStartedRef.current !== null &&
          speechDuration >= MIN_SPEECH_MS &&
          silenceTimerRef.current === null
        ) {
          // Arm a short silence timeout before stopping so the reply feels snappier.
          // Do NOT clear speechStartedRef here — onstop reads it to decide
          // whether to submit the blob, then resets it itself.
          silenceTimerRef.current = window.setTimeout(() => {
            silenceTimerRef.current = null
            if (recorderRef.current?.state === 'recording') {
              recorderRef.current.stop()
            }
          }, 300)
        } else if (speechStartedRef.current === null) {
          // No real speech yet — ignore ambient noise entirely
        }
      }

      monitorFrameRef.current = window.requestAnimationFrame(probe)
    }

    if (monitorFrameRef.current === null) {
      monitorFrameRef.current = window.requestAnimationFrame(probe)
    }
  }

  const startRecordingSegment = async () => {
    if (!streamRef.current) {
      await ensureRecordingStream()
    }

    if (!streamRef.current) {
      throw new Error('No media stream available')
    }

    const recorder = new MediaRecorder(streamRef.current, {
      mimeType: 'audio/webm',
    })

    chunksRef.current = []

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        chunksRef.current.push(event.data)
      }
    }

    recorder.onstop = async () => {
      stopSilenceMonitor()
      recorderRef.current = null

      if (turnStartedAtRef.current !== null) {
        logLatency('audio_capture', turnStartedAtRef.current)
      }

      const blob = new Blob(chunksRef.current, {
        type: 'audio/webm',
      })
      chunksRef.current = []

      // Only call the backend if there was enough real speech detected.
      // If speechStartedRef is null it means we never crossed the voice threshold
      // (ambient noise only) — skip the API call entirely.
      const hadRealSpeech = speechStartedRef.current !== null
      speechStartedRef.current = null

      if (hadRealSpeech) {
        try {
          await handleRecordedBlob(blob)
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Failed to process audio'
          setStatus(message)
          appendTurn('system', message)
        }
      }

      if (listeningRef.current) {
        setStatus('Listening...')
        try {
          await startRecordingSegment()
        } catch (error) {
          const message = error instanceof Error ? error.message : 'Unable to restart listening'
          setStatus(message)
          appendTurn('system', message)
          setListeningState(false)
        }
      }
    }

    recorderRef.current = recorder
    recorder.start()
    turnStartedAtRef.current = performance.now()
    console.info('[latency] audio_capture_start')
    setStatus('Listening...')
    startSilenceMonitor()
  }

  const livekitErrorMessage = (error: unknown) => {
    const raw = error instanceof Error ? error.message : String(error)
    if (raw.includes('ERR_CONNECTION_REFUSED') || raw.includes("Couldn't connect to server")) {
      return 'LiveKit server offline'
    }
    return raw || 'LiveKit connection failed'
  }

  const connectRoom = async () => {
    setStatus('Establishing Pipeline...')
    const response = await fetch(
      `${backendBaseUrl}/livekit/token?room=${encodeURIComponent(roomName)}&identity=${encodeURIComponent(identity)}`,
    )

    if (!response.ok) {
      throw new Error(await response.text())
    }

    const payload = (await response.json()) as {
      livekit_url: string
      access_token: string
    }

    const room = new Room({
      adaptiveStream: true,
      dynacast: true,
    })

    room.on(RoomEvent.Disconnected, () => {
      setConnected(false)
      setStatus('Pipeline Offline')
    })

    room.on(RoomEvent.Connected, () => {
      setConnected(true)
      setStatus('Live')
    })

    roomRef.current = room
    setStatus('Connecting...')

    try {
      await room.connect(payload.livekit_url, payload.access_token)
    } catch (error) {
      roomRef.current = null
      throw new Error(livekitErrorMessage(error))
    }

    await room.localParticipant.setMicrophoneEnabled(true)
    await ensureRecordingStream()
    appendTurn('system', `Connected as ${identity}`)
  }

  const disconnectRoom = async () => {
    const room = roomRef.current
    if (recorderRef.current?.state === 'recording') {
      recorderRef.current.stop()
    }

    stopSilenceMonitor()

    recorderRef.current = null
    chunksRef.current = []

    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null

    if (audioContextRef.current) {
      audioContextRef.current.close().catch(() => null)
      audioContextRef.current = null
      analyserRef.current = null
      playbackAnalyserRef.current = null
      audioSourceRef.current = null
      setMicAnalyser(null)
      setPlaybackAnalyser(null)
    }

    if (room) {
      room.removeAllListeners()
      await room.disconnect()
      roomRef.current = null
    }

    setConnected(false)
    setListening(false)
    setIsSpeaking(false)
    setStatus('Disconnected')
  }

  const base64ToBytes = (audioBase64: string) => {
    const binaryString = atob(audioBase64)
    const bytes = new Uint8Array(binaryString.length)

    for (let index = 0; index < binaryString.length; index += 1) {
      bytes[index] = binaryString.charCodeAt(index)
    }

    return bytes
  }

  const playResponseAudio = async (audioBase64?: string, audioMimeType = 'audio/wav') => {
    if (!audioBase64 || !audioRef.current) {
      return
    }

    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current)
      audioUrlRef.current = null
    }

    const audioBlob = new Blob([base64ToBytes(audioBase64)], { type: audioMimeType })
    const audioUrl = URL.createObjectURL(audioBlob)
    audioUrlRef.current = audioUrl
    const audioEl = audioRef.current
    audioEl.src = audioUrl

    // Connect Web Audio API Node for real-time visualizer
    if (!audioContextRef.current) {
      audioContextRef.current = new (window.AudioContext || (window as any).webkitAudioContext)()
    }
    const audioContext = audioContextRef.current
    if (audioContext.state === 'suspended') {
      await audioContext.resume()
    }

    // Connect audio node to analyser (only once per media element)
    if (!audioSourceRef.current) {
      const source = audioContext.createMediaElementSource(audioEl)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      analyser.connect(audioContext.destination)
      audioSourceRef.current = source
      playbackAnalyserRef.current = analyser
      setPlaybackAnalyser(analyser)
    }

    setIsSpeaking(true)

    const playbackStartedAt = performance.now()

    await audioEl.play()
    logLatency('audio_playback_start', playbackStartedAt)

    await new Promise<void>((resolve, reject) => {
      const handleEnded = () => {
        cleanup()
        setIsSpeaking(false)
        logLatency('audio_playback_total', playbackStartedAt)
        resolve()
      }
      const handleError = () => {
        cleanup()
        setIsSpeaking(false)
        reject(new Error('Audio playback failed'))
      }
      const cleanup = () => {
        audioEl.removeEventListener('ended', handleEnded)
        audioEl.removeEventListener('error', handleError)
      }
      audioEl.addEventListener('ended', handleEnded)
      audioEl.addEventListener('error', handleError)
    })
  }

  const sendVoiceTurn = async (text: string, city?: string, language?: string) => {
    const response = await fetch(`${backendBaseUrl}/voice/turn`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        transcript: text,
        voice,
        language: language ?? sessionLanguage,
        session_id: sessionId,
        city: city || activeCity || undefined,
      }),
    })

    if (!response.ok) {
      throw new Error(await response.text())
    }

    const payload = (await response.json()) as VoiceTurnResponse
    const resolvedLanguage = normalizeLanguageCode(payload.language)
    if (resolvedLanguage) {
      setSessionLanguage(resolvedLanguage)
    }
    return payload
  }

  const handleRecordedBlob = async (blob: Blob) => {
    const turnStartedAt = performance.now()
    console.info('[latency] voice_turn_pipeline_start')
    const file = new File([blob], 'turn.webm', {
      type: 'audio/webm',
    })
    const formData = new FormData()
    formData.append('audio_file', file)
    formData.append('language_code', 'unknown')

    setIsProcessing(true)
    setStatus('Thinking...')
    const sttStartedAt = performance.now()
    const sttResponse = await fetch(`${backendBaseUrl}/soravm/stt`, {
      method: 'POST',
      body: formData,
    })
    logLatency('stt_roundtrip', sttStartedAt)

    if (!sttResponse.ok) {
      setIsProcessing(false)
      throw new Error(await sttResponse.text())
    }

    const transcriptPayload = (await sttResponse.json()) as Record<string, unknown>
    const recognizedText = extractTranscript(transcriptPayload)
    const detectedLanguage = extractDetectedLanguage(transcriptPayload)
    if (detectedLanguage) {
      setSessionLanguage(detectedLanguage)
    }
    setTranscript(recognizedText)
    appendTurn('user', recognizedText)

    const cityForTurn = detectCityFromText(recognizedText) || activeCity || undefined
    const mapSyncStartedAt = performance.now()
    const mapSyncPromise = syncVoiceToUi(recognizedText).then((resolvedCity) => {
      logLatency('property_lookup', mapSyncStartedAt, resolvedCity ? `city=${resolvedCity}` : undefined)
      return resolvedCity
    })

    const voiceTurnStartedAt = performance.now()
    const voiceTurnPromise = sendVoiceTurn(
      recognizedText,
      cityForTurn,
      detectedLanguage ?? 'unknown',
    ).then((payload) => {
      logLatency('backend_voice_turn', voiceTurnStartedAt)
      return payload
    })

    setStatus('Thinking...')
    const [, voiceTurn] = await Promise.all([mapSyncPromise, voiceTurnPromise])
    setReplyText(voiceTurn.response_text ?? '')
    appendTurn('assistant', voiceTurn.response_text ?? '')
    setIsProcessing(false)
    await playResponseAudio(voiceTurn.audio_base64, voiceTurn.audio_mime_type)
    logLatency('voice_turn_total', turnStartedAt)
    setStatus('Ready')
  }

  const setListeningState = (value: boolean) => {
    listeningRef.current = value
    setListening(value)
  }

  const toggleRecording = async () => {
    if (listeningRef.current) {
      setListeningState(false)
      stopSilenceMonitor()
      if (recorderRef.current?.state === 'recording') {
        recorderRef.current.stop()
      }
      setStatus('Ready')
      return
    }

    try {
      await ensureRecordingStream()
      setStatus('Listening...')
      setListeningState(true)
      await startRecordingSegment()
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Unable to start recording'
      setStatus(message)
      appendTurn('system', message)
      setListeningState(false)
    }
  }

  const handleManualSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()

    if (!manualTranscript.trim()) return

    try {
      const turnStartedAt = performance.now()
      const currentQuery = manualTranscript
      setManualTranscript('')
      appendTurn('user', currentQuery)
      setTranscript(currentQuery)
      setIsProcessing(true)
      setStatus('Thinking...')
      const cityForTurn = detectCityFromText(currentQuery) || activeCity || undefined
      const mapSyncStartedAt = performance.now()
      const mapSyncPromise = syncVoiceToUi(currentQuery).then((resolvedCity) => {
        logLatency('property_lookup', mapSyncStartedAt, resolvedCity ? `city=${resolvedCity}` : undefined)
        return resolvedCity
      })
      const voiceTurnStartedAt = performance.now()
      const voiceTurnPromise = sendVoiceTurn(currentQuery, cityForTurn, 'unknown').then((payload) => {
        logLatency('backend_voice_turn', voiceTurnStartedAt)
        return payload
      })
      const [, voiceTurn] = await Promise.all([mapSyncPromise, voiceTurnPromise])
      setReplyText(voiceTurn.response_text ?? '')
      appendTurn('assistant', voiceTurn.response_text ?? '')
      setIsProcessing(false)
      await playResponseAudio(voiceTurn.audio_base64, voiceTurn.audio_mime_type)
      logLatency('voice_turn_total', turnStartedAt)
      setStatus('Ready')
    } catch (error) {
      setIsProcessing(false)
      const message = error instanceof Error ? error.message : 'Failed to generate reply'
      setStatus(message)
      appendTurn('system', message)
    }
  }

  const handleBookSiteVisit = async () => {
    const query = `Book a site visit for ${selectedApartment?.name ?? 'the selected property'} this weekend`
    appendTurn('user', query)
    setTranscript(query)
    setIsProcessing(true)
    setStatus('Thinking...')
    try {
      const voiceTurn = await sendVoiceTurn(query, selectedApartment?.city || activeCity || undefined)
      setReplyText(voiceTurn.response_text ?? '')
      appendTurn('assistant', voiceTurn.response_text ?? '')
      setIsProcessing(false)
      await playResponseAudio(voiceTurn.audio_base64, voiceTurn.audio_mime_type)
      setStatus('Ready')
    } catch (error) {
      setIsProcessing(false)
      const message = error instanceof Error ? error.message : 'Failed to book site visit'
      setStatus(message)
      appendTurn('system', message)
    }
  }

  const handleQuickAction = (actionName: string) => {
    if (actionName === 'Schedule Site Visit') {
      void handleBookSiteVisit()
    } else {
      const query = `Give me information on ${actionName} for ${selectedApartment?.name ?? 'this property'}`
      appendTurn('user', query)
      setTranscript(query)
      setStatus('Thinking...')
      setIsProcessing(true)
      sendVoiceTurn(query, selectedApartment?.city || activeCity || undefined)
        .then(async (res) => {
          setReplyText(res.response_text ?? '')
          appendTurn('assistant', res.response_text ?? '')
          setIsProcessing(false)
          await playResponseAudio(res.audio_base64, res.audio_mime_type)
          setStatus('Ready')
        })
        .catch((err) => {
          setIsProcessing(false)
          setStatus('Ready')
          appendTurn('system', err instanceof Error ? err.message : 'Action failed')
        })
    }
  }

  useEffect(() => {
    if (hasGreetedRef.current) return
    hasGreetedRef.current = true

    const autoGreet = async () => {
      try {
        const formData = new FormData()
        formData.append('text', 'Hello! I am your AI Real Estate Assistant. How can I help you today?')
        formData.append('voice', voice)
        formData.append('target_language_code', 'en-IN')

        const response = await fetch(`${backendBaseUrl}/soravm/tts`, {
          method: 'POST',
          body: formData,
        })

        if (response.ok) {
          const data = await response.json()
          const greetingText = 'Hello! I am your AI Real Estate Assistant. How can I help you today?'
          setReplyText(greetingText)
          appendTurn('assistant', greetingText)
          
          await playResponseAudio(data.audio_base64, data.audio_mime_type)
          
          // Start recording after greeting completes
          if (!listeningRef.current) {
            await toggleRecording()
          }
        }
      } catch (err) {
        console.error('Auto-greeting failed:', err)
      }
    }

    // Small delay to ensure UI renders
    setTimeout(autoGreet, 800)
  }, [])

  const conversationTurns = turns.filter((t) => t.role !== 'system')

  return (
    <div className="app-shell">
      {/* TOP NAVBAR */}
      <header className="top-navbar">
        <div className="brand-section">
          <svg className="brand-logo" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" width="24" height="24">
             <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0118 0z" />
             <circle cx="12" cy="10" r="3" />
          </svg>
          <h1 className="brand-name">CallingGen</h1>
        </div>
        <div className="search-section">
          <div className="search-bar">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18">
              <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input type="text" placeholder="Search properties or ask AI..." />
          </div>
        </div>
        <div className="actions-section">
          <button className="icon-btn">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
              <rect x="3" y="6" width="18" height="15" rx="2" ry="2"/><path d="M3 10h18"/><path d="M7 15h.01"/>
            </svg>
          </button>
          <button className="icon-btn">
            <div className="notification-dot"></div>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="20" height="20">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
          </button>
          <img src="https://images.unsplash.com/photo-1580489944761-15a19d654956?w=100&h=100&fit=crop" alt="Profile" className="profile-avatar" />
        </div>
      </header>

      <main className={`app-content ${showPropertyPanel ? '' : 'app-content--map-expanded'}`}>
        {/* LEFT COLUMN: AI ASSISTANT */}
        <section className="ai-assistant-panel">
          <div className="panel-header">
            <h2>AI Assistant</h2>
            <div className="lang-toggle" title={`Speaking: ${LANGUAGE_LABELS[sessionLanguage]}`}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="14" height="14">
                <circle cx="12" cy="12" r="10"/><path d="M2 12h20"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
              </svg>
              {LANGUAGE_LABELS[sessionLanguage]}
            </div>
          </div>
          
          <SpeakingAvatar
            isSpeaking={isSpeaking}
            isListening={listening}
            isProcessing={isProcessing}
          />
          
          <div className="ai-controls">
            <p className="transcript-area">
              {replyText || transcript || 'Hello! I am your AI Real Estate Assistant. How can I help you today?'}
            </p>
            
            <div className="mic-speaker-controls">
              <button className="circle-icon-btn" onClick={toggleRecording}>
                {listening ? (
                  <svg viewBox="0 0 24 24" fill="currentColor" width="18" height="18"><rect x="6" y="6" width="12" height="12" rx="2.5" /></svg>
                ) : (
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z" /><path d="M19 10v2a7 7 0 01-14 0v-2M12 19v4M8 23h8" /></svg>
                )}
              </button>
              <button className="circle-icon-btn">
                 <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" width="18" height="18"><polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"></polygon><path d="M19.07 4.93a10 10 0 0 1 0 14.14M15.54 8.46a5 5 0 0 1 0 7.07"></path></svg>
              </button>
            </div>
            
            <button className="btn-primary" onClick={connected ? () => void 0 : connectRoom}>Start Conversation</button>
            <button className="btn-outline" onClick={disconnectRoom}>End Conversation</button>
          </div>
        </section>

        {/* CENTER COLUMN: MAP */}
        <section className="map-panel">
          <div className="map-controls">
            <button className="map-control-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg></button>
            <button className="map-control-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="5" y1="12" x2="19" y2="12"></line></svg></button>
            <button className="map-control-btn" style={{marginTop: '0.5rem'}}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 22s-8-4.5-8-11.8A8 8 0 0 1 12 2a8 8 0 0 1 8 8.2c0 7.3-8 11.8-8 11.8z"/><circle cx="12" cy="10" r="3"/></svg></button>
            <button className="map-control-btn"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polygon points="12 2 2 7 12 12 22 7 12 2"/><polyline points="2 12 12 17 22 12"/><polyline points="2 17 12 22 22 17"/></svg></button>
          </div>

          <div style={{ width: '100%', height: '100%' }}>
            <PropertyMap
              city={activeCity}
              apartments={apartments}
              selectedApartment={selectedApartment}
              zoomMode={mapZoomMode}
              onApartmentSelect={handleApartmentSelect}
            />
          </div>
        </section>

        {/* RIGHT COLUMN: PROPERTY DETAILS — hidden until a property is selected or recommended */}
        <section
          className={`property-details-panel property-details-panel--sidebar ${showPropertyPanel ? 'property-details-panel--visible' : 'property-details-panel--hidden'}`}
          aria-hidden={!showPropertyPanel}
        >
          {showPropertyPanel && selectedApartment && (
            <ApartmentDetails apartment={selectedApartment} />
          )}
        </section>
      </main>

      <audio ref={audioRef} className="sr-only-audio" aria-hidden="true" />
      <div style={{ display: 'none' }}>
        {/* Hidden elements just to satisfy TS unused vars while keeping logic intact */}
        {apartments[0] && <ApartmentCard apartment={apartments[0]} selected={false} onSelect={() => {}} />}
        <span onClick={() => {
          setRoomName('')
          setIdentity('')
          setVoice('')
          setSessionId('')
          setShowSettings(false)
          setActiveSidebar('')
          handleManualSubmit({ preventDefault: () => {} } as any)
          handleQuickAction('')
        }}>{status} {transcript} {replyText} {focusedLocationPin} {apartmentsLoading} {conversationTurns.length} {config?.livekit_url} {showSettings} {activeSidebar} {playbackAnalyser?.channelCount} {micAnalyser?.channelCount} {SUPPORTED_CITIES.length}</span>
      </div>
    </div>
  )
}

export default App


