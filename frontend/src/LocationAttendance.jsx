import { useEffect, useRef, useState } from 'react'
import { Camera, Check, MapPin, SwitchCamera, X } from 'lucide-react'
import api from './services/api'
import EmployeeAttendanceUndo from './EmployeeAttendanceUndo'
import { formatIndiaDate, formatKolkataDate, formatKolkataTime, kolkataDateKey } from './time'

function formatLocation(location) {
  if (!location || location.latitude == null || location.longitude == null) return 'Location unavailable'
  if (location.display_name) return location.display_name
  if (location.area) return location.city && !location.area.includes(location.city) ? `${location.area}, ${location.city}` : location.area
  return 'Area unavailable'
}

function accuracyLabel(accuracy) {
  if (accuracy == null) return 'Accuracy unavailable'
  if (accuracy <= 30) return `High accuracy (~${Math.round(accuracy)}m)`
  if (accuracy <= 100) return `Approximate location (~${Math.round(accuracy)}m)`
  return `Low accuracy (~${Math.round(accuracy)}m)`
}

function LocationDetails({ location }) {
  return <div className="location-details"><b>{formatLocation(location)}</b>{location?.accuracy != null && <small>{accuracyLabel(location.accuracy)}</small>}</div>
}

function detectDevice() {
  const userAgent = navigator.userAgent || ''
  if (/iPad|Tablet|Android/i.test(userAgent)) return 'Mobile / tablet'
  return 'Laptop / desktop'
}

