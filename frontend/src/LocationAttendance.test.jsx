// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import api from './services/api'
import LocationAttendance from './LocationAttendance'
import EmployeeAttendanceHistory from './EmployeeAttendanceHistory'
import { formatIndiaDate, kolkataDateKey } from './time'

vi.mock('./services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))

function previousDate(date) {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCDate(value.getUTCDate() - 1)
  return value.toISOString().slice(0, 10)
}

describe('employee daily attendance dashboard', () => {
  beforeEach(() => vi.clearAllMocks())
  afterEach(() => cleanup())

  it("shows today's summary and controls without rendering historical attendance", async () => {
    const today = kolkataDateKey()
    const yesterday = previousDate(today)
    api.get.mockImplementation(url => Promise.resolve({
      data: url.endsWith('/notes')
        ? [
            { date: yesterday, content: 'Historical-only note' },
            { date: today, content: 'Today-only note' },
          ]
        : [
            {
              attendance_id: 'yesterday',
              date: yesterday,
              final_status: 'ABSENT',
              check_in_location: { latitude: 1, longitude: 1, area: 'Yesterday area' },
            },
            {
              attendance_id: 'today',
              date: today,
              final_status: 'PRESENT',
              check_in_time: '2026-10-23T03:30:00Z',
              check_in_location: { latitude: 1, longitude: 1, area: 'Today area' },
            },
          ],
    }))

    render(<LocationAttendance/>)

    expect(await screen.findByText('Today-only note')).toBeTruthy()
    expect(screen.getByText("Today's status")).toBeTruthy()
    expect(screen.getByText('PRESENT')).toBeTruthy()
    expect(screen.getByText('Today area')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Check Out' })).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Undo Check In' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Undo Check Out' })).toBeNull()
    expect(screen.queryByText(formatIndiaDate(yesterday))).toBeNull()
    expect(screen.queryByText('Yesterday area')).toBeNull()
    expect(screen.queryByText('Historical-only note')).toBeNull()
    expect(screen.queryByText('Attendance history')).toBeNull()
  })

  it('shows Undo Check Out only after checkout exists and enforces undo order', async () => {
    const today = kolkataDateKey()
    api.get.mockImplementation(url => Promise.resolve({
      data: url.endsWith('/notes') ? [] : [{
        attendance_id: 'today-attendance',
        date: today,
        final_status: 'PRESENT',
        check_in_time: '2026-10-23T03:30:00Z',
        check_out_time: '2026-10-23T12:00:00Z',
      }],
    }))

    render(<LocationAttendance/>)

    const undoCheckIn = await screen.findByRole('button', { name: 'Undo Check In' })
    const undoCheckOut = screen.getByRole('button', { name: 'Undo Check Out' })
    expect(undoCheckIn.disabled).toBe(true)
    expect(undoCheckOut.disabled).toBe(false)
  })

  it('does not show undo controls when today has no attendance record', async () => {
    api.get.mockImplementation(url => Promise.resolve({ data: [] }))

    render(<LocationAttendance/>)

    expect(await screen.findByText('READY')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Undo Check In' })).toBeNull()
    expect(screen.queryByRole('button', { name: 'Undo Check Out' })).toBeNull()
  })

  it('selects today by the Asia/Kolkata date rather than the UTC date', async () => {
    const previousNow = Date.now()
    vi.setSystemTime(new Date('2026-10-22T20:00:00Z'))
    const today = kolkataDateKey()
    const yesterday = previousDate(today)
    api.get.mockImplementation(url => Promise.resolve({
      data: url.endsWith('/notes') ? [] : [
        {
          attendance_id: 'yesterday-attendance',
          date: yesterday,
          final_status: 'PRESENT',
          check_in_time: '2026-10-22T03:30:00Z',
          check_in_location: { latitude: 1, longitude: 1, area: 'Yesterday area' },
        },
        {
          attendance_id: 'kolkata-today-attendance',
          date: today,
          final_status: 'PRESENT',
          check_in_time: '2026-10-23T03:30:00Z',
          check_in_location: { latitude: 2, longitude: 2, area: 'Kolkata today area' },
        },
      ],
    }))

    try {
      render(<LocationAttendance/>)
      expect(await screen.findByText('Kolkata today area')).toBeTruthy()
      expect(screen.queryByText('Yesterday area')).toBeNull()
      expect(screen.getByRole('button', { name: 'Undo Check In' })).toBeTruthy()
    } finally {
      vi.setSystemTime(previousNow)
    }
  })

  it('moves the dashboard to the new Kolkata date without deleting the previous record', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-10-22T18:29:30Z'))
    const previousDay = kolkataDateKey()
    const nextDay = '2026-10-23'
    const previousRecord = {
      attendance_id: 'previous-day-attendance',
      date: previousDay,
      final_status: 'PRESENT',
      check_in_time: '2026-10-22T03:30:00Z',
      check_in_location: { latitude: 1, longitude: 1, area: 'Previous day area' },
    }
    api.get.mockImplementation(url => Promise.resolve({
      data: url.endsWith('/notes') ? [] : [previousRecord],
    }))

    try {
      render(<LocationAttendance/>)
      await act(async () => {
        await Promise.resolve()
        await Promise.resolve()
      })
      expect(screen.getByText('PRESENT')).toBeTruthy()
      expect(screen.getByRole('button', { name: 'Undo Check In' })).toBeTruthy()

      await act(async () => {
        vi.setSystemTime(new Date('2026-10-22T18:30:30Z'))
        await vi.advanceTimersByTimeAsync(60_000)
      })

      expect(screen.getByText('READY')).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Undo Check In' })).toBeNull()
      expect(screen.queryByText('Previous day area')).toBeNull()
      expect(previousRecord.date).toBe(previousDay)
      expect(nextDay).not.toBe(previousDay)
    } finally {
      vi.useRealTimers()
    }
  })

  it('refreshes the main status after undo and My History loads the same backend record', async () => {
    const user = userEvent.setup()
    const today = kolkataDateKey()
    const yesterday = previousDate(today)
    const historicalRecord = {
      attendance_id: 'yesterday-attendance',
      date: yesterday,
      final_status: 'PRESENT',
      check_in_time: '2026-10-22T03:30:00Z',
    }
    const checkedOutRecord = {
      attendance_id: 'today-attendance',
      date: today,
      final_status: 'PRESENT',
      check_in_time: '2026-10-23T03:30:00Z',
      check_in_location: { latitude: 1, longitude: 1, area: 'Today check-in area' },
      check_out_time: '2026-10-23T12:00:00Z',
      check_out_location: { latitude: 2, longitude: 2, area: 'Today check-out area' },
    }
    const checkedInRecord = { ...checkedOutRecord, check_out_time: null, check_out_location: null }
    let records = [checkedOutRecord, historicalRecord]
    api.get.mockImplementation(url => Promise.resolve({
      data: url.endsWith('/notes') ? [] : records,
    }))
    api.post.mockImplementation(async () => {
      records = [checkedInRecord, historicalRecord]
      return { data: { success: true } }
    })

    const dashboard = render(<LocationAttendance/>)
    await user.click(await screen.findByRole('button', { name: 'Undo Check Out' }))
    const dialog = screen.getByRole('dialog', { name: 'Undo Check Out?' })
    await user.click(within(dialog).getByRole('button', { name: 'Undo Check Out' }))

    await waitFor(() => {
      expect(screen.getByText('Open')).toBeTruthy()
      expect(screen.queryByRole('button', { name: 'Undo Check Out' })).toBeNull()
    })
    expect(screen.getByText('Today check-in area')).toBeTruthy()
    expect(screen.queryByText('Today check-out area')).toBeNull()
    expect(api.post).toHaveBeenCalledWith('/api/attendance/mine/today-attendance/undo-check-out')
    expect(api.get).toHaveBeenCalledTimes(3)

    dashboard.unmount()
    render(<EmployeeAttendanceHistory/>)
    const todayRow = (await screen.findByText(formatIndiaDate(today))).closest('tr')
    const yesterdayRow = screen.getByText(formatIndiaDate(yesterday)).closest('tr')
    expect(within(todayRow).getByText('Open')).toBeTruthy()
    expect(within(todayRow).getByText('Today check-in area')).toBeTruthy()
    expect(within(yesterdayRow).getByText(formatIndiaDate(yesterday))).toBeTruthy()
  })
})
