import { useEffect, useState } from 'react'
import { Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { ArrowLeft, ArrowRight, BarChart3, Check, ClipboardList, Download, LogOut, MapPin, ShieldCheck, Trash2, UserPlus, Users, X } from 'lucide-react'
import api from './services/api'
import LocationAttendance from './LocationAttendance'
import { formatKolkataTime, kolkataDateKey } from './time'

function formatLocation(location) {
  if (!location || location.latitude == null || location.longitude == null) return 'Unavailable'
  if (location.display_name) return location.display_name
  if (location.area) return location.city && !location.area.includes(location.city) ? `${location.area}, ${location.city}` : location.area
  return 'Location unavailable'
}

function Logo({ className = 'brand-logo' }) {
  return <img className={className} src="/aurelix-logo.png" alt="Aurelix" />
}

function statusBadgeClass(status) {
  return status === 'ABSENT' ? 'badge absent' : 'badge verified'
}

function exportDetails(criteria) {
  const params = new URLSearchParams({ range: criteria.range })
  if (criteria.range === 'custom') {
    params.set('start_date', criteria.startDate)
    params.set('end_date', criteria.endDate)
    return { query: params.toString(), filename: `aurelix-attendance-custom-${criteria.startDate}-to-${criteria.endDate}.xlsx` }
  }
  if (criteria.range === 'months') {
    params.set('start_date', `${criteria.startMonth}-01`)
    params.set('end_date', `${criteria.endMonth}-01`)
    return { query: params.toString(), filename: `aurelix-attendance-months-${criteria.startMonth}-to-${criteria.endMonth}.xlsx` }
  }
  if (criteria.range === 'month') {
    params.set('date', `${criteria.month}-01`)
    return { query: params.toString(), filename: `aurelix-attendance-month-${criteria.month}.xlsx` }
  }
  if (criteria.range === 'year') {
    params.set('date', `${criteria.year}-01-01`)
    return { query: params.toString(), filename: `aurelix-attendance-year-${criteria.year}.xlsx` }
  }
  params.set('date', criteria.date)
  return { query: params.toString(), filename: `aurelix-attendance-day-${criteria.date}.xlsx` }
}

async function exportErrorMessage(error) {
  const payload = error.response?.data
  let detail = ''
  if (payload instanceof Blob) {
    try {
      const body = JSON.parse(await payload.text())
      if (typeof body.detail === 'string') detail = body.detail
      if (Array.isArray(body.detail)) detail = body.detail.map(item => item.msg).join(' ')
    } catch {
      // A non-JSON error response still receives the generic message below.
    }
  }
  if (typeof payload?.detail === 'string') detail = payload.detail
  if (!error.response) return error.request ? 'Network error: the attendance server could not be reached.' : `Export setup error: ${error.message || 'unknown error'}`
  const status = error.response.status
  const label = ({ 401: 'Authentication required', 403: 'Access denied', 404: 'Export endpoint not found', 422: 'Invalid export selection', 500: 'Server export error' })[status] || 'Export request failed'
  return `${status} ${label}${detail ? `: ${detail}` : '.'}`
}

function Shell({ user, onLogout, children }) {
  const navigate = useNavigate()
  const location = useLocation()
  const isAdmin = user?.role === 'admin'
  const links = isAdmin ? [['/admin', BarChart3, 'Command center'], ['/admin/employees', Users, 'People']] : [['/attendance', MapPin, 'Attendance'], ['/history', ClipboardList, 'My history']]
  return <div className="app-shell"><aside className="sidebar"><div className="brand"><Logo /></div><div className="workspace-label">Workspace / {isAdmin ? 'Operations' : 'Employee'}</div><nav>{links.map(([path, Icon, label]) => <button className={location.pathname === path ? 'nav-item active' : 'nav-item'} onClick={() => navigate(path)} key={path}><Icon size={17}/>{label}</button>)}</nav><div className="sidebar-bottom"><div className="user-chip"><span className="avatar">{user.full_name?.slice(0, 1)}</span><span><b>{user.full_name}</b><small>{user.department}</small></span></div><button className="nav-item" onClick={onLogout}><LogOut size={17}/>Sign out</button></div></aside><main className="main-content"><header className="topbar"><div><span className="eyebrow">AURELIX / {isAdmin ? 'OPERATIONS' : 'PERSONAL SPACE'}</span><h1>{isAdmin ? 'Attendance command center' : 'Good to see you, ' + user.full_name.split(' ')[0]}</h1></div><span className="secure-pill"><ShieldCheck size={14}/> Secure session</span></header>{children}</main></div>
}

function Login({ onLogin }) {
  const [form, setForm] = useState({ email: '', password: '' })
  const [screen, setScreen] = useState('login')
  const [recovery, setRecovery] = useState({ recovery_email: '', otp: '', challengeToken: '', resetToken: '', maskedEmail: '' })
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [busy, setBusy] = useState(false)
  const navigate = useNavigate()

  function backToLogin() {
    setScreen('login')
    setError('')
    setMessage('')
    setForm(current => ({ ...current, password: '' }))
  }

  async function submitLogin(event) {
    event.preventDefault()
    setError('')
    setBusy(true)
    try {
      const { data } = await api.post('/api/auth/login', form)
      localStorage.setItem('aurelix_token', data.access_token)
      onLogin(data.user)
      navigate(data.user.role === 'admin' ? '/admin' : '/attendance')
    } catch (err) {
      setError(err.response ? 'Incorrect email or password.' : 'Unable to sign in right now. Please check your connection.')
    } finally {
      setBusy(false)
    }
  }

  async function sendOtp(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    setMessage('')
    try {
      const { data } = await api.post('/api/auth/forgot-password/request', { recovery_email: recovery.recovery_email })
      setRecovery(current => ({ ...current, challengeToken: data.challenge_token, maskedEmail: data.masked_recovery_email || '', otp: '' }))
      setMessage(data.delivery_mode === 'mock' ? 'Development mock mode: code captured locally; no email was sent.' : data.message)
      setScreen('verify')
    } catch (err) {
      setError('Could not request a verification code. Check the details and try again.')
    } finally {
      setBusy(false)
    }
  }

  async function verifyOtp(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { data } = await api.post('/api/auth/forgot-password/verify', { challenge_token: recovery.challengeToken, otp: recovery.otp })
      setRecovery(current => ({ ...current, resetToken: data.reset_token }))
      setScreen('reset')
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(detail === 'Too many attempts. Please request a new code.' ? detail : 'Invalid or expired verification code.')
    } finally {
      setBusy(false)
    }
  }

  async function resetPassword(event) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    const newPassword = values.get('new_password')
    const confirmPassword = values.get('confirm_password')
    setError('')
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }
    setBusy(true)
    try {
      const { data } = await api.post('/api/auth/forgot-password/reset', { reset_token: recovery.resetToken, new_password: newPassword })
      setMessage(data.message)
      setScreen('success')
      setForm(current => ({ ...current, password: '' }))
    } catch (err) {
      setError(err.response?.data?.detail === 'Your password reset session has expired. Please start again.' ? err.response.data.detail : 'Could not reset your password. Please start again.')
    } finally {
      setBusy(false)
    }
  }

  return <div className="login-page"><div className="login-visual"><div className="brand"><Logo /></div><div className="visual-copy"><span className="eyebrow cyan">ATTENDANCE / TIME / PLACE</span><h1>Presence, recorded.</h1><p>Check in and check out with a secure account, server time, and a one-time location capture.</p></div><div className="signal-grid"><span><b>01</b> SECURE LOGIN</span><span><b>02</b> SERVER TIME</span><span><b>03</b> LOCATION STORED</span></div></div>
    {screen === 'login' && <form className="login-card" onSubmit={submitLogin}><span className="eyebrow">WELCOME BACK</span><h2>Sign in to your workspace</h2><p className="muted">Use your Aurelix credentials to continue.</p><label>Work email<input type="email" required value={form.email} onChange={event => setForm({ ...form, email: event.target.value })} placeholder="you@aurelix.com" disabled={busy} /></label><label>Password<input type="password" required value={form.password} onChange={event => setForm({ ...form, password: event.target.value })} placeholder="Password" disabled={busy} /></label>{error && <><div className="error-box"><X size={16}/>{error}</div><button className="login-link" type="button" onClick={() => { setError(''); setScreen('request') }}>Forgot password?</button></>}<button className="primary-button" type="submit" disabled={busy}>{busy ? 'Signing in...' : <>Enter workspace <ArrowRight size={17}/></>}</button><small className="form-note">Protected by JWT authentication and role-based access.</small></form>}
    {screen === 'request' && <form className="login-card recovery-card" onSubmit={sendOtp}><span className="eyebrow">ACCOUNT RECOVERY</span><h2>Forgot Password</h2><label>Recovery Email<input type="email" required autoComplete="email" value={recovery.recovery_email} onChange={event => setRecovery({ ...recovery, recovery_email: event.target.value })} disabled={busy}/></label>{error && <div className="error-box"><X size={16}/>{error}</div>}<button className="primary-button" type="submit" disabled={busy}>{busy ? 'Sending...' : 'Send OTP'}</button><button className="login-link" type="button" onClick={backToLogin} disabled={busy}>Back to Login</button></form>}
    {screen === 'verify' && <form className="login-card recovery-card" onSubmit={verifyOtp}><span className="eyebrow">ACCOUNT RECOVERY</span><h2>OTP Verification</h2><p className="muted">Verification code sent to</p><p className="recovery-destination">{recovery.maskedEmail}</p>{message && <div className="success-box"><Check size={16}/>{message}</div>}<label>OTP<input type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={recovery.otp} onChange={event => setRecovery({ ...recovery, otp: event.target.value.replace(/\D/g, '').slice(0, 6) })} disabled={busy}/></label>{error && <div className="error-box"><X size={16}/>{error}</div>}<button className="primary-button" type="submit" disabled={busy || recovery.otp.length !== 6}>{busy ? 'Verifying...' : 'Verify OTP'}</button><button className="login-link" type="button" onClick={sendOtp} disabled={busy}>Resend OTP</button><button className="login-link" type="button" onClick={backToLogin} disabled={busy}>Back to Login</button></form>}
    {screen === 'reset' && <form className="login-card recovery-card" onSubmit={resetPassword}><span className="eyebrow">ACCOUNT RECOVERY</span><h2>Create New Password</h2><label>New Password<input type="password" name="new_password" minLength={8} maxLength={128} autoComplete="new-password" required disabled={busy}/></label><label>Confirm New Password<input type="password" name="confirm_password" minLength={8} maxLength={128} autoComplete="new-password" required disabled={busy}/></label>{error && <div className="error-box"><X size={16}/>{error}</div>}<button className="primary-button" type="submit" disabled={busy}>{busy ? 'Resetting...' : 'Reset Password'}</button><button className="login-link" type="button" onClick={backToLogin} disabled={busy}>Back to Login</button></form>}
    {screen === 'success' && <section className="login-card recovery-card"><span className="eyebrow">ACCOUNT RECOVERY</span><h2>Password reset complete</h2><div className="success-box"><Check size={16}/>{message}</div><button className="primary-button" type="button" onClick={backToLogin}>Back to Login</button></section>}
  </div>
}

