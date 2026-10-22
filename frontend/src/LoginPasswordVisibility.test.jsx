// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import api from './services/api'
import { Login } from './App'

vi.mock('./services/api', () => ({ default: { post: vi.fn() } }))

describe('login password visibility control', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => cleanup())

  it('toggles visibility without changing the password or submitting the form', async () => {
    const user = userEvent.setup()
    api.post.mockResolvedValue({ data: { access_token: 'test-token', user: { role: 'employee' } } })
    render(<MemoryRouter><Login onLogin={vi.fn()}/></MemoryRouter>)

    const password = screen.getByPlaceholderText('Password')
    expect(password.type).toBe('password')
    await user.type(password, 'Correct-Horse-42')
    const show = screen.getByRole('button', { name: 'Show password' })

    await user.click(show)
    expect(password.type).toBe('text')
    expect(password.value).toBe('Correct-Horse-42')
    expect(api.post).not.toHaveBeenCalled()

    await user.click(screen.getByRole('button', { name: 'Hide password' }))
    expect(password.type).toBe('password')
    expect(password.value).toBe('Correct-Horse-42')
    await user.click(screen.getByRole('button', { name: 'Show password' }))
    expect(password.type).toBe('text')
    expect(password.value).toBe('Correct-Horse-42')
    expect(api.post).not.toHaveBeenCalled()

    await user.type(screen.getByPlaceholderText('you@aurelix.com'), 'employee@example.com')
    await user.click(screen.getByRole('button', { name: /Enter workspace/ }))
    expect(api.post).toHaveBeenCalledWith('/api/auth/login', {
      email: 'employee@example.com',
      password: 'Correct-Horse-42',
    })
  })

  it.each([
    [{ response: { status: 401 } }, 'Email or password not recognized. Check your credentials or use Forgot password.'],
    [{ response: { status: 403 } }, 'This account is inactive. Contact an administrator for help.'],
    [{ response: { status: 429 } }, 'Too many sign-in attempts. Wait a moment and try again.'],
    [{ response: { status: 500 } }, 'Sign-in service is temporarily unavailable. Please try again later.'],
    [{ request: {} }, 'Unable to sign in right now. Please check your connection.'],
  ])('shows an accurate login message for each failure type', async (failure, message) => {
    api.post.mockRejectedValue(failure)
    render(<MemoryRouter><Login onLogin={vi.fn()}/></MemoryRouter>)

    await userEvent.setup().type(screen.getByPlaceholderText('you@aurelix.com'), 'employee@example.com')
    await userEvent.setup().type(screen.getByPlaceholderText('Password'), 'Test-password-123')
    fireEvent.submit(screen.getByRole('button', { name: /Enter workspace/ }).closest('form'))

    expect(await screen.findByText(message)).toBeTruthy()
  })
})
