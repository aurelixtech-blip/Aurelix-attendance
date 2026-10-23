import { useEffect, useState } from 'react'
import api from './services/api'
import EmployeeAttendanceUndo from './EmployeeAttendanceUndo'
import EmployeeAttendanceNote from './EmployeeAttendanceNote'
import { formatIndiaDate, formatKolkataTime } from './time'

function formatLocation(location) {
  if (!location || location.latitude == null || location.longitude == null) return 'Unavailable'
  if (location.display_name) return location.display_name
  if (location.area) return location.city && !location.area.includes(location.city) ? `${location.area}, ${location.city}` : location.area
  return 'Location unavailable'
}

function statusBadgeClass(status) {
  return status === 'ABSENT' ? 'badge absent' : 'badge verified'
}

export default function EmployeeAttendanceHistory() {
  const [items, setItems] = useState([])
  const [notes, setNotes] = useState({})
  const [noteError, setNoteError] = useState('')
  const [notesLoading, setNotesLoading] = useState(true)
  async function load() {
    const { data } = await api.get('/api/attendance/mine')
    setItems(data)
    try {
      const { data: savedNotes } = await api.get('/api/attendance/notes')
      setNotes(Object.fromEntries(savedNotes.map(note => [note.date, note])))
      setNoteError('')
    } catch {
      setNoteError('Could not load attendance notes.')
    } finally {
      setNotesLoading(false)
    }
  }
  function saveNote(note) {
    setNotes(current => ({ ...current, [note.date]: note }))
    setNoteError('')
  }
  function deleteNote(date) {
    setNotes(current => {
      const next = { ...current }
      delete next[date]
      return next
    })
  }
  useEffect(() => { load().catch(() => {}) }, [])
  return <section className="panel table-panel"><span className="eyebrow">MY ATTENDANCE</span><h2>Attendance history</h2>{noteError && <div className="error-box" role="alert">{noteError}</div>}<div className="table-scroll"><table><thead><tr><th>Date</th><th>Check in</th><th>Check-in location</th><th>Check out</th><th>Check-out location</th><th>Status</th><th>Attendance note</th><th>Undo Events</th></tr></thead><tbody>{items.map(item => <tr key={item.attendance_id}><td><b>{formatIndiaDate(item.date)}</b></td><td>{item.check_in_time ? `${formatKolkataTime(item.check_in_time)} IST` : '-'}</td><td>{formatLocation(item.check_in_location)}</td><td>{item.check_out_time ? `${formatKolkataTime(item.check_out_time)} IST` : 'Open'}</td><td>{formatLocation(item.check_out_location)}</td><td><span className={statusBadgeClass(item.final_status)}>{item.final_status}</span></td><td><EmployeeAttendanceNote date={item.date} note={notes[item.date]} loading={notesLoading} onSaved={saveNote} onDeleted={deleteNote}/></td><td><EmployeeAttendanceUndo record={item} onRefresh={setItems}/></td></tr>)}</tbody></table></div></section>
}
