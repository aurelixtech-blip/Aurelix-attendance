import { useEffect, useState } from 'react'
import { Clock3, Edit3, Trash2, X } from 'lucide-react'
import api from './services/api'
import { formatIndiaDate } from './time'

const MAX_NOTE_LENGTH = 1000

function remainingTime(expiresAt, now) {
  const remaining = new Date(expiresAt).getTime() - now
  if (remaining <= 0) return 'Expired'
  const hours = Math.floor(remaining / 3600000)
  const minutes = Math.floor((remaining % 3600000) / 60000)
  return hours ? `${hours}h ${minutes}m remaining` : `${minutes}m remaining`
}

function expiryLabel(expiresAt) {
  return new Intl.DateTimeFormat('en-IN', {
    timeZone: 'Asia/Kolkata',
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(expiresAt))
}

export default function EmployeeAttendanceNote({ date, note, loading = false, onSaved, onDeleted }) {
  const [editing, setEditing] = useState(false)
  const [content, setContent] = useState('')
  const [editingExisting, setEditingExisting] = useState(false)
  const [currentNote, setCurrentNote] = useState(note)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())
  const visibleNote = currentNote && new Date(currentNote.expires_at).getTime() > Date.now() ? currentNote : null

  useEffect(() => setCurrentNote(note), [note])

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 15000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (!currentNote) return undefined
    const delay = new Date(currentNote.expires_at).getTime() - Date.now()
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(delay, 0))
    return () => window.clearTimeout(timer)
  }, [currentNote])

  useEffect(() => {
    if (!editing) return undefined
    const onKeyDown = event => {
      if (event.key === 'Escape' && !busy) setEditing(false)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [editing, busy])

  function openEditor() {
    setContent(visibleNote?.content || '')
    setEditingExisting(Boolean(visibleNote))
    setError('')
    setEditing(true)
  }

  async function save(event) {
    event.preventDefault()
    const cleanContent = content.trim()
    if (!cleanContent) {
      setError('Enter a note before saving.')
      return
    }
    setBusy(true)
    setError('')
    try {
      const request = editingExisting ? api.patch : api.post
      const { data } = await request(`/api/attendance/notes/${encodeURIComponent(date)}`, { content: cleanContent })
      setCurrentNote(data)
      onSaved(data)
      setEditing(false)
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'Could not save your note. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  async function remove() {
    if (!window.confirm(`Delete your attendance note for ${formatIndiaDate(date)}?`)) return
    setBusy(true)
    setError('')
    try {
      await api.delete(`/api/attendance/notes/${encodeURIComponent(date)}`)
      setCurrentNote(null)
      onDeleted(date)
    } catch (requestError) {
      setError(requestError.response?.data?.detail || 'Could not delete your note. Please try again.')
    } finally {
      setBusy(false)
    }
  }

  return <section className="attendance-note-section" aria-label={`Attendance note for ${formatIndiaDate(date)}`}>
    {loading ? <p className="attendance-note-empty">Loading note status...</p> : visibleNote ? <>
      <span className="attendance-note-label">Attendance note · {formatIndiaDate(date)}</span>
      <p className="attendance-note-content">{visibleNote.content}</p>
      <div className="attendance-note-status"><Clock3 size={14}/><span>Saved · {remainingTime(visibleNote.expires_at, now)} · expires {expiryLabel(visibleNote.expires_at)} IST</span></div>
      <div className="attendance-note-actions">
        <button className="ghost-button" type="button" onClick={openEditor} disabled={busy}><Edit3 size={15}/>Edit note</button>
        <button className="ghost-button attendance-note-delete" type="button" onClick={remove} disabled={busy}><Trash2 size={15}/>Delete</button>
      </div>
    </> : <>
      <span className="attendance-note-label">Attendance note · {formatIndiaDate(date)}</span>
      <p className="attendance-note-empty">No note saved for this attendance date.</p>
      <button className="ghost-button attendance-note-add" type="button" onClick={openEditor}>Add a note</button>
    </>}
    {error && !editing && <div className="error-box" role="alert"><X size={16}/>{error}</div>}
    {editing && <div className="attendance-note-editor-overlay" role="dialog" aria-modal="true" aria-labelledby={`attendance-note-title-${date}`}>
      <form className="attendance-note-editor" onSubmit={save}>
        <header className="attendance-note-editor-heading">
          <div><span className="eyebrow cyan">TEMPORARY ATTENDANCE NOTE</span><h2 id={`attendance-note-title-${date}`}>{visibleNote ? 'Edit your note' : 'Add a note'}</h2><p>{formatIndiaDate(date)} · Automatically deleted 24 hours after creation</p></div>
          <button className="icon-button" type="button" aria-label="Close note editor" onClick={() => setEditing(false)} disabled={busy}><X size={18}/></button>
        </header>
        <label className="attendance-note-field">Note<textarea value={content} onChange={event => setContent(event.target.value.slice(0, MAX_NOTE_LENGTH))} maxLength={MAX_NOTE_LENGTH} rows={8} autoFocus disabled={busy} placeholder="Add a short note about this attendance date..." /></label>
        <div className="attendance-note-count" aria-live="polite">{content.length}/{MAX_NOTE_LENGTH} characters</div>
        {error && <div className="error-box" role="alert"><X size={16}/>{error}</div>}
        <div className="attendance-note-editor-actions">
          <button className="ghost-button" type="button" onClick={() => setEditing(false)} disabled={busy}>Cancel</button>
          <button className="primary-button" type="submit" disabled={busy || !content.trim()}>{busy ? 'Saving...' : 'Save note'}</button>
        </div>
      </form>
    </div>}
  </section>
}