function detectBrowser() {
  const userAgent = navigator.userAgent || ''
  if (/Edg\//i.test(userAgent)) return 'Edge'
  if (/Firefox\//i.test(userAgent)) return 'Firefox'
  if (/CriOS\//i.test(userAgent)) return 'Chrome on iOS'
  if (/Chrome\//i.test(userAgent)) return 'Chrome'
  if (/Safari\//i.test(userAgent) && !/Chrome\//i.test(userAgent)) return 'Safari'
  return 'Unknown browser'
}

function statusBadgeClass(status) {
  return status === 'ABSENT' ? 'badge absent' : 'badge verified'
}

function locationFailureMessage(error) {
  if (!window.isSecureContext) return 'Location requires localhost or HTTPS. Open the app on localhost/127.0.0.1 or serve it over HTTPS.'
  if (!navigator.geolocation) return 'This browser does not support location capture.'
  if (error?.code === 1) return 'Location permission was denied. Allow location access in the browser, then try again.'
  if (error?.code === 2) return 'The browser could not determine your location. Check device location services and try again.'
  if (error?.code === 3) return 'Location capture timed out. Try again, or move near a window for a better signal.'
  return 'Location unavailable.'
}

function requestFailureMessage(error) {
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(item => item.msg).join(' ')
  if (detail) return detail
  if (error.code === 'ERR_NETWORK') return 'Could not reach the attendance API. Confirm that the backend is running, then try again.'
  return error.message || 'Attendance could not be recorded.'
}

function cameraFailureMessage(error) {
  if (!window.isSecureContext) return 'Camera requires localhost or HTTPS. Open the app on localhost/127.0.0.1 or serve it over HTTPS.'
  if (!navigator.mediaDevices?.getUserMedia) return 'This browser does not support an in-page camera.'
  if (error?.name === 'NotAllowedError' || error?.name === 'PermissionDeniedError') return 'Camera permission was denied. Allow camera access in the browser, then try again.'
  if (error?.name === 'NotFoundError' || error?.name === 'OverconstrainedError' || error?.name === 'NotReadableError') return 'No camera is available, or it is already in use. Check device camera access and try again.'
  if (error?.name === 'SecurityError' || error?.message === 'unsupported') return 'Camera requires localhost or HTTPS.'
  return 'Camera unavailable. Allow camera access and try again.'
}

function stopMediaStream(stream) {
  stream?.getTracks?.().forEach(track => track.stop())
}

export default function LocationAttendance() {
  const [history, setHistory] = useState([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [locationDiagnostics, setLocationDiagnostics] = useState(null)
  const [cameraPhase, setCameraPhase] = useState('idle')
  const [cameraSession, setCameraSession] = useState(0)
  const [cameraFacingMode, setCameraFacingMode] = useState('environment')
  const [photoFile, setPhotoFile] = useState(null)
  const [photoPreview, setPhotoPreview] = useState('')
  const videoRef = useRef(null)
  const streamRef = useRef(null)
  const photoPreviewRef = useRef('')
  const today = kolkataDateKey()
  const todayRecord = history.find(item => item.date === today)
  const action = todayRecord?.check_in_time && !todayRecord?.check_out_time ? 'check_out' : 'check_in'
  const actionLabel = action === 'check_in' ? 'Check In' : 'Check Out'

  async function loadHistory() {
    const { data } = await api.get('/api/attendance/mine')
    setHistory(data)
    return data
  }

  useEffect(() => { loadHistory().catch(() => setError('Could not load attendance history.')) }, [])

  useEffect(() => () => {
    stopMediaStream(streamRef.current)
    streamRef.current = null
    if (photoPreviewRef.current) URL.revokeObjectURL(photoPreviewRef.current)
  }, [])

  useEffect(() => {
    if (cameraPhase !== 'live') return undefined
    let cancelled = false
    async function startCamera() {
      try {
        if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error('unsupported'), { name: 'SecurityError' })
        if (!window.isSecureContext) throw Object.assign(new Error('insecure'), { name: 'SecurityError' })
        let stream
        let usedFallback = false
        try {
          stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { exact: cameraFacingMode } }, audio: false })
        } catch (firstError) {
          if (firstError?.name === 'NotAllowedError' || firstError?.name === 'PermissionDeniedError') throw firstError
          stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false })
          usedFallback = true
        }
        if (cancelled) {
          stopMediaStream(stream)
          return
        }
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play()
        }
        if (usedFallback) {
          const requestedCamera = cameraFacingMode === 'environment' ? 'rear' : 'front'
          setError(`The ${requestedCamera} camera is unavailable. Using the default camera instead.`)
        }
      } catch (cameraError) {
        if (cancelled) return
        setCameraPhase('idle')
        setError(cameraFailureMessage(cameraError))
      }
    }
    startCamera()
    return () => {
      cancelled = true
      stopMediaStream(streamRef.current)
      streamRef.current = null
      if (videoRef.current) videoRef.current.srcObject = null
    }
  }, [cameraPhase, cameraSession, cameraFacingMode])

  async function getLocationOnce(onStatus) {
    const diagnosticBase = { device: detectDevice(), browser: detectBrowser(), secureContext: window.isSecureContext, permission: 'unknown', readings: [], selected: null, watchDurationMs: 0 }
    let permission = 'unknown'
    try {
      permission = (await navigator.permissions?.query({ name: 'geolocation' }))?.state || 'unknown'
    } catch {
      permission = 'unknown'
    }
    diagnosticBase.permission = permission
    setLocationDiagnostics(diagnosticBase)
    if (!navigator.geolocation || !window.isSecureContext) {
      return { location: { latitude: null, longitude: null, accuracy: null, source: 'browser' }, warning: locationFailureMessage(), reason: 'unavailable' }
    }
    return new Promise(resolve => {
      const startedAt = Date.now()
      let bestPosition = null
      let watchId = null
      let timerId = null
      let settled = false
      const finish = (locationError = null) => {
        if (settled) return
        settled = true
        if (watchId != null) navigator.geolocation.clearWatch(watchId)
        if (timerId != null) window.clearTimeout(timerId)
        if (bestPosition) {
          const { latitude, longitude, accuracy } = bestPosition.coords
          const selected = { latitude, longitude, accuracy, altitude: bestPosition.coords.altitude, altitudeAccuracy: bestPosition.coords.altitudeAccuracy, heading: bestPosition.coords.heading, speed: bestPosition.coords.speed, timestamp: bestPosition.timestamp }
          setLocationDiagnostics(current => ({ ...current, selected, watchDurationMs: Date.now() - startedAt }))
          console.info('Attendance browser GeolocationPosition selected.', { source: 'browser geolocation (OS-selected provider)', position: bestPosition, ...selected })
          resolve({ location: { latitude, longitude, accuracy, source: 'browser' }, warning: '', reason: '' })
          return
        }
        console.warn('Attendance location capture failed.', { code: locationError?.code, message: locationError?.message })
        resolve({ location: { latitude: null, longitude: null, accuracy: null, source: 'browser' }, warning: locationFailureMessage(locationError), reason: locationError?.code === 1 ? 'permission-denied' : 'unavailable' })
      }
      const consider = position => {
        const reading = { latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy: position.coords.accuracy, altitude: position.coords.altitude, altitudeAccuracy: position.coords.altitudeAccuracy, heading: position.coords.heading, speed: position.coords.speed, timestamp: position.timestamp }
        setLocationDiagnostics(current => ({ ...current, readings: [...(current?.readings || []), reading] }))
        console.info('Attendance browser GeolocationPosition reading.', { source: 'browser geolocation (OS-selected provider)', position, ...reading })
        if (!bestPosition || position.coords.accuracy < bestPosition.coords.accuracy) {
          bestPosition = position
          onStatus(`Location found. Accuracy approximately ${Math.round(position.coords.accuracy)}m. Improving fix...`)
          if (position.coords.accuracy <= 30) finish()
        }
      }
      const options = { enableHighAccuracy: true, timeout: 30000, maximumAge: 0 }
      navigator.geolocation.getCurrentPosition(consider, error => { if (error.code === 1 && !bestPosition) finish(error) }, options)
      watchId = navigator.geolocation.watchPosition(consider, error => { if (error.code === 1 && !bestPosition) finish(error) }, options)
      timerId = window.setTimeout(() => finish(), 15000)
    })
  }

  function clearCapturedPhoto() {
    if (photoPreviewRef.current) URL.revokeObjectURL(photoPreviewRef.current)
    photoPreviewRef.current = ''
    setPhotoFile(null)
    setPhotoPreview('')
  }

  function openCamera() {
    setError('')
    setMessage('')
    clearCapturedPhoto()
    setCameraFacingMode('environment')
    setCameraSession(value => value + 1)
    setCameraPhase('live')
  }

  function cancelCamera() {
    setCameraPhase('idle')
    clearCapturedPhoto()
    setMessage('')
  }

  function retakePhoto() {
    setError('')
    setMessage('')
    clearCapturedPhoto()
    setCameraSession(value => value + 1)
    setCameraPhase('live')
  }

  function flipCamera() {
    setError('')
    setCameraFacingMode(mode => mode === 'environment' ? 'user' : 'environment')
    setCameraSession(value => value + 1)
  }

  function takePhoto() {
    const video = videoRef.current
    if (!video || !video.videoWidth) {
      setError('Camera is not ready yet. Wait for the live preview, then take the photo.')
      return
    }
    const canvas = document.createElement('canvas')
    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    canvas.getContext('2d').drawImage(video, 0, 0)
    canvas.toBlob(blob => {
      if (!blob) {
        setError('Could not capture a photo from the camera.')
        return
      }
      stopMediaStream(streamRef.current)
      streamRef.current = null
      if (videoRef.current) videoRef.current.srcObject = null
      clearCapturedPhoto()
      const previewUrl = URL.createObjectURL(blob)
      photoPreviewRef.current = previewUrl
      setPhotoFile(new File([blob], 'attendance.jpg', { type: blob.type || 'image/jpeg' }))
      setPhotoPreview(previewUrl)
      setCameraPhase('preview')
      setError('')
      setMessage('Review your photo before recording attendance.')
    }, 'image/jpeg', 0.92)
  }

  async function recordAttendance() {
    if (cameraPhase !== 'preview' || !photoFile) {
      openCamera()
      return
    }
    setBusy(true)
    setError('')
    setMessage('Getting your location...')
    try {
      const { location, warning, reason } = await getLocationOnce(status => setMessage(status))
      if (location.latitude === null || location.longitude === null) {
        setMessage(`${warning} Recording attendance without location.`)
      }
      const formData = new FormData()
      formData.append('action', action)
      if (location.latitude != null) formData.append('latitude', location.latitude)
      if (location.longitude != null) formData.append('longitude', location.longitude)
      if (location.accuracy != null) formData.append('accuracy', location.accuracy)
      formData.append('source', location.source || 'browser')
      formData.append('photo', photoFile)
      const { data } = await api.post('/api/attendance/verify-with-photo', formData)
      if (!data.success) throw new Error(data.message || 'Attendance could not be recorded.')
      const refreshedHistory = await loadHistory()
      const record = refreshedHistory.find(item => item.date === today)
      const savedLocation = action === 'check_in' ? record?.check_in_location : record?.check_out_location
      setLocationDiagnostics(current => current ? ({ ...current, resolvedArea: formatLocation(savedLocation) }) : current)
      const locationNote = location.latitude === null || location.longitude === null ? ` ${reason === 'permission-denied' ? 'Location unavailable — permission denied.' : 'Location unavailable.'}` : ` ${formatLocation(savedLocation)}. ${accuracyLabel(savedLocation?.accuracy)}`
      setMessage(`${action === 'check_in' ? 'Check-in' : 'Check-out'} recorded at ${formatKolkataTime(data.timestamp)} IST.${locationNote}`)
      setCameraPhase('idle')
      clearCapturedPhoto()
    } catch (requestError) {
      console.error('Attendance request failed.', { url: requestError.config?.url, status: requestError.response?.status, detail: requestError.response?.data?.detail, message: requestError.message })
      setError(requestFailureMessage(requestError))
      setMessage('')
    } finally {
      setBusy(false)
    }
  }

  return <>
    <section className="hero-strip"><div><span className="eyebrow cyan">TODAY / {formatKolkataDate().toUpperCase()}</span><h2>Record your presence</h2><p>Open the camera on this page, take one photo, then record your attendance and current location when available.</p></div><div className="verification-state"><span className="pulse-dot"/> Attendance ready</div></section>
    <div className="attendance-grid"><section className="panel verification-panel"><div className="panel-heading"><div><span className="eyebrow">AURELIX ATTENDANCE</span><h3>{action === 'check_in' ? 'Check in' : 'Check out'}</h3></div><span className="step-count">PHOTO</span></div>
      {cameraPhase === 'idle' && <button className="primary-button action-button" type="button" disabled={busy || (action === 'check_in' && todayRecord?.check_in_time) || (action === 'check_out' && todayRecord?.check_out_time)} onClick={openCamera}><Camera size={18}/>{actionLabel}</button>}
      {cameraPhase !== 'idle' && <div className="camera-capture" aria-label={`${actionLabel} camera`}>
        <div className="camera-stage">
          {cameraPhase === 'live' && <video ref={videoRef} className={cameraFacingMode === 'user' ? 'camera-preview front-camera' : 'camera-preview'} autoPlay playsInline muted />}
          {cameraPhase === 'preview' && photoPreview && <img src={photoPreview} alt="Captured attendance photo" />}
        </div>
        <div className="photo-actions">
          {cameraPhase === 'live' && <>
            <button className="ghost-button" type="button" onClick={flipCamera} disabled={busy}><SwitchCamera size={16}/>Flip Camera</button>
            <button className="primary-button" type="button" onClick={takePhoto} disabled={busy}>Take Photo</button>
            <button className="ghost-button" type="button" onClick={cancelCamera} disabled={busy}>Cancel</button>
          </>}
          {cameraPhase === 'preview' && <>
            <button className="ghost-button" type="button" onClick={retakePhoto} disabled={busy}>Retake</button>
            <button className="primary-button" type="button" onClick={recordAttendance} disabled={busy}>{busy ? 'Recording...' : `Use Photo & ${actionLabel}`}</button>
          </>}
        </div>
      </div>}
      {message && <div className="success-box"><Check size={17}/>{message}</div>}{error && <div className="error-box"><X size={16}/>{error}</div>}<div className="checks"><span><Camera size={15}/> Photo required before recording</span><span><MapPin size={15}/> One-time browser location</span><span><Check size={15}/> Server timestamp recorded</span></div>{import.meta.env.DEV && locationDiagnostics && <div className="location-diagnostics"><h4>Development location diagnostics</h4><span>Device: {locationDiagnostics.device}</span><span>Browser: {locationDiagnostics.browser}</span><span>Secure context: {locationDiagnostics.secureContext ? 'yes' : 'no'}</span><span>Permission: {locationDiagnostics.permission}</span><span>Readings: {locationDiagnostics.readings.length}</span>{locationDiagnostics.selected && <><span>Latitude: {locationDiagnostics.selected.latitude}</span><span>Longitude: {locationDiagnostics.selected.longitude}</span><span>Accuracy: {locationDiagnostics.selected.accuracy} meters</span><span>Timestamp: {formatIndiaDate(locationDiagnostics.selected.timestamp)} {formatKolkataTime(locationDiagnostics.selected.timestamp)} IST</span><span>Selected: lowest accuracy reading</span></>}{locationDiagnostics.resolvedArea && <span>Reverse-geocoded area: {locationDiagnostics.resolvedArea}</span>}</div>}</section><section className="panel status-panel"><div className="panel-heading"><h3>Today's status</h3></div><div className={todayRecord?.final_status === 'ABSENT' ? 'big-status absent' : 'big-status'}>{todayRecord?.final_status || 'READY'}<small>{todayRecord?.check_in_time ? `In ${formatKolkataTime(todayRecord.check_in_time)} IST` : 'No check-in recorded'}</small></div><div className="history-list">{todayRecord && <><div className="history-row"><span>Check-in location</span><LocationDetails location={todayRecord.check_in_location}/></div><div className="history-row"><span>Check-out</span><b>{todayRecord.check_out_time ? `${formatKolkataTime(todayRecord.check_out_time)} IST` : 'Open'}</b></div><div className="history-row"><span>Check-out location</span><LocationDetails location={todayRecord.check_out_location}/></div></>}</div></section></div>
    <section className="panel table-panel"><span className="eyebrow">MY ATTENDANCE</span><h2>Attendance history</h2><div className="history-list">{history.map(item => <div className="history-row attendance-history-row" key={item.attendance_id}><b>{formatIndiaDate(item.date)}</b><span>{item.check_in_time ? `${formatKolkataTime(item.check_in_time)} IST` : '-'}</span><LocationDetails location={item.check_in_location}/><span>{item.check_out_time ? `${formatKolkataTime(item.check_out_time)} IST` : 'Open'}</span><LocationDetails location={item.check_out_location}/><span className={statusBadgeClass(item.final_status)}>{item.final_status}</span><EmployeeAttendanceUndo record={item} onRefresh={setHistory}/></div>)}</div></section>
  </>
}
