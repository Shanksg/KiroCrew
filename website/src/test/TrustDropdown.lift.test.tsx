import { describe, it, expect, vi } from 'vitest'

// The shared menu mock, with the offset the menu is opened at kept on the DOM,
// so the lift the timed menu computes can be read back. Its trigger also hands
// the button to the child's own ref, as Radix's Slot composes refs: the lift is
// measured from that ref.
vi.mock('@radix-ui/react-dropdown-menu', async () => {
  const React = await import('react')
  const mock = await import('./__mocks__/@radix-ui/react-dropdown-menu')
  const Content = React.forwardRef<HTMLDivElement, { sideOffset?: number }>(({ sideOffset, ...props }, ref) =>
    React.createElement(mock.Content, { ...props, ref, 'data-side-offset': sideOffset }))
  const Trigger = React.forwardRef<HTMLButtonElement, { children: React.ReactElement }>(({ children, ...props }, ref) => {
    const childRef = (children as unknown as { ref?: React.Ref<HTMLButtonElement> }).ref
    const both = (node: HTMLButtonElement | null) => {
      for (const r of [ref, childRef]) {
        if (typeof r === 'function') r(node)
        else if (r) (r as React.MutableRefObject<HTMLButtonElement | null>).current = node
      }
    }
    return React.createElement(mock.Trigger, { ...props, ref: both }, children)
  })
  return { ...mock, Content, Trigger }
})

import { render, screen, fireEvent } from '@testing-library/react'
import TrustDropdown from '../components/TrustDropdown'

/** A box whose top edge sits at `top` px. */
const at = (top: number) => ({ top, bottom: top + 20, left: 0, right: 100, width: 100, height: 20, x: 0, y: top, toJSON: () => ({}) }) as DOMRect

describe('TrustDropdown timed menu lift', () => {
  it('opens far enough above its trigger to clear the command box', () => {
    // The exact-command row names no command, so the command the card shows
    // must stay readable while the menu is open: the menu lifts by the
    // distance from the command box's top to the trigger's top.
    const command = document.createElement('pre')
    command.getBoundingClientRect = () => at(200)
    render(<TrustDropdown fullCommand="ls /tmp" baseCommand="ls" isShell timedGrants keepClear={() => command} onAction={() => {}} />)
    const trigger = screen.getByRole('button', { name: 'Trust' })
    trigger.getBoundingClientRect = () => at(320)
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toHaveAttribute('data-side-offset', String(320 - 200 + 4))
  })

  it('does not lift when the command box is below the trigger or absent', () => {
    const command = document.createElement('pre')
    command.getBoundingClientRect = () => at(400)
    const { unmount } = render(<TrustDropdown fullCommand="ls /tmp" baseCommand="ls" isShell timedGrants keepClear={() => command} onAction={() => {}} />)
    const trigger = screen.getByRole('button', { name: 'Trust' })
    trigger.getBoundingClientRect = () => at(320)
    fireEvent.click(trigger)
    expect(screen.getByRole('menu')).toHaveAttribute('data-side-offset', '4')
    unmount()
    render(<TrustDropdown fullCommand="ls /tmp" baseCommand="ls" isShell timedGrants onAction={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'Trust' }))
    expect(screen.getByRole('menu')).toHaveAttribute('data-side-offset', '4')
  })
})
