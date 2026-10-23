// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import api from './services/api'
import EmployeeAttendanceNote from './EmployeeAttendanceNote'

vi.mock('./services/api', () => ({ default: { post: vi.fn(), patch: vi.fn(), delete: vi.fn() } }))

const savedNote = { date: '2026-10-20', content: 'Arrived late due to transit' }

describe('employee attendance notes', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('creates a note in the full-screen editor without expiry UI', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn()
    api.post.mockResolvedValue({ data: savedNote })
    render(<EmployeeAttendanceNote date="2026-10-20" onSaved={onSaved} onDeleted={vi.fn()}/>)

    await user.click(screen.getByRole('button', { name: 'Add a note' }))
    const dialog = screen.getByRole('dialog')
    expect(within(dialog).getByRole('button', { name: 'Close note editor' })).toBeTruthy()
    const textarea = within(dialog).getByLabelText('Note')
    await user.type(textarea, savedNote.content)
    expect(within(dialog).getByText(`${savedNote.content.length}/1000 characters`)).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Save note' }))

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(savedNote))
    expect(api.post).toHaveBeenCalledWith('/api/attendance/notes/2026-10-20', { content: savedNote.content })
    expect(screen.getByText('Arrived late due to transit')).toBeTruthy()
    expect(screen.queryByText(/expires|automatically deleted/i)).toBeNull()
  })

  it('continues to display a legacy note whose expiry timestamp is in the past', () => {
    const legacyNote = { ...savedNote, expires_at: '2020-01-01T00:00:00Z' }
    render(<EmployeeAttendanceNote date={savedNote.date} note={legacyNote} onSaved={vi.fn()} onDeleted={vi.fn()}/>)

    expect(screen.getByText(savedNote.content)).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Edit note' })).toBeTruthy()
  })

  it('edits an active note without using the create endpoint and confirms deletion', async () => {
    const user = userEvent.setup()
    const onSaved = vi.fn()
    const onDeleted = vi.fn()
    api.patch.mockResolvedValue({ data: { ...savedNote, content: 'Corrected note' } })
    api.delete.mockResolvedValue({})
    const { rerender } = render(<EmployeeAttendanceNote date={savedNote.date} note={savedNote} onSaved={onSaved} onDeleted={onDeleted}/>)

    await user.click(screen.getByRole('button', { name: 'Edit note' }))
    const dialog = screen.getByRole('dialog')
    const textarea = within(dialog).getByLabelText('Note')
    fireEvent.change(textarea, { target: { value: 'Corrected note' } })
    await user.click(within(dialog).getByRole('button', { name: 'Save note' }))
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ content: 'Corrected note' })))
    expect(api.patch).toHaveBeenCalledWith('/api/attendance/notes/2026-10-20', { content: 'Corrected note' })
    expect(api.post).not.toHaveBeenCalled()

    rerender(<EmployeeAttendanceNote date={savedNote.date} note={savedNote} onSaved={onSaved} onDeleted={onDeleted}/>)
    await user.click(screen.getByRole('button', { name: 'Delete' }))
    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/api/attendance/notes/2026-10-20'))
    expect(window.confirm).toHaveBeenCalledOnce()
    expect(onDeleted).toHaveBeenCalledWith(savedNote.date)
  })
})
