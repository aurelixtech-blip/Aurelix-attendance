import { useEffect, useState } from 'react'
import { Check, MessageSquare, Send } from 'lucide-react'
import api from './services/api'

function responseError(error, fallback) {
  const detail = error.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(item => item.msg).join(' ')
  return detail || fallback
}

export function EmployeePrivateComment() {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [message, setMessage] = useState('')
  const [error, setError] = useState('')
  const lineCount = comment ? comment.split(/\r?\n/).length : 0
  const valid = Boolean(comment.trim()) && lineCount <= 7

  async function submit(event) {
    event.preventDefault()
    setMessage('')
    setError('')
    if (!valid) {
      setError(!comment.trim() ? 'Enter a comment before submitting.' : 'Comments can contain up to 7 lines.')
      return
    }
    setBusy(true)
    try {
      const { data } = await api.post('/api/comments', { comment })
      setComment('')
      setMessage(data.message || 'Comment submitted successfully.')
    } catch (requestError) {
      setError(responseError(requestError, 'Could not submit your comment. Please try again.'))
    } finally {
      setBusy(false)
    }
  }

  return <section className="panel private-comment-panel" aria-labelledby="private-comment-heading">
    <div className="panel-heading"><div><span className="eyebrow">PRIVATE TO ADMIN</span><h3 id="private-comment-heading">Add a private comment</h3></div><MessageSquare size={18} aria-hidden="true"/></div>
    <p className="muted">Share a note with your administrator. You can submit a comment at any time; it is not connected to attendance check-in or check-out.</p>
    <form className="private-comment-form" onSubmit={submit}>
      <label htmlFor="employee-private-comment">Your comment</label>
      <textarea id="employee-private-comment" value={comment} maxLength={500} rows={4} onChange={event => { setComment(event.target.value); setMessage(''); setError('') }} aria-describedby="private-comment-counter private-comment-status" />
      <div id="private-comment-counter" className="private-comment-counter">{comment.length}/500 characters · {lineCount}/7 lines</div>
      {message && <div id="private-comment-status" className="success-box" role="status"><Check size={16}/>{message}</div>}
      {error && <div id="private-comment-status" className="error-box" role="alert">{error}</div>}
      <button className="primary-button" type="submit" disabled={busy || !valid}><Send size={16}/>{busy ? 'Submitting...' : 'Submit Comment'}</button>
    </form>
  </section>
}

export function AdminPrivateComments() {
  const [comments, setComments] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let active = true
    api.get('/api/comments')
      .then(({ data }) => { if (active) setComments(data) })
      .catch(requestError => { if (active) setError(responseError(requestError, 'Could not load private comments.')) })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [])

  return <section className="panel admin-private-comments" aria-labelledby="admin-private-comments-heading">
    <div className="panel-heading"><div><span className="eyebrow">ADMIN ONLY</span><h3 id="admin-private-comments-heading">Private employee comments</h3></div></div>
    {loading && <p className="muted" role="status">Loading private comments...</p>}
    {error && <div className="error-box" role="alert">{error}</div>}
    {!loading && !error && comments.length === 0 && <div className="empty-state">No private comments have been submitted.</div>}
    {!loading && !error && comments.length > 0 && <div className="private-comment-list">{comments.map((item, index) => <article className="private-comment-entry" key={`${item.employee_id}-${item.submitted_at}-${index}`}>
      <div className="private-comment-meta"><strong>{item.employee_name}</strong><span>{item.employee_id}</span><time dateTime={item.submitted_at}>{new Date(item.submitted_at).toLocaleString()}</time></div>
      <p>{item.comment}</p>
    </article>)}</div>}
  </section>
}
