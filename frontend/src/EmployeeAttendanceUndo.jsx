import { useState } from 'react'
import { X } from 'lucide-react'
import api from './services/api'
import { formatIndiaDate } from './time'

export default function EmployeeAttendanceUndo({ record, onRefresh }) {
  const [event, setEvent] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const hasCheckIn = Boolean(record.check_in_time)
  const hasCheckOut = Boolean(record.check_out_time)

  async function confirmUndo() {
    if (!event) return
    setBusy(true)
    setError('')
    try {
      await api.post(`/api/attendance/mine/${encodeURIComponent(record.attendance_id)}/undo-${event.replace('_', '-')}`)
    } catch (requestError) {
      setError(requestError.response?.data?.detail || `Could not undo ${event.replace('_', ' ')}.`)
      setBusy(false)
      return
    }
    setEvent(null)
    try {
      const { data } = await api.get('/api/attendance/mine')
      onRefresh(data)
    } catch {
      setError('Undo succeeded, but attendance could not be refreshed. Reload the page to see the updated state.')
    } finally {
      setBusy(false)
    }
  }

  const title = event === 'check_in' ? 'Undo Check In?' : 'Undo Check Out?'
  const eventLabel = event === 'check_in' ? 'check-in' : 'check-out'
  const consequence = event === 'check_in'
    ? 'This will remove your check-in time, location, and check-in photo reference.'
    : 'This will remove your check-out time, location, and check-out photo reference.'

  return <>
    <div className="attendance-undo-actions">
      {hasCheckIn && <button className="ghost-button table-action undo-check-in" type="button" onClick={() => { setError(''); setEvent('check_in') }} disabled={hasCheckOut} title={hasCheckOut ? 'Undo Check Out first to safely undo Check In.' : undefined}>Undo Check In</button>}
      {hasCheckIn && hasCheckOut && <button className="ghost-button table-action undo-check-out" type="button" onClick={() => { setError(''); setEvent('check_out') }}>Undo Check Out</button>}
      {hasCheckIn && hasCheckOut && <small className="undo-sequence-hint">Undo Check Out first.</small>}
    </div>
    {error && !event && <div className="error-box" role="alert"><X size={16}/>{error}</div>}
    {event && <div className="photo-capture-overlay undo-dialog-overlay" role="dialog" aria-modal="true" aria-labelledby="employee-undo-dialog-title" onClick={click => { if (!busy && click.target === click.currentTarget) setEvent(null) }}>
      <section className="photo-capture-card undo-dialog">
        <span className="eyebrow cyan">REVERSE ATTENDANCE EVENT</span>
        <h3 id="employee-undo-dialog-title">{title}</h3>
        <p className="undo-dialog-copy">{consequence}</p>
        <p className="undo-dialog-employee">{record.user_name || record.employee_id} · {formatIndiaDate(record.date)} · {eventLabel}</p>
        {error && <div className="error-box" role="alert"><X size={16}/>{error}</div>}
        <div className="undo-dialog-actions">
          <button className="ghost-button" type="button" onClick={() => setEvent(null)} disabled={busy}>Cancel</button>
          <button className={`primary-button ${event === 'check_in' ? 'undo-confirm-check-in' : 'undo-confirm-check-out'}`} type="button" onClick={confirmUndo} disabled={busy}>{busy ? 'Undoing...' : title.replace('?', '')}</button>
        </div>
      </section>
    </div>}
  </>
}
