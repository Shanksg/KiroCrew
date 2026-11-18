/**
 * Slack owner claim: the direct-local window in which the person sends the bot
 * a DM and confirms themselves as the owner (`/api/slack/owner-claim`).
 */

import type { ClientTransport } from './transport'

/** One sender offered for confirmation. */
export interface SlackOwnerClaimCandidate {
  user_id: string
  display_name: string
  handle: string
  /** An `https://` avatar URL, or "". */
  avatar: string
  team_name: string
  /** The message text the sender typed, cut to a few hundred characters. */
  text: string
  /** Seconds since the epoch. */
  received_at: number
}

/** GET /api/slack/owner-claim. */
export interface SlackOwnerClaimState {
  open: boolean
  /** Seconds since the epoch; 0 when closed. */
  expires_at: number
  team_name: string
  /** Newest first. */
  candidates: SlackOwnerClaimCandidate[]
  /** More than one person sent a message: the page warns before Confirm. */
  multiple: boolean
  ignored: number
  /** The owner this gateway process confirmed, if any. */
  confirmed: { user_id: string; handle: string; team_name: string } | null
}

export function createSlackOwnerClaimEndpoints({ get, post, j }: ClientTransport) {
  const ownerClaim = {
    getSlackOwnerClaim: () => get('/api/slack/owner-claim').then(j) as Promise<SlackOwnerClaimState>,
    startSlackOwnerClaim: () => post('/api/slack/owner-claim/start', {}).then(j) as Promise<SlackOwnerClaimState>,
    cancelSlackOwnerClaim: () => post('/api/slack/owner-claim/cancel', {}).then(j) as Promise<SlackOwnerClaimState>,
    confirmSlackOwnerClaim: (userId: string) =>
      post('/api/slack/owner-claim/confirm', { user_id: userId }).then(j) as Promise<{ owner_id: string; connected: boolean; connect_error: string }>,
  }
  return { ownerClaim }
}
