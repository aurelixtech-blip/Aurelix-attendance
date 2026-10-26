// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import api from './services/api'
import App from './App'

vi.mock('./services/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
vi.mock('./LocationAttendance', () => ({ default: () => <div>Employee Check In</div> }))
vi.mock('./EmployeeAttendanceHistory', () => ({ default: () => <div>Employee History</div> }))

describe('role-based route access', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.setItem('aurelix_token', 'session-token')
    api.get.mockImplementation(url => url === '/api/auth/me'
      ? Promise.resolve({ data: { employee_id: 'ADM-001', full_name: 'System Admin', department: 'Operations', role: 'admin' } })
      : Promise.resolve({ data: [] }))
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
  })

  it.each(['/attendance', '/history'])('redirects an admin from the employee route %s', async path => {
    render(<MemoryRouter initialEntries={[path]}><App /></MemoryRouter>)

    expect(await screen.findByText('Attendance command center')).toBeTruthy()
    expect(screen.queryByText('Employee Check In')).toBeNull()
    expect(screen.queryByText('Employee History')).toBeNull()
  })

  it('routes an employee to the employee attendance page', async () => {
    api.get.mockResolvedValueOnce({ data: { employee_id: 'EMP-1', full_name: 'Asha Rao', department: 'Operations', role: 'employee' } })
    render(<MemoryRouter initialEntries={['/attendance']}><App /></MemoryRouter>)

    expect(await screen.findByText('Employee Check In')).toBeTruthy()
  })

  it('rejects a current-user response with a missing role rather than treating it as an employee', async () => {
    api.get.mockResolvedValueOnce({ data: { employee_id: 'ADM-001', full_name: 'System Admin' } })
    render(<MemoryRouter initialEntries={['/attendance']}><App /></MemoryRouter>)

    expect(await screen.findByText('Your account role is missing or invalid. Contact an administrator.')).toBeTruthy()
    expect(localStorage.getItem('aurelix_token')).toBeNull()
    expect(screen.queryByText('Employee Check In')).toBeNull()
  })
})
