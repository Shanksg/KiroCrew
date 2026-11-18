/**
 * Settings ▸ Slack ▸ "Claim in Slack" (`pages/settings/SlackOwnerClaim.tsx`).
 * The routes are direct-local only; the block must not even render for a
 * read-only remote viewer.
 */
import { describe, it, expect, vi, afterEach } from 'vitest'
import { screen, fireEvent, waitFor } from '@testing-library/react'

import { renderWithProviders } from './helpers'
import { api, type SlackConfigData, type SlackOwnerClaimState } from '../api/client'
import { SlackOwnerClaim } from '../pages/settings/SlackOwnerClaim'

const onOwner = vi.fn()

afterEach(() => {
  vi.restoreAllMocks()
  onOwner.mockClear()
})

function config(over: Partial<SlackConfigData> = {}): SlackConfigData {
  return {
    read_only: false,
    bot_token_set: true,
    app_token_set: true,
    owner_id: '',
    ...over,
  } as SlackConfigData
}

function state(over: Partial<SlackOwnerClaimState> = {}): SlackOwnerClaimState {
  return { open: false, expires_at: 0, team_name: '', candidates: [], multiple: false, ignored: 0, confirmed: null, ...over }
}

const candidate = (id: string, text: string) => ({
  user_id: id, display_name: `Name ${id}`, handle: `h${id}`, avatar: '', team_name: 'Acme', text, received_at: 1_700_000_000,
})

describe('SlackOwnerClaim', () => {
  it('renders nothing and reads nothing for a read-only viewer', () => {
    const get = vi.spyOn(api, 'getSlackOwnerClaim')
    const { container } = renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config({ read_only: true })} />)
    expect(container.querySelector('[data-testid="slack-owner-claim"]')).toBeNull()
    expect(screen.queryByTestId('slack-owner-claim-start')).toBeNull()
    expect(screen.queryByTestId('slack-owner-confirm')).toBeNull()
    expect(get).not.toHaveBeenCalled()
  })

  it('opens the window from the Claim button', async () => {
    vi.spyOn(api, 'getSlackOwnerClaim').mockResolvedValue(state())
    const start = vi.spyOn(api, 'startSlackOwnerClaim').mockResolvedValue(state({ open: true, expires_at: 1_700_000_600 }))
    renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config()} />)
    fireEvent.click(await screen.findByTestId('slack-owner-claim-start'))
    await waitFor(() => expect(start).toHaveBeenCalledOnce())
  })

  it('disables Claim until both tokens are saved', async () => {
    vi.spyOn(api, 'getSlackOwnerClaim').mockResolvedValue(state())
    renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config({ app_token_set: false })} />)
    expect((await screen.findByTestId('slack-owner-claim-start')).hasAttribute('disabled')).toBe(true)
  })

  it('lists candidates with their text, warns on more than one, and confirms the one clicked', async () => {
    vi.spyOn(api, 'getSlackOwnerClaim').mockResolvedValue(state({
      open: true,
      expires_at: 1_700_000_600,
      candidates: [candidate('U02', 'second'), candidate('U01', 'it is me')],
      multiple: true,
    }))
    const confirm = vi.spyOn(api, 'confirmSlackOwnerClaim').mockResolvedValue({ owner_id: 'U01', connected: true, connect_error: '' })
    renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config()} />)
    const rows = await screen.findAllByTestId('slack-owner-candidate')
    expect(rows).toHaveLength(2)
    expect(rows[1].textContent).toContain('it is me')
    expect(screen.getByTestId('slack-owner-claim-multiple')).toBeTruthy()
    fireEvent.click(screen.getAllByTestId('slack-owner-confirm')[1])
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('U01'))
    // The panel's owner draft follows, so a later Save does not undo the claim.
    await waitFor(() => expect(onOwner).toHaveBeenCalledWith('U01'))
  })

  it('shows a failed poll instead of stale candidates reading as current', async () => {
    vi.spyOn(api, 'getSlackOwnerClaim').mockRejectedValue(new Error('offline'))
    renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config()} />)
    expect(await screen.findByRole('alert')).toBeTruthy()
  })

  it('shows the owner with Clear once one is set, and Clear saves an empty owner', async () => {
    vi.spyOn(api, 'getSlackOwnerClaim').mockResolvedValue(state({ confirmed: { user_id: 'U01', handle: 'me', team_name: 'Acme' } }))
    const save = vi.spyOn(api, 'saveSlackConfig').mockResolvedValue({ ok: true, restart_required: true, verify_warning: '' })
    renderWithProviders(<SlackOwnerClaim onOwnerChange={onOwner} config={config({ owner_id: 'U01' })} />)
    expect(await screen.findByText(/@me/)).toBeTruthy()
    expect(screen.queryByTestId('slack-owner-claim-start')).toBeNull()
    fireEvent.click(screen.getByTestId('slack-owner-clear'))
    await waitFor(() => expect(save).toHaveBeenCalledWith({ owner_id: '' }))
    await waitFor(() => expect(onOwner).toHaveBeenCalledWith(''))
  })
})
