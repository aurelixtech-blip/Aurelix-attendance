// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import api from './services/api'
import { AdminPrivateComments, EmployeePrivateComment } from './PrivateComments'

vi.mock('./services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

describe('private employee comments', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it('submits a comment without attendance state and clears the form on success', async () => {
    const user = userEvent.setup()
    api.post.mockResolvedValue({ data: { message: 'Comment submitted successfully' } })
    render(<EmployeePrivateComment/>)

    const field = screen.getByLabelText('Your comment')
    await user.type(field, 'Please review my schedule.')
    await user.click(screen.getByRole('button', { name: 'Submit Comment' }))

    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/api/comments', { comment: 'Please review my schedule.' }))
    expect((await screen.findByRole('status')).textContent).toContain('Comment submitted successfully')
    expect(field.value).toBe('')
    expect(api.get).not.toHaveBeenCalled()
  })

  it('enforces the seven-line limit and character limit in the form', async () => {
    const user = userEvent.setup()
    render(<EmployeePrivateComment/>)
    const field = screen.getByLabelText('Your comment')

    fireEvent.change(field, { target: { value: Array(8).fill('line').join('\n') } })
    expect(screen.getByRole('button', { name: 'Submit Comment' }).disabled).toBe(true)
    expect(screen.getByText(/8\/7 lines/)).toBeTruthy()

    await user.clear(field)
    fireEvent.change(field, { target: { value: 'x'.repeat(500) } })
    expect(field.value).toHaveLength(500)
    expect(screen.getByText(/500\/500 characters/)).toBeTruthy()
  })

  it('shows submitted comments only in the admin comments panel', async () => {
    api.get.mockResolvedValue({ data: [{
      employee_id: 'EMP-1',
      employee_name: 'Employee One',
      comment: 'Private note',
      submitted_at: '2026-01-01T00:00:00+00:00',
    }] })

    render(<AdminPrivateComments/>)

    expect(await screen.findByText('Private note')).toBeTruthy()
    expect(screen.getByText('EMP-1')).toBeTruthy()
    expect(api.get).toHaveBeenCalledWith('/api/comments')
  })

  it('does not render placeholder entries when there are no comments', async () => {
    api.get.mockResolvedValue({ data: [] })

    render(<AdminPrivateComments/>)

    expect(await screen.findByText('No private comments have been submitted.')).toBeTruthy()
    expect(document.querySelectorAll('.private-comment-entry')).toHaveLength(0)
  })
})