function AdminPhotoModal({ viewer, onClose }) {
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const eventLabel = viewer.event === 'check_in' ? 'Check In' : 'Check Out'
  useEffect(() => {
    const onKey = event => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])
  useEffect(() => {
    let objectUrl = ''
    let cancelled = false
    setLoading(true)
    setError('')
    setUrl('')
    api.get(`/api/attendance/admin/${viewer.attendanceId}/photo?event=${viewer.event}`, { responseType: 'blob' })
      .then(({ data }) => {
        if (cancelled) return
        if (data?.type && data.type.includes('application/json')) {
          setError('Photo expired or unavailable')
          return
        }
        objectUrl = URL.createObjectURL(data)
        setUrl(objectUrl)
      })
      .catch(() => { if (!cancelled) setError('Photo expired or unavailable') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [viewer.attendanceId, viewer.event])
  return (
    <div className="photo-capture-overlay admin-photo-modal" role="dialog" aria-modal="true" aria-label={`${eventLabel} attendance photo`} onClick={event => { if (event.target === event.currentTarget) onClose() }}>
      <div className="photo-capture-card admin-photo-card">
        <span className="eyebrow">ATTENDANCE PHOTO</span>
        <h3>{eventLabel} photo</h3>
        <p className="admin-photo-meta"><b>{viewer.employee}</b><small>Event: {eventLabel}</small><small>Attendance date: {viewer.date || 'Unavailable'}</small><small>Event timestamp: {viewer.timestamp ? `${formatKolkataTime(viewer.timestamp)} IST` : 'Unavailable'}</small></p>
        {loading && <div className="admin-photo-loading">Loading photo...</div>}
        {error && <div className="error-box"><X size={16}/>{error}</div>}
        {url && <img src={url} alt={`${eventLabel} attendance photo for ${viewer.employee}`} />}
        <button className="ghost-button admin-photo-close" type="button" onClick={onClose}>Close</button>
      </div>
    </div>
  )
}

function ExportDialog({ selectedDate, onClose, onExport, isExporting, exportError }) {
  const currentYear = Number(selectedDate.slice(0, 4))
  const [range, setRange] = useState('day')
  const [date, setDate] = useState(selectedDate)
  const [startDate, setStartDate] = useState(selectedDate)
  const [endDate, setEndDate] = useState(selectedDate)
  const [month, setMonth] = useState(selectedDate.slice(0, 7))
  const [startMonth, setStartMonth] = useState(selectedDate.slice(0, 7))
  const [endMonth, setEndMonth] = useState(selectedDate.slice(0, 7))
  const [year, setYear] = useState(String(currentYear))
  const [validationError, setValidationError] = useState('')
  const years = Array.from({ length: 11 }, (_, index) => currentYear - 5 + index)

  function submit(event) {
    event.preventDefault()
    setValidationError('')
    if (range === 'months' && startMonth > endMonth) {
      setValidationError('Start month must not be after end month.')
      return
    }
    if (range === 'custom' && startDate > endDate) {
      setValidationError('From Date must not be after To Date.')
      return
    }
    onExport({ range, date, month, startMonth, endMonth, startDate, endDate, year })
  }

  return <div className="photo-capture-overlay export-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="export-dialog-title" onClick={event => { if (!isExporting && event.target === event.currentTarget) onClose() }}>
    <form className="photo-capture-card export-dialog" onSubmit={submit}>
      <div className="export-dialog-heading"><div><span className="eyebrow cyan">ATTENDANCE EXPORT</span><h3 id="export-dialog-title">Export attendance data</h3></div><button className="icon-button" type="button" title="Close export dialog" onClick={onClose} disabled={isExporting}><X size={16}/></button></div>
      <label className="export-field">Export period<select value={range} onChange={event => { setRange(event.target.value); setValidationError('') }} disabled={isExporting}><option value="day">Day</option><option value="custom">Custom Date Range</option><option value="month">Month</option><option value="months">Multiple Months</option><option value="year">Year</option></select></label>
      {range === 'day' && <label className="export-field">Date<input type="date" value={date} onChange={event => setDate(event.target.value)} disabled={isExporting} required /></label>}
      {range === 'custom' && <div className="export-month-fields"><label className="export-field">From Date<input type="date" value={startDate} onChange={event => setStartDate(event.target.value)} disabled={isExporting} required /></label><label className="export-field">To Date<input type="date" value={endDate} onChange={event => setEndDate(event.target.value)} disabled={isExporting} required /></label></div>}
      {range === 'month' && <label className="export-field">Month<input type="month" value={month} onChange={event => setMonth(event.target.value)} disabled={isExporting} required /></label>}
      {range === 'months' && <div className="export-month-fields"><label className="export-field">Start month<input type="month" value={startMonth} onChange={event => setStartMonth(event.target.value)} disabled={isExporting} required /></label><label className="export-field">End month<input type="month" value={endMonth} onChange={event => setEndMonth(event.target.value)} disabled={isExporting} required /></label></div>}
      {range === 'year' && <label className="export-field">Year<select value={year} onChange={event => setYear(event.target.value)} disabled={isExporting}>{years.map(value => <option value={value} key={value}>{value}</option>)}</select></label>}
      {(validationError || exportError) && <div className="error-box"><X size={16}/>{validationError || exportError}</div>}
      <div className="export-dialog-actions"><button className="ghost-button" type="button" onClick={onClose} disabled={isExporting}>Cancel</button><button className="primary-button" type="submit" disabled={isExporting}>{isExporting ? 'Exporting...' : <><Download size={16}/>Export workbook</>}</button></div>
    </form>
  </div>
}

function AdminDashboard() {
  async function clearRecord(attendanceId) {
    if (!window.confirm('Clear this attendance record?')) return
    await api.delete(`/api/attendance/admin/${attendanceId}`)
    window.location.reload()
  }
  const today = kolkataDateKey()
  const [selectedDate, setSelectedDate] = useState(today)
  const [month, setMonth] = useState(today.slice(0, 7))
  const [stats, setStats] = useState({})
  const [records, setRecords] = useState([])
  const [monthRecords, setMonthRecords] = useState([])
  const [photoViewer, setPhotoViewer] = useState(null)
  const [exportDialogOpen, setExportDialogOpen] = useState(false)
  const [isExporting, setIsExporting] = useState(false)
  const [exportError, setExportError] = useState('')
  useEffect(() => { api.get(`/api/attendance/admin?date=${selectedDate}`).then(({ data }) => { setRecords(data); const present = data.filter(item => item.final_status === 'PRESENT').length; setStats({ total_employees: data.length, present_today: present, absent_today: data.length - present }) }).catch(() => {}) }, [selectedDate])
  useEffect(() => { api.get(`/api/attendance/admin/month?month=${month}`).then(({ data }) => setMonthRecords(data)).catch(() => {}) }, [month])
  function chooseDate(value) { if (value) { setSelectedDate(value); setMonth(value.slice(0, 7)) } }
  function shiftMonth(amount) {
    const [year, monthNumber] = month.split('-').map(Number)
    const nextDate = new Date(year, monthNumber - 1 + amount, 1)
    const next = `${nextDate.getFullYear()}-${String(nextDate.getMonth() + 1).padStart(2, '0')}`
    setMonth(next)
    setSelectedDate(`${next}-01`)
  }
  function renderCalendar() {
    const [year, monthNumber] = month.split('-').map(Number)
    const days = new Date(year, monthNumber, 0).getDate()
    const offset = new Date(year, monthNumber - 1, 1).getDay()
    const summaries = monthRecords.reduce((result, item) => ({ ...result, [item.date]: (result[item.date] || 0) + (item.final_status === 'PRESENT' ? 1 : 0) }), {})
    return [...Array(offset).fill(null).map((_, index) => <span className="calendar-day empty" key={`empty-${index}`} />), ...Array.from({ length: days }, (_, index) => { const date = `${month}-${String(index + 1).padStart(2, '0')}`; return <button type="button" className={date === selectedDate ? 'calendar-day selected' : 'calendar-day'} onClick={() => chooseDate(date)} key={date}><b>{index + 1}</b>{summaries[date] ? <small>{summaries[date]} present</small> : <small>-</small>}</button> })]
  }
  async function exportData(criteria) {
    const details = exportDetails(criteria)
    setIsExporting(true)
    setExportError('')
    try {
      const response = await api.get(`/api/admin/export?${details.query}`, { responseType: 'blob' })
      const url = URL.createObjectURL(response.data)
      const link = document.createElement('a')
      const filename = response.headers?.['content-disposition']?.match(/filename="?([^";]+)"?/)?.[1] || details.filename
      link.href = url
      link.download = filename
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      setExportDialogOpen(false)
    } catch (error) {
      setExportError(await exportErrorMessage(error))
    } finally {
      setIsExporting(false)
    }
  }
  function openPhoto(record, event) {
    setPhotoViewer({
      attendanceId: record.attendance_id,
      event,
      employee: record.employee?.full_name || record.user_name || record.employee_id,
      date: record.date,
      timestamp: event === 'check_in' ? record.check_in_time : record.check_out_time,
    })
  }
  function photoCell(record, event) {
    if (!record[`${event}_photo_available`]) return <span className="muted">-</span>
    return <button className="ghost-button table-action" type="button" onClick={() => openPhoto(record, event)}>{event === 'check_in' ? 'View Check-In Photo' : 'View Check-Out Photo'}</button>
  }
  return <><section className="hero-strip compact"><div><span className="eyebrow cyan">LIVE OPERATIONS / OVERVIEW</span><h2>Attendance register</h2><p>Review employee attendance by day and export the stored check-in/check-out records.</p></div><label className="date-picker">Selected day<input type="date" value={selectedDate} onChange={event => chooseDate(event.target.value)} /></label></section><div className="stats-grid">{[['TOTAL EMPLOYEES', stats.total_employees, Users], ['PRESENT', stats.present_today, Check], ['ABSENT', stats.absent_today, X]].map(([label, value, Icon]) => <div className="stat-card" key={label}><Icon size={17}/><span>{label}</span><strong>{value ?? '-'}</strong></div>)}</div><div className="admin-dashboard-grid"><section className="panel calendar-panel"><div className="panel-heading"><div><span className="eyebrow">ATTENDANCE CALENDAR</span><h3>{new Date(Number(month.slice(0, 4)), Number(month.slice(5, 7)) - 1, 1).toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</h3></div><div className="calendar-actions"><button className="icon-button" title="Previous month" onClick={() => shiftMonth(-1)}><ArrowLeft size={16}/></button><button className="icon-button" title="Next month" onClick={() => shiftMonth(1)}><ArrowRight size={16}/></button></div></div><div className="calendar-weekdays">{['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(day => <span key={day}>{day}</span>)}</div><div className="calendar-grid">{renderCalendar()}</div></section><section className="panel table-panel"><div className="panel-heading"><div><span className="eyebrow">ATTENDANCE LOG / {selectedDate}</span><h3>Daily Attendance Register</h3></div><button className="ghost-button" type="button" onClick={() => { setExportError(''); setExportDialogOpen(true) }}><Download size={16}/>Export data</button></div><div className="table-scroll"><table><thead><tr><th>Employee</th><th>Department</th><th>In</th><th>In location</th><th>Check-in Photo</th><th>Out</th><th>Out location</th><th>Check-out Photo</th><th>Status</th><th>Action</th></tr></thead><tbody>{records.map(item => <tr key={item.attendance_id}><td><b>{item.employee?.full_name || item.user_name || item.employee_id}</b><small>{item.employee_id}</small></td><td>{item.employee?.department || '-'}</td><td>{item.check_in_time ? `${formatKolkataTime(item.check_in_time)} IST` : '-'}</td><td>{formatLocation(item.check_in_location)}</td><td>{photoCell(item, 'check_in')}</td><td>{item.check_out_time ? `${formatKolkataTime(item.check_out_time)} IST` : 'Open'}</td><td>{formatLocation(item.check_out_location)}</td><td>{photoCell(item, 'check_out')}</td><td><span className={item.final_status === 'PRESENT' ? 'badge verified' : 'badge absent'}>{item.final_status}</span></td><td><button className="ghost-button table-action" onClick={() => clearRecord(item.attendance_id)} disabled={item.attendance_id.startsWith('absent-')}>Undo record</button></td></tr>)}</tbody></table>{!records.length && <div className="empty-state">No active employees found.</div>}</div></section></div>{exportDialogOpen && <ExportDialog selectedDate={selectedDate} onClose={() => setExportDialogOpen(false)} onExport={exportData} isExporting={isExporting} exportError={exportError} />}{photoViewer && <AdminPhotoModal viewer={photoViewer} onClose={() => setPhotoViewer(null)} />}</>
}

function RecoveryEmailVerificationPanel({ challenge, onVerified }) {
  const [otp, setOtp] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError('')
    try {
      const { data } = await api.post('/api/auth/recovery-email/verify', { challenge_token: challenge.token, otp })
      onVerified(data.message)
    } catch (err) {
      const detail = err.response?.data?.detail
      setError(detail === 'Too many attempts. Please request a new code.' ? detail : 'Invalid or expired verification code.')
    } finally {
      setBusy(false)
    }
  }
  return <form className="recovery-verification" onSubmit={submit}><p className="muted">Verification code sent to <b>{challenge.maskedEmail}</b></p><label>Recovery Email OTP<input type="text" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" maxLength={6} required value={otp} onChange={event => setOtp(event.target.value.replace(/\D/g, '').slice(0, 6))} disabled={busy}/></label>{error && <div className="error-box"><X size={16}/>{error}</div>}<button className="primary-button" type="submit" disabled={busy || otp.length !== 6}>{busy ? 'Verifying...' : 'Verify Recovery Email'}</button></form>
}

function CreateEmployeePanel({ onCreated }) {
  const [form, setForm] = useState({ employee_id: '', full_name: '', email: '', recovery_email: '', department: '', role: 'employee', password: '' })
  const [message, setMessage] = useState('')
  const [verification, setVerification] = useState(null)
  async function submit(event) {
    event.preventDefault()
    try {
      const { data } = await api.post('/api/employees', form)
      setMessage(data.verification_delivery_mode === 'mock' ? 'Development mock mode: code captured locally; no email was sent.' : data.verification_message || 'Employee created. Verify the recovery email to enable password recovery.')
      setVerification(data.verification_challenge_token ? { token: data.verification_challenge_token, maskedEmail: data.recovery_email.replace(/^(.).*(@.*)$/, '$1******$2') } : null)
      setForm({ employee_id: '', full_name: '', email: '', recovery_email: '', department: '', role: 'employee', password: '' })
      onCreated()
    } catch (error) {
      const detail = error.response?.data?.detail
      setMessage(Array.isArray(detail) ? detail.map(item => item.msg).join(' ') : detail || 'Could not create employee.')
    }
  }
  return <section className="panel form-panel"><span className="eyebrow">PEOPLE / NEW RECORD</span><h2>Add employee</h2><form className="employee-form" onSubmit={submit}>{[['employee_id','Employee ID'],['full_name','Full name'],['email','Login Email'],['recovery_email','Recovery Email'],['department','Department'],['password','Temporary password']].map(([key, label]) => <label key={key}>{label}<input required={key !== 'password'} type={key === 'email' || key === 'recovery_email' ? 'email' : key === 'password' ? 'password' : 'text'} autoComplete={key === 'recovery_email' ? 'email' : undefined} value={form[key]} onChange={event => setForm({ ...form, [key]: event.target.value })}/></label>)}<button className="primary-button" type="submit"><UserPlus size={17}/> Add employee</button></form>{message && <div className="success-box"><Check size={17}/>{message}</div>}{verification && <RecoveryEmailVerificationPanel challenge={verification} onVerified={verificationMessage => { setVerification(null); setMessage(verificationMessage); onCreated() }}/>}</section>
}

function EmployeeEditor({ user }) {
  const [employees, setEmployees] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [form, setForm] = useState(null)
  const [message, setMessage] = useState('')
  const [verification, setVerification] = useState(null)
  async function load() {
    const { data } = await api.get('/api/employees')
    setEmployees(data)
    if (selectedId) {
      const selected = data.find(item => item.employee_id === selectedId)
      if (selected) setForm(current => current || { ...selected, password: '' })
    }
  }
  useEffect(() => { load().catch(() => {}) }, [])
  function selectEmployee(id) {
    const selected = employees.find(item => item.employee_id === id)
    setSelectedId(id)
    setForm(selected ? { ...selected, password: '' } : null)
    setMessage('')
  }
  async function save(event) {
    event.preventDefault()
    try {
      const { data: updatedEmployee } = await api.put(`/api/employees/${selectedId}`, { employee_id: form.employee_id, full_name: form.full_name, email: form.email, recovery_email: form.recovery_email || null, department: form.department, role: form.role, password: form.password || null })
      setSelectedId(updatedEmployee.employee_id)
      setForm({ ...updatedEmployee, password: '' })
      setVerification(updatedEmployee.verification_challenge_token ? { token: updatedEmployee.verification_challenge_token, maskedEmail: updatedEmployee.recovery_email.replace(/^(.).*(@.*)$/, '$1******$2') } : null)
      setMessage(updatedEmployee.verification_delivery_mode === 'mock' ? 'Development mock mode: code captured locally; no email was sent.' : updatedEmployee.verification_message || 'Employee details saved successfully.')
      await load()
    } catch (error) {
      const detail = error.response?.data?.detail
      setMessage(Array.isArray(detail) ? detail.map(item => item.msg).join(' ') : detail || 'Could not save employee details.')
    }
  }
  async function remove() {
    if (!form || !window.confirm(`Remove ${form.full_name}? Attendance history will be kept.`)) return
    try {
      await api.delete(`/api/employees/${selectedId}`)
      setMessage('Employee removed.')
      setSelectedId('')
      setForm(null)
      await load()
    } catch (error) {
      setMessage(error.response?.data?.detail || 'Could not remove employee.')
    }
  }
  const isCurrentUser = Boolean(form && (form.id === user?.id || form.employee_id === user?.employee_id))
  return <div className="admin-people-grid"><CreateEmployeePanel onCreated={load}/><section className="panel"><span className="eyebrow">PEOPLE / DIRECTORY</span><h2>Members and admins</h2><div className="people-list">{employees.map(item => <button type="button" className={item.employee_id === selectedId ? 'person-row selected-person' : 'person-row'} onClick={() => selectEmployee(item.employee_id)} key={item.employee_id}><span><b>{item.full_name}</b><small>{item.employee_id} - {item.department} - {item.role}</small></span><span className="muted">Edit</span></button>)}</div></section><section className="panel">{form ? <><span className="eyebrow">PEOPLE / EDIT RECORD</span><h2>Edit employee details</h2><form className="employee-form" onSubmit={save}>{[['employee_id','Employee ID'],['full_name','Full name'],['email','Login Email'],['department','Department'],['password','New password']].map(([key, label]) => <label key={key}>{label}<input type={key === 'email' ? 'email' : key === 'password' ? 'password' : 'text'} value={form[key] || ''} onChange={event => setForm({ ...form, [key]: event.target.value })}/></label>)}<label>Recovery Email<input type="email" autoComplete="email" value={form.recovery_email || ''} onChange={event => setForm({ ...form, recovery_email: event.target.value })}/>{form.recovery_email && <small>{form.recovery_email_verified ? 'Verified' : 'Not verified'}</small>}</label><button className="primary-button" type="submit">Save changes</button>{!isCurrentUser && <button className="ghost-button" type="button" onClick={remove}><Trash2 size={16}/> Remove employee</button>}</form>{message && <div className="success-box"><Check size={17}/>{message}</div>}{verification && <RecoveryEmailVerificationPanel challenge={verification} onVerified={verificationMessage => { setVerification(null); setForm(current => ({ ...current, recovery_email_verified: true })); setMessage(verificationMessage); load() }}/>}</> : <><span className="eyebrow">PEOPLE / EDIT RECORD</span><h2>Select a person</h2><p className="muted">Choose a person from the directory to edit their details.</p></>}</section></div>
}

function History() {
  const [items, setItems] = useState([])
  const [error, setError] = useState('')
  async function load() { const { data } = await api.get('/api/attendance/mine'); setItems(data) }
  useEffect(() => { load().catch(() => setError('Could not load attendance history.')) }, [])
  async function undo(item, action) {
    const label = action === 'check_in' ? 'check-in time' : 'check-out time'
    if (!window.confirm(`Undo this ${label}?`)) return
    setError('')
    try {
      await api.delete(`/api/attendance/mine/${item.attendance_id}?action=${action}`)
      await load()
    } catch (err) {
      setError(err.response?.data?.detail || `Could not undo ${label}.`)
    }
  }
  return <section className="panel table-panel"><span className="eyebrow">MY ATTENDANCE</span><h2>Attendance history</h2>{error && <div className="error-box"><X size={16}/>{error}</div>}<div className="table-scroll"><table><thead><tr><th>Date</th><th>Check in</th><th>Check-in location</th><th>Check out</th><th>Check-out location</th><th>Status</th><th>Actions</th></tr></thead><tbody>{items.map(item => <tr key={item.attendance_id}><td><b>{item.date}</b></td><td>{item.check_in_time ? `${formatKolkataTime(item.check_in_time)} IST` : '-'}</td><td>{formatLocation(item.check_in_location)}</td><td>{item.check_out_time ? `${formatKolkataTime(item.check_out_time)} IST` : 'Open'}</td><td>{formatLocation(item.check_out_location)}</td><td><span className={statusBadgeClass(item.final_status)}>{item.final_status}</span></td><td><button className="ghost-button table-action" disabled={!item.check_in_time} onClick={() => undo(item, 'check_in')}>Undo check-in</button><button className="ghost-button table-action" disabled={!item.check_out_time} onClick={() => undo(item, 'check_out')}>Undo check-out</button></td></tr>)}</tbody></table></div></section>
}

export default function App() {
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => { if (localStorage.getItem('aurelix_token')) api.get('/api/auth/me').then(({ data }) => setUser(data)).catch(() => localStorage.removeItem('aurelix_token')).finally(() => setLoading(false)); else setLoading(false) }, [])
  if (loading) return <div className="loading">Loading secure workspace...</div>
  if (!user) return <Routes><Route path="*" element={<Login onLogin={setUser}/>}/></Routes>
  const logout = () => { localStorage.removeItem('aurelix_token'); setUser(null) }
  return <Shell user={user} onLogout={logout}><Routes><Route path="/" element={<Navigate to={user.role === 'admin' ? '/admin' : '/attendance'} replace/>}/><Route path="/attendance" element={<LocationAttendance/>}/><Route path="/history" element={<History/>}/><Route path="/admin" element={user.role === 'admin' ? <AdminDashboard/> : <Navigate to="/attendance"/>}/><Route path="/admin/employees" element={user.role === 'admin' ? <EmployeeEditor user={user}/> : <Navigate to="/attendance"/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes></Shell>
}
