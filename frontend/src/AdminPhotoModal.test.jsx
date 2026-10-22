// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import api from './services/api'
import { AdminPhotoModal } from './App'

vi.mock('./services/api', () => ({ default: { get: vi.fn() } }))

const viewer = {
  attendanceId: 'attendance-1',
  event: 'check_in',
  employee: 'Employee One',
  date: '2026-10-20',
  timestamp: '2026-10-20T08:30:00Z',
}

describe('admin attendance photo note display', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:attendance-photo')
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  function mockPhotoAndNote(noteRequest) {
    api.get.mockImplementation(url => {
      if (url.endsWith('/photo?event=check_in') || url.endsWith('/photo?event=check_out')) {
        return Promise.resolve({ data: new Blob(['photo'], { type: 'image/jpeg' }) })
      }
      return noteRequest()
    })
  }

  it('shows the active note returned for the photo attendance record', async () => {
    mockPhotoAndNote(() => Promise.resolve({ data: {
      content: 'For this attendance date only',
      employee_name: 'Employee One',
      date: '2026-10-20',
      expires_at: new Date(Date.now() + 3600000).toISOString(),
    } }))
    render(<AdminPhotoModal viewer={viewer} onClose={vi.fn()}/>)

    expect(await screen.findByText('For this attendance date only')).toBeTruthy()
    expect(screen.getByText('Employee One · 20-10-2026')).toBeTruthy()
    expect(api.get).toHaveBeenCalledWith('/api/attendance/admin/attendance-1/note')
  })

  it('shows the matching active note when the check-out photo is opened', async () => {
    const checkoutViewer = { ...viewer, event: 'check_out' }
    api.get.mockImplementation(url => url.endsWith('/note')
      ? Promise.resolve({ data: { content: 'Checkout note', employee_name: 'Employee One', date: viewer.date, expires_at: new Date(Date.now() + 3600000).toISOString() } })
      : Promise.resolve({ data: new Blob(['photo'], { type: 'image/jpeg' }) }))
    render(<AdminPhotoModal viewer={checkoutViewer} onClose={vi.fn()}/>)

    expect(await screen.findByText('Checkout note')).toBeTruthy()
    expect(api.get).toHaveBeenCalledWith('/api/attendance/admin/attendance-1/photo?event=check_out', { responseType: 'blob' })
    expect(api.get).toHaveBeenCalledWith('/api/attendance/admin/attendance-1/note')
  })

  it('does not render a note section when no active note exists', async () => {
    const notFound = Object.assign(new Error('Not found'), { response: { status: 404 } })
    mockPhotoAndNote(() => Promise.reject(notFound))
    render(<AdminPhotoModal viewer={{ ...viewer, event: 'check_out' }} onClose={vi.fn()}/>)

    await waitFor(() => expect(api.get).toHaveBeenCalledWith('/api/attendance/admin/attendance-1/note'))
    expect(screen.queryByText('TEMPORARY ATTENDANCE NOTE')).toBeNull()
    expect(document.querySelector('.admin-attendance-note')).toBeNull()
  })
})
