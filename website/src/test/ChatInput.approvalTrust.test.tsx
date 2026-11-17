import { describe, it, expect, vi, beforeEach, onTestFinished } from 'vitest'

vi.mock("@radix-ui/react-dropdown-menu", async () => await import("./__mocks__/@radix-ui/react-dropdown-menu"))
vi.mock("@radix-ui/react-popover", async () => await import("./__mocks__/@radix-ui/react-popover"))

import { act, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { renderWithProviders, renderHookWithProviders, createTestStore } from './helpers'
import ChatInput from '../components/ChatInput'
import { useToolApproval } from '../components/chat-input/approval'
import { api } from '../api/client'
import { resolveApprovalRow, sseChatMessage } from '../store/chatSlice'
import { SlotProvider } from '../providers/SlotContext'
import { defaultProps, rejectVia, openTrust, chooseDuration, clickTrustRow, trustAllTools, stateWithApproval } from './approvalCardFixtures'

vi.mock('../api/client', () => {
  // Declared INSIDE the factory: vi.mock is hoisted above module-level
  // declarations, so a top-level class here would be a TDZ error.
  //
  // Must be a real class: ChatInput narrows the rejection with
  // `err instanceof ApiError` to tell an orphaned approval (404) from a
  // genuine transport failure. A bare object stub makes that check false.
  class MockApiError extends Error {
    readonly status: number
    readonly body: string
    constructor(status: number, message: string, body = '') {
      super(message)
      this.name = 'ApiError'
      this.status = status
      this.body = body
    }
  }
  return {
    api: {
      resolveApproval: vi.fn(() => Promise.resolve({})),
      decideApproval: vi.fn(() => Promise.resolve({})),
      approveChatSlot: vi.fn(() => Promise.resolve({})),
      spawnList: vi.fn(() => Promise.resolve({ agents: [] })),
      // A personal Crew by default: no Shared Crew capability is reported.
      sharedCrewCapabilities: vi.fn(() => Promise.resolve(null)),
      sharedCrewMode: vi.fn(() => Promise.resolve(null)),
      sharedCrewPeople: vi.fn(() => Promise.resolve(null)),
      sharedCrewViewer: vi.fn(() => Promise.resolve(null)),
    },
    ApiError: MockApiError,
  }
})

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.sharedCrewCapabilities).mockResolvedValue(null as never)
  vi.mocked(api.sharedCrewMode).mockResolvedValue(null as never)
  vi.mocked(api.sharedCrewPeople).mockResolvedValue(null as never)
})

