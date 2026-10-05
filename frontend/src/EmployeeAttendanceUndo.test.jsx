// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import api from './services/api'
import EmployeeAttendanceHistory from './EmployeeAttendanceHistory'

vi.mock('./services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

const fullyCheckedInRecord = {
  attendance_id: 'att-1',
  employee_id: 'EMP-1',
  user_name: 'Test Employee',
  date: '2026-10-05',
  check_in_time: '2026-10-05T03:30:00Z',
  check_out_time: '2026-10-05T12:00:00Z',
}

describe('employee attendance history undo controls', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('requires confirmation for each undo and refreshes attendance after both', async () => {
    const user = userEvent.setup()
    const afterCheckOutUndo = { ...fullyCheckedInRecord, check_out_time: null }
    const afterCheckInUndo = { ...afterCheckOutUndo, check_in_time: null, final_status: 'ABSENT' }
    api.post.mockResolvedValue({ data: { success: true } })
    api.get
      .mockResolvedValueOnce({ data: [fullyCheckedInRecord] })
      .mockResolvedValueOnce({ data: [afterCheckOutUndo] })
      .mockResolvedValueOnce({ data: [afterCheckInUndo] })
    render(<EmployeeAttendanceHistory/>)

    const undoCheckIn = await screen.findByRole('button', { name: 'Undo Check In' })
    expect(undoCheckIn.disabled).toBe(true)
    expect(screen.getByRole('button', { name: 'Undo Check Out' }).disabled).toBe(false)
    await user.click(undoCheckIn)
    expect(screen.queryByRole('dialog')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Undo Check Out' }))
    const checkOutDialog = screen.getByRole('dialog', { name: 'Undo Check Out?' })
    await user.click(within(checkOutDialog).getByRole('button', { name: 'Cancel' }))
    expect(api.post).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Undo Check Out' }))
    const confirmCheckOutDialog = screen.getByRole('dialog', { name: 'Undo Check Out?' })
    await user.click(within(confirmCheckOutDialog).getByRole('button', { name: 'Undo Check Out' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Undo Check In' }).disabled).toBe(false))
    expect(api.post).toHaveBeenCalledWith('/api/attendance/mine/att-1/undo-check-out')

    expect(screen.getByRole('button', { name: 'Undo Check In' }).disabled).toBe(false)
    await user.click(screen.getByRole('button', { name: 'Undo Check In' }))
    const checkInDialog = screen.getByRole('dialog', { name: 'Undo Check In?' })
    expect(within(checkInDialog).getByText(/remove your check-in time, location, and check-in photo reference/)).toBeTruthy()
    await user.click(within(checkInDialog).getByRole('button', { name: 'Undo Check In' }))

    await waitFor(() => expect(screen.getByText('ABSENT')).toBeTruthy())
    expect(screen.queryByRole('button', { name: 'Undo Check In' })).toBeNull()
    expect(api.post).toHaveBeenLastCalledWith('/api/attendance/mine/att-1/undo-check-in')
    expect(api.get).toHaveBeenCalledTimes(3)
  })
})
