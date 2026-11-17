/** Shared fixtures for the chat approval card tests (ChatInput.approval*.test.tsx). */
import { screen, fireEvent } from '@testing-library/react'
import { vi, expect } from 'vitest'
import type { RootState } from '../store'
export const NATIVE = { origin: 'native', id: 'ap-123', slot: 'slot-1', mid: 'mid-123' }
/** A pending spawn's coordinator request. */
export const spawnTarget = (agent: string) => ({ origin: 'coordinator' as const, id: `spawn:${agent}`, slot: 'slot-1', instance: `inst-${agent}` })

export const defaultProps = {
  value: '',
  onChange: vi.fn(),
  onSend: vi.fn(),
}

/** Open the reject dropdown and click one of its two tiers. */
export function rejectVia(label: 'Reject once' | 'Reject all') {
  fireEvent.click(screen.getByRole('button', { name: 'Reject' }))
  const item = screen.getAllByRole('menuitem').find(b => b.textContent?.startsWith(label))!
  expect(item).toBeTruthy()
  fireEvent.click(item)
}

/** The approval row's controls in order, by visible label. */
export function barControls(): string[] {
  const row = document.querySelector('[data-approval-actions]')!
  return Array.from(row.querySelectorAll('button')).map(b => b.textContent || '')
}

/** The bar's Trust control holds its slot, disabled, and grants nothing. */
export function expectTrustHeldDisabled() {
  expect(screen.getByRole('button', { name: 'Trust' })).toBeDisabled()
  expect(barControls()).toEqual(['Reject', 'Trust', 'Allow once'])
}

/** Open the Trust menu and return its scope rows (the duration choices are
 *  `menuitemradio`, so they are not among them). */
export function openTrust(): HTMLElement[] {
  fireEvent.click(screen.getByRole('button', { name: 'Trust' }))
  return screen.getAllByRole('menuitem')
}

/** Choose a lifetime in the open Trust menu by the start of its label. */
export function chooseDuration(label: '1 hour' | 'Until midnight' | 'This session') {
  fireEvent.click(screen.getAllByRole('menuitemradio').find(b => b.textContent?.startsWith(label))!)
}

/** Click the open Trust menu's row with exactly this text. */
export function clickTrustRow(text: string) {
  fireEvent.click(screen.getAllByRole('menuitem').find(b => b.textContent === text)!)
}

/** Grant all tools: the widest row takes a second click. */
export function trustAllTools() {
  openTrust()
  clickTrustRow('All tools — for this session')
  clickTrustRow('Click again to confirm — every tool runs without asking')
}

export function stateWithApproval(meta: Record<string, unknown> = {}): Partial<RootState> {
  return {
    chat: {
      activeSlot: 'slot-1',
      messages: [
        { role: 'user', content: 'list files' },
        {
          role: 'permission',
          content: 'Running: ls /tmp',
          meta: {
            approval_id: 'ap-123',
            // The runner's row identity: with the slot, it names the request.
            mid: 'mid-123',
            request_id: 'req-123',
            tool_input: '{"command":"ls /tmp"}',
            is_read_only: '1',
            tool_title: 'Running: ls /tmp',
            is_shell: '1',
            full_command: 'ls /tmp',
            base_command: 'ls',
            trust_command_grantable: '1',
            trust_base_grantable: '1',
            trust_grantable: '1',
            tool_call_id: 'tc-1',
          },
          ...meta,
        },
      ],
      toolLog: [],
      slotStatusDetail: {},
    } as unknown as RootState['chat'],
    dashboard: {
      slots: [{ key: 'slot-1', messages: 2, running: true, pending_approval: true, waiting_for_input: false, last_activity_ts: undefined }],
      approvalMode: 'normal',
      connected: true,
      channelTrusted: false,
      refreshTrigger: 0,
      unreadSlots: [],
      updateProgress: null,
    } as unknown as RootState['dashboard'],
  }
}