// The Trust menu's rows and what each sends, and the receipt a decision leaves.
// Split from ChatInput.approval.test.tsx, which shares its fixtures.
describe('ChatInput approval Trust menu and receipts', () => {
  it('Trust dropdown trust_command calls approveChatSlot with pattern', async () => {
    const store = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    openTrust()
    clickTrustRow('This exact command — for this session')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_command', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'ls /tmp' }
      )
    })
    expect(api.decideApproval).not.toHaveBeenCalled()
  })

  it('Trust dropdown trust_base calls approveChatSlot with glob pattern', async () => {
    const store = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    openTrust()
    clickTrustRow('Any ls command — for this session')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_base', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'ls *' }
      )
    })
    expect(api.decideApproval).not.toHaveBeenCalled()
  })

  it('Trust dropdown timed command row sends ttl_seconds and says when it ends', async () => {
    const store = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    openTrust()
    chooseDuration('1 hour')
    clickTrustRow('This exact command — for 1 hour')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_command', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'ls /tmp', ttl_seconds: 3600 }
      )
    })
    expect(api.resolveApproval).not.toHaveBeenCalled()
    const chip = await screen.findByText('ls /tmp', { selector: '[role="status"] span' })
    // A landed grant wears the Approved pill, never the warning lock of a refusal.
    const notice = chip.closest('[role="status"]')!
    expect(notice.textContent).toMatch(/^ApprovedTrusted ls \/tmp until .+, then it asks again$/)
    expect(within(notice as HTMLElement).getByText('Approved')).toBeInTheDocument()
    expect(notice.querySelector('svg.lucide-lock')).toBeNull()
  })

  it('Trust dropdown timed command row shows a command ending in a closing quote once (#23893)', async () => {
    const cmd = 'echo “hi”'
    const state = stateWithApproval()
    const msg = state.chat!.messages[1]
    msg.content = `Running: ${cmd}`
    msg.meta = { ...msg.meta, tool_input: JSON.stringify({ command: cmd }), tool_title: `Running: ${cmd}`, full_command: cmd, base_command: 'echo' }
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    openTrust()
    chooseDuration('1 hour')
    clickTrustRow('This exact command — for 1 hour')
    const chip = await screen.findByText(cmd, { selector: '[role="status"] span' })
    expect(chip.closest('[role="status"]')!.textContent).toMatch(/^ApprovedTrusted echo “hi” until .+, then it asks again$/)
  })

  it('Trust dropdown timed family row names the family it trusted', async () => {
    const store = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    openTrust()
    chooseDuration('1 hour')
    clickTrustRow('Any ls command — for 1 hour')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_base', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'ls *', ttl_seconds: 3600 }
      )
    })
    expect(await screen.findByText(/^Trusted any “ls” command until .+, then it asks again$/)).toBeInTheDocument()
  })

  it('Trust dropdown entire tool calls approveChatSlot with trust action', async () => {
    const store = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    openTrust()
    // The widest grant takes a second click: the first only arms the row,
    // which then says what the next click does. Nothing is sent yet.
    clickTrustRow('All tools — for this session')
    expect(api.approveChatSlot).not.toHaveBeenCalled()
    expect(screen.getAllByRole('menuitem').map(b => b.textContent)).not.toContain('All tools — for this session')
    clickTrustRow('Click again to confirm — every tool runs without asking')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123' }
      )
    })
    expect(api.decideApproval).not.toHaveBeenCalled()
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent('ApprovedTrusted all tools for the rest of this session')
  })

  it('choosing a lifetime disarms the all-tools row', () => {
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    openTrust()
    // A rule under the rows the duration covers, and a heading over the session-long ones.
    const menu = screen.getByRole('menu')
    const order = Array.from(menu.querySelectorAll('[role="menuitem"], [role="separator"], [data-testid="trust-session-rows"]')).map(el => el.getAttribute('role') === 'separator' ? '---' : el.textContent)
    expect(order).toEqual(['This exact command — for this session', 'Any ls command — for this session', '---', 'Until this chat ends', 'Read-only commands — for this session', 'All tools — for this session'])
    clickTrustRow('All tools — for this session')
    chooseDuration('Until midnight')
    expect(screen.getAllByRole('menuitem').map(b => b.textContent)).toEqual([
      'This exact command — until midnight',
      'Any ls command — until midnight',
      // The lifetime above covers the two command rows only.
      'Read-only commands — for this session',
      'All tools — for this session',
    ])
    expect(api.approveChatSlot).not.toHaveBeenCalled()
  })

  it('the lone all-tools control also takes a second click', async () => {
    // One tier means no menu, so the control IS the grant. It arms first.
    const state = stateWithApproval()
    const meta = state.chat!.messages[1].meta!
    delete meta.trust_command_grantable
    delete meta.trust_base_grantable
    delete meta.full_command
    delete meta.base_command
    meta.is_read_only = ''
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    fireEvent.click(screen.getByRole('button', { name: 'Trust all tools for this session' }))
    expect(api.approveChatSlot).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Click again to confirm — every tool runs without asking' }))
    await waitFor(() => expect(api.approveChatSlot).toHaveBeenCalledWith('slot-1', 'trust', expect.objectContaining({ request_id: 'ap-123' })))
  })

  it.each([
    ['This exact command — for this session', 'ApprovedTrusted ls /tmp for the rest of this session'],
    ['Any ls command — for this session', 'ApprovedTrusted any “ls” command for the rest of this session'],
    ['Read-only commands — for this session', 'ApprovedTrusted read-only commands for the rest of this session'],
  ])('after %s, one line says what was granted and that it ends with the session', async (row, line) => {
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    openTrust()
    clickTrustRow(row)
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent(line)
  })

  it('Allow once and both rejections each leave a receipt under their own verdict', async () => {
    const first = renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent('ApprovedAllowed once — Kiro Crew asks again next time')
    first.unmount()
    const second = renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    rejectVia('Reject once')
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent('RejectedRejected once — Kiro Crew keeps asking about the rest')
    second.unmount()
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    rejectVia('Reject all')
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent('RejectedRejected all — Kiro Crew refuses the rest without asking')
  })

  it('a one-shot receipt gives way to the next card, a standing grant\'s stays', async () => {
    const next = (store: ReturnType<typeof createTestStore>) => act(() => {
      store.dispatch(sseChatMessage({ slot: 'slot-1', role: 'permission', content: 'Running: ls /var', meta: { approval_id: 'ap-456', mid: 'mid-456', tool_input: '{"command":"ls /var"}', is_shell: '1', full_command: 'ls /var', base_command: 'ls', trust_command_grantable: '1', trust_base_grantable: '1', trust_grantable: '1', tool_call_id: 'tc-2' } }))
    })
    const oneShot = createTestStore(stateWithApproval())
    const first = renderWithProviders(<ChatInput {...defaultProps} />, { store: oneShot })
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await screen.findByTestId('approval-receipt')
    next(oneShot)
    await waitFor(() => expect(screen.queryByTestId('approval-receipt')).not.toBeInTheDocument())
    first.unmount()
    const standing = createTestStore(stateWithApproval())
    renderWithProviders(<ChatInput {...defaultProps} />, { store: standing })
    openTrust()
    clickTrustRow('Any ls command — for this session')
    const receiptPill = await waitFor(() => screen.getByTestId('approval-receipt').querySelector('[data-layout-id]')!)
    next(standing)
    await waitFor(() => expect(screen.getByTestId('approval-command')).toHaveTextContent('ls /var'))
    expect(screen.getByTestId('approval-receipt')).toHaveTextContent('Trusted any “ls” command')
    // The receipt's pill and the next card's pill answer different requests, so
    // they never share a layout stack: Framer Motion would fly the new pill out
    // of the receipt and hide the receipt's verdict.
    const ids = Array.from(document.querySelectorAll('[data-layout-id]')).map(el => el.getAttribute('data-layout-id'))
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)
    expect(receiptPill.getAttribute('data-layout-id')).toBe('approval-status-pill:slot-1:ap-123:mid-123')
  })

  it('a card pill and the receipt for the same request share one layout id', async () => {
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    const cardId = document.querySelector('[data-layout-id]')!.getAttribute('data-layout-id')
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    const receipt = await screen.findByTestId('approval-receipt')
    expect(receipt.querySelector('[data-layout-id]')!.getAttribute('data-layout-id')).toBe(cardId)
  })

  it('a standing grant\'s receipt does not follow the composer into another session', async () => {
    // One composer serves whichever session is active: "for the rest of this
    // session" over a different session's composer would name the wrong one.
    const state = stateWithApproval()
    // The second session's own (empty) transcript and activity, which a
    // non-active slot is read from.
    Object.assign(state.chat!, { slotMessages: { 'slot-2': [] }, slotActivity: {} })
    const store = createTestStore(state)
    const { rerender } = renderWithProviders(<SlotProvider slotId="slot-1"><ChatInput {...defaultProps} /></SlotProvider>, { store })
    trustAllTools()
    expect(await screen.findByTestId('approval-receipt')).toHaveTextContent('Trusted all tools')
    rerender(<SlotProvider slotId="slot-2"><ChatInput {...defaultProps} /></SlotProvider>)
    // Gone in the render that moves the composer: never drawn, even fading, over slot-2.
    expect(screen.queryByTestId('approval-receipt')).not.toBeInTheDocument()
  })

  /** An MCP call whose heading is the agent's own description. */
  function mcpStateWithPurpose() {
    const state = stateWithApproval()
    const msg = state.chat!.messages[1]
    msg.content = 'post_message'
    msg.meta = { ...msg.meta, is_shell: '', is_read_only: '', tool_name: 'post_message', mcp_server: 'slack-mcp', tool_purpose: 'Say hello to the team', tool_input: '{"channel":"#team"}', full_command: undefined, base_command: undefined, trust_command_grantable: undefined, trust_base_grantable: undefined }
    return state
  }

  it('names the tool itself on the card, not only the agent\'s description of it', () => {
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(mcpStateWithPurpose()) })
    expect(screen.getByTestId('approval-title')).toHaveTextContent('Say hello to the team')
    // The gateway's own name for the tool, beside the server it belongs to.
    expect(screen.getByTestId('approval-tool')).toHaveTextContent('post_message')
    expect(screen.getByTestId('approval-tool').className).toContain('font-mono')
  })

  it('heads the card with the tool label when simplified names are off', () => {
    localStorage.setItem('mc-chat-config', JSON.stringify({ simplifiedToolNames: false }))
    try {
      renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(mcpStateWithPurpose()) })
      expect(screen.getByTestId('approval-title')).not.toHaveTextContent('Say hello to the team')
    } finally {
      localStorage.removeItem('mc-chat-config')
    }
  })

  it('Trust dropdown until-midnight command row sends the seconds left in the day', async () => {
    vi.useFakeTimers({ toFake: ['Date'] })
    onTestFinished(() => { vi.useRealTimers() })
    vi.setSystemTime(new Date(2026, 10, 17, 22, 0, 0))
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    openTrust()
    chooseDuration('Until midnight')
    clickTrustRow('This exact command — until midnight')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_command', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'ls /tmp', ttl_seconds: 7200 }
      )
    })
  })

  it('Trust dropdown always-allow row grants the ref the server stamped on the card', async () => {
    const state = mcpStateWithPurpose()
    Object.assign(state.chat!.messages[1].meta!, { always_allow_tool_grantable: '1', always_allow_tool_ref: '@slack-mcp/post_message' })
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    openTrust()
    clickTrustRow('Always allow this tool')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'always_allow_tool', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: '@slack-mcp/post_message' }
      )
    })
  })

  it('Trust dropdown shows a refused always-allow row disabled, with its reason', () => {
    const state = mcpStateWithPurpose()
    Object.assign(state.chat!.messages[1].meta!, { always_allow_tool_refusal: 'asks_every_time' })
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    const row = openTrust().find(b => b.textContent?.startsWith('Always allow this tool'))!
    // Radix marks a disabled row aria-disabled; the test mock forwards the prop.
    expect(row.getAttribute('aria-disabled') === 'true' || row.hasAttribute('disabled')).toBe(true)
    expect(row.textContent!.length).toBeGreaterThan('Always allow this tool'.length)
    fireEvent.click(row)
    expect(api.approveChatSlot).not.toHaveBeenCalled()
  })

  it('keeps the command box under a description heading', () => {
    // The heading is agent-written; the exact command below it is what is consented to.
    const state = stateWithApproval()
    state.chat!.messages[1].meta!.tool_purpose = 'List the temp directory'
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    expect(screen.getByTestId('approval-title')).toHaveTextContent('List the temp directory')
    expect(screen.getByTestId('approval-command')).toHaveTextContent('ls /tmp')
  })

  it('an armed all-tools row does not carry over to another session\'s card', async () => {
    // One composer serves whichever session is active, so the Trust menu's
    // first-click consent must not survive a switch to another session's card.
    const state = stateWithApproval()
    const row = (state.chat!.messages as unknown as { meta: Record<string, unknown> }[])[1]
    const other = { ...row, meta: { ...row.meta, approval_id: 'ap-456', mid: 'mid-456', request_id: 'req-456', tool_call_id: 'tc-2' } }
    Object.assign(state.chat!, { slotMessages: { 'slot-2': [{ role: 'user', content: 'list files' }, other] }, slotActivity: {} })
    ;(state.dashboard!.slots as unknown[]).push({ key: 'slot-2', messages: 2, running: true, pending_approval: true, waiting_for_input: false, last_activity_ts: undefined })
    const store = createTestStore(state)
    const { rerender } = renderWithProviders(<SlotProvider slotId="slot-1"><ChatInput {...defaultProps} /></SlotProvider>, { store })
    openTrust()
    clickTrustRow('All tools — for this session')
    expect(screen.getByText('Click again to confirm — every tool runs without asking')).toBeInTheDocument()
    rerender(<SlotProvider slotId="slot-2"><ChatInput {...defaultProps} /></SlotProvider>)
    expect(screen.getByRole('button', { name: 'Trust' })).toBeEnabled()
    expect(screen.queryByText('Click again to confirm — every tool runs without asking')).not.toBeInTheDocument()
    expect(api.approveChatSlot).not.toHaveBeenCalled()
  })

  it('names an MCP tool grant by the tool the server keys it on', async () => {
    // A tool called with no arguments is grantable by its server and name, so
    // the row says that tool, never this one call.
    const state = stateWithApproval()
    const msg = state.chat!.messages[1]
    msg.content = 'list_channels'
    msg.meta = { ...msg.meta, is_shell: '', is_read_only: '', tool_name: 'list_channels', mcp_server: 'slack-mcp', tool_input: '', full_command: 'mcp__slack-mcp__list_channels', base_command: undefined, trust_base_grantable: undefined }
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(state) })
    const texts = openTrust().map(b => b.textContent)
    expect(texts).toContain('mcp__slack-mcp__list_channels — for this session')
    expect(texts.some(t => t?.includes('exact'))).toBe(false)
    clickTrustRow('mcp__slack-mcp__list_channels — for this session')
    await waitFor(() => {
      expect(api.approveChatSlot).toHaveBeenCalledWith(
        'slot-1', 'trust_command', { response_id: expect.stringMatching(/^r_/), request_id: 'ap-123', origin: 'native', request_mid: 'mid-123', pattern: 'mcp__slack-mcp__list_channels' }
      )
    })
  })

  /** Report the system "reduce motion" setting as on, for this test only. */
  function reduceMotion() {
    vi.stubGlobal('matchMedia', (query: string) => ({
      matches: query.includes('prefers-reduced-motion'), media: query, onchange: null,
      addListener: vi.fn(), removeListener: vi.fn(), addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: vi.fn(),
    }))
    onTestFinished(() => { vi.unstubAllGlobals() })
  }

  it('settles a decision with a calm fade by default', async () => {
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    expect(screen.getByTestId('approval-card-motion')).toHaveAttribute('data-motion', 'settle')
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    expect(await screen.findByTestId('approval-receipt')).toHaveAttribute('data-motion', 'settle')
  })

  it('swaps the card for its receipt at once when the reader asked for less motion', async () => {
    reduceMotion()
    renderWithProviders(<ChatInput {...defaultProps} />, { store: createTestStore(stateWithApproval()) })
    expect(screen.getByTestId('approval-card-motion')).toHaveAttribute('data-motion', 'instant')
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    expect(await screen.findByTestId('approval-receipt')).toHaveAttribute('data-motion', 'instant')
  })

  it('drops a receipt whose decision lands after the composer moved to another session', async () => {
    // The receipt names "this session"; over another session's composer it would name the wrong one.
    let land: (v: unknown) => void = () => {}
    vi.mocked(api.approveChatSlot).mockImplementationOnce(() => new Promise(r => { land = r }) as never)
    const state = stateWithApproval()
    Object.assign(state.chat!, { slotMessages: { 'slot-2': [] }, slotActivity: {} })
    const store = createTestStore(state)
    const { rerender } = renderWithProviders(<SlotProvider slotId="slot-1"><ChatInput {...defaultProps} /></SlotProvider>, { store })
    openTrust()
    clickTrustRow('This exact command — for this session')
    rerender(<SlotProvider slotId="slot-2"><ChatInput {...defaultProps} /></SlotProvider>)
    await act(async () => { land({}) })
    expect(screen.queryByTestId('approval-receipt')).not.toBeInTheDocument()
  })

  it('a trust verb sent for a coordinator request is a one-shot allow, and its receipt says so', async () => {
    // Defence in depth: the menu is not offered there, so drive the decision directly.
    const state = stateWithApproval()
    const row = (state.chat!.messages as unknown as { meta: Record<string, unknown> }[])[1]
    row.meta = { ...row.meta, mid: undefined, registry: 'coordinator', approval_target: { origin: 'coordinator', id: 'ap-123', slot: 'slot-1', instance: 'inst-123' } }
    const store = createTestStore(state)
    const { result } = renderHookWithProviders(() => useToolApproval({ slotId: 'slot-1', slotApprovalChrome: true, approvalMode: 'normal', dispatch: store.dispatch }), { store })
    await act(async () => { result.current.handleApprovalAction('trust') })
    expect(api.approveChatSlot).not.toHaveBeenCalled()
    expect(api.decideApproval).toHaveBeenCalledWith(expect.objectContaining({ origin: 'coordinator', id: 'ap-123' }), 'approve', expect.stringMatching(/^r_/))
    await waitFor(() => expect(result.current.approvalNoticeKind).toBe('granted'))
    renderWithProviders(<>{result.current.approvalNotice}</>)
    expect(document.body.textContent).toContain('Allowed once')
    expect(document.body.textContent).not.toContain('Trusted')
  })

  it('a one-shot receipt does not show under the next request when the resolve frame lands first', async () => {
    // Two requests in the slot; the card shows the last. This tab's own
    // approval_resolved frame moves the card to the other one before the
    // HTTP reply, so "Allowed once" would sit under a card still waiting.
    const state = stateWithApproval()
    const msgs = state.chat!.messages as unknown as { role: string; content: string; meta: Record<string, unknown> }[]
    const r1 = msgs[1]
    const r0 = { ...r1, content: 'Running: ls /var', meta: { ...r1.meta, approval_id: 'ap-100', mid: 'mid-100', request_id: 'req-100', tool_call_id: 'tc-0', tool_input: '{"command":"ls /var"}', full_command: 'ls /var' } }
    msgs.splice(1, 0, r0)
    const store = createTestStore(state)
    vi.mocked(api.decideApproval).mockImplementationOnce(async () => {
      act(() => { store.dispatch(resolveApprovalRow({ target: { origin: 'native', id: 'ap-123', slot: 'slot-1', mid: 'mid-123' }, decision: 'approved' })) })
      return {} as never
    })
    renderWithProviders(<ChatInput {...defaultProps} />, { store })
    expect(screen.getByTestId('approval-command')).toHaveTextContent('ls /tmp')
    fireEvent.click(screen.getByRole('button', { name: 'Allow once' }))
    await waitFor(() => expect(screen.getByTestId('approval-command')).toHaveTextContent('ls /var'))
    await act(async () => { await Promise.resolve() })
    expect(screen.queryByTestId('approval-receipt')).not.toBeInTheDocument()
  })

  it('a session switch drops the last session\'s card at once, not in a fade over the new composer', () => {
    const state = stateWithApproval()
    Object.assign(state.chat!, { slotMessages: { 'slot-2': [] }, slotActivity: {} })
    const store = createTestStore(state)
    const { rerender } = renderWithProviders(<SlotProvider slotId="slot-1"><ChatInput {...defaultProps} /></SlotProvider>, { store })
    expect(screen.getByTestId('approval-card-motion')).toBeInTheDocument()
    rerender(<SlotProvider slotId="slot-2"><ChatInput {...defaultProps} /></SlotProvider>)
    // Slot-2 has no request: slot-1's card, buttons and all, is gone in this render.
    expect(screen.queryByTestId('approval-card-motion')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Allow once' })).not.toBeInTheDocument()
  })
})
