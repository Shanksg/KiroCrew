import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { ConductorModeTitleMark, CrewGhostBadge, CrewModeComposerChip, CrewStatusDot, CrewWorkerStack } from './CrewModeMarks'
import { OPEN_DELAY_MS } from '../InstantTip'

const reducedMotion = vi.hoisted(() => ({ value: false }))
vi.mock('../../hooks/useReducedMotion', () => ({ useReducedMotion: () => reducedMotion.value }))

describe('CrewModeMarks', () => {
  it('draws the ghost badge on the crew gradient at the size asked', () => {
    render(<CrewGhostBadge size={20} />)
    const badge = screen.getByTestId('crew-ghost-badge')
    expect(badge.className).toContain('crew-grad')
    expect(badge.style.width).toBe('20px')
    expect(badge.getAttribute('aria-hidden')).toBe('true')
  })

  it('the status dot names the state in its tone, as text or for screen readers only', () => {
    const { rerender } = render(<CrewStatusDot group="approval" />)
    let dot = screen.getByTestId('crew-status')
    expect(dot.getAttribute('data-group')).toBe('approval')
    expect(dot.className).toContain('crew-tone-warn')
    expect(dot.textContent).toBe('Approval needed')
    expect(dot.querySelector('.sr-only')).toBeNull()

    rerender(<CrewStatusDot group="waiting" withLabel={false} />)
    dot = screen.getByTestId('crew-status')
    // A waiting worker's dot is hollow; the word stays for a screen reader.
    expect(dot.querySelector('[aria-hidden]')?.className).toContain('border-current')
    expect(dot.querySelector('.sr-only')?.textContent).toBe('Waiting')
  })

  it('the worker stack shows up to `max` ghosts in their tones and counts the rest', () => {
    render(<CrewWorkerStack groups={['approval', 'working', 'done', 'waiting']} max={3} />)
    const stack = screen.getByTestId('crew-worker-stack')
    const ghosts = stack.querySelectorAll('[data-group]')
    expect([...ghosts].map(g => g.getAttribute('data-group'))).toEqual(['approval', 'working', 'done'])
    expect(ghosts[0].className).toContain('crew-tone-warn')
    expect(stack.textContent).toContain('+1')
  })

  it('a full stack shows no count', () => {
    render(<CrewWorkerStack groups={['working']} />)
    expect(screen.getByTestId('crew-worker-stack').textContent).not.toContain('+')
  })

  it('the composer bolt is a read-only status mark', () => {
    render(<CrewModeComposerChip />)
    const mark = screen.getByTestId('crew-mode-composer-chip')
    // Read-only: a focusable anchor for its tip, with no toggle state and no native title.
    expect(mark.tagName).toBe('BUTTON')
    expect(mark).not.toHaveAttribute('aria-pressed')
    expect(mark).not.toHaveAttribute('title')
    expect(mark).not.toHaveAttribute('aria-describedby')
    expect(mark.getAttribute('aria-label')).toBe('This chat runs in Conductor mode')
    expect(mark.className).toContain('cursor-default')
    expect(mark).toHaveClass('h-7', 'w-7', 'mc-touch-hit')
    expect(mark.textContent).toBe('')
    expect(mark.querySelector('.lucide-zap')).not.toBeNull()
  })

  it('the composer bolt states the mode and names no action, unlike the labelled toggle', () => {
    render(<CrewModeComposerChip />)
    const label = screen.getByTestId('crew-mode-composer-chip').getAttribute('aria-label') ?? ''
    // A tip that reads like a switch ("is on", "turn off", "click") tells a person
    // the mark does something; it does nothing, so it says only what is true.
    expect(label).toBe('This chat runs in Conductor mode')
    expect(label).not.toMatch(/\b(turn|click|tap|press|switch|is on|off)\b/i)
  })

  it('the session title bolt sits out of the title line box, with a real 24px box', () => {
    render(<ConductorModeTitleMark />)
    const mark = screen.getByTestId('conductor-mode-title-mark')
    // Out of flow, at its static position: the row's title reserves `indent-6`
    // (24px) for it, so the 20px title line never grows to hold the box.
    expect(mark).toHaveClass('absolute', 'h-6', 'w-6', '-mt-0.5')
    expect(mark.className).not.toMatch(/\binline-(grid|flex|block)\b|\b-ml-/)
    expect(mark).toHaveAttribute('data-read-only-bolt')
  })

  it('the session title bolt opens its tip below, clear of the search field above the list', () => {
    render(<ConductorModeTitleMark />)
    fireEvent.focus(screen.getByTestId('conductor-mode-title-mark'))
    expect(screen.getByRole('tooltip')).toHaveAttribute('data-placement', 'below')
  })

  it('the session title bolt is a read-only anchor with no visible text and no native tooltip', () => {
    render(<span title="Full session title"><ConductorModeTitleMark /></span>)
    const mark = screen.getByTestId('conductor-mode-title-mark')
    expect(mark.tagName).toBe('BUTTON')
    expect(mark).toHaveAttribute('aria-label', 'Conductor mode')
    expect(mark).not.toHaveAttribute('aria-pressed')
    expect(mark).not.toHaveAttribute('aria-describedby')
    // Empty, so the title's own native tooltip cannot stack on the bubble.
    expect(mark).toHaveAttribute('title', '')
    expect(mark.className).toContain('cursor-default')
    expect(mark).toHaveClass('h-6', 'w-6', 'mc-touch-hit')
    expect(mark.textContent).toBe('')
    expect(mark.querySelectorAll('.lucide-zap')).toHaveLength(1)
    expect(mark.querySelector('.lucide-zap')).toHaveClass('conductor-mode-accent')
  })

  // Both read-only bolts render through one component; each case runs the same
  // gestures against one of them.
  describe.each([
    { name: 'composer bolt', el: () => <CrewModeComposerChip />, testId: 'crew-mode-composer-chip', label: 'This chat runs in Conductor mode' },
    { name: 'session title bolt', el: () => <ConductorModeTitleMark />, testId: 'conductor-mode-title-mark', label: 'Conductor mode' },
  ])('the read-only $name shows its name in a tip', ({ el, testId, label }) => {
    beforeEach(() => { vi.useFakeTimers() })
    afterEach(() => { vi.useRealTimers() })

    /** A parent standing in for a Sessions row: every gesture it could act on. */
    const parent = {
      onPointerDown: vi.fn(), onPointerUp: vi.fn(),
      onMouseDown: vi.fn(), onMouseUp: vi.fn(),
      onClick: vi.fn(), onDoubleClick: vi.fn(), onAuxClick: vi.fn(),
    }
    beforeEach(() => { Object.values(parent).forEach(fn => fn.mockClear()) })
    const renderInRow = () => {
      render(<div role="button" tabIndex={0} {...parent}>{el()}</div>)
      return screen.getByTestId(testId)
    }

    it('on hover, after the intent delay, and hides on leave', () => {
      const mark = renderInRow()
      fireEvent.pointerEnter(mark, { pointerType: 'mouse' })
      fireEvent.mouseEnter(mark)
      expect(screen.queryByRole('tooltip')).toBeNull()
      act(() => { vi.advanceTimersByTime(OPEN_DELAY_MS) })
      expect(screen.getByRole('tooltip')).toHaveTextContent(label)
      fireEvent.mouseLeave(mark)
      expect(screen.queryByRole('tooltip')).toBeNull()
    })

    it('on keyboard focus at once, and Escape hides it', () => {
      const mark = renderInRow()
      fireEvent.focus(mark)
      expect(screen.getByRole('tooltip')).toHaveTextContent(label)
      fireEvent.keyDown(window, { key: 'Escape' })
      expect(screen.queryByRole('tooltip')).toBeNull()
    })

    it('on a touch tap, since the mark has no click to lose, and the tap stops at the mark', () => {
      const mark = renderInRow()
      fireEvent.pointerEnter(mark, { pointerType: 'touch' })
      fireEvent.pointerDown(mark, { pointerType: 'touch' })
      fireEvent.pointerUp(mark, { pointerType: 'touch' })
      fireEvent.mouseEnter(mark)
      fireEvent.mouseDown(mark)
      fireEvent.focus(mark)
      fireEvent.mouseUp(mark)
      fireEvent.click(mark)
      expect(screen.getByRole('tooltip')).toHaveTextContent(label)
      expect(parent.onPointerDown).not.toHaveBeenCalled()
      expect(parent.onPointerUp).not.toHaveBeenCalled()
      expect(parent.onMouseDown).not.toHaveBeenCalled()
      expect(parent.onMouseUp).not.toHaveBeenCalled()
      expect(parent.onClick).not.toHaveBeenCalled()
    })

    it('keeps a tapped tip open through the replayed mouse leave, and closes it on blur', () => {
      const mark = renderInRow()
      fireEvent.pointerDown(mark, { pointerType: 'touch' })
      fireEvent.pointerUp(mark, { pointerType: 'touch' })
      fireEvent.mouseEnter(mark)
      fireEvent.focus(mark)
      fireEvent.click(mark)
      // The finger lifts: a touch browser replays a mouseleave in the same turn.
      fireEvent.mouseLeave(mark)
      expect(screen.getByRole('tooltip')).toHaveTextContent(label)
      fireEvent.blur(mark)
      expect(screen.queryByRole('tooltip')).toBeNull()
    })

    it('keeps pointer, click, double click, middle click and mouse events from reaching its parent', () => {
      const mark = renderInRow()
      fireEvent.pointerDown(mark)
      fireEvent.pointerUp(mark)
      fireEvent.mouseDown(mark)
      fireEvent.mouseUp(mark)
      fireEvent.click(mark)
      fireEvent.click(mark, { detail: 2 })
      fireEvent.doubleClick(mark)
      fireEvent.mouseDown(mark, { button: 1 })
      fireEvent(mark, new MouseEvent('auxclick', { bubbles: true, button: 1 }))
      for (const fn of Object.values(parent)) expect(fn).not.toHaveBeenCalled()
      // The parent itself still hears a press on its own area.
      fireEvent.click(mark.parentElement as HTMLElement)
      expect(parent.onClick).toHaveBeenCalledTimes(1)
    })
  })

  it('marks conductor mode with a lightning bolt, not the ghost', () => {
    render(<CrewModeComposerChip />)
    const chip = screen.getByTestId('crew-mode-composer-chip')
    expect(chip.querySelector('.lucide-zap')).not.toBeNull()
    expect(chip.querySelector('[data-testid="kiro-ghost-mark"]')).toBeNull()
  })
})
