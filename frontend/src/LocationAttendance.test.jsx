// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import api from './services/api'
import LocationAttendance from './LocationAttendance'
import { formatIndiaDate, kolkataDateKey } from './time'

vi.mock('./services/api', () => ({ default: { get: vi.fn() } }))

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
    expect(screen.queryByText(formatIndiaDate(yesterday))).toBeNull()
    expect(screen.queryByText('Yesterday area')).toBeNull()
    expect(screen.queryByText('Historical-only note')).toBeNull()
    expect(screen.queryByText('Attendance history')).toBeNull()
  })
})
