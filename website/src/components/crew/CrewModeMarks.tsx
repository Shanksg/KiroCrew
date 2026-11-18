/** The small marks every Crew Mode surface shares: the solid ghost badge,
 *  the composer bolt, a worker's state dot and a stack of tiny worker ghosts
 *  coloured by state. Colours are theme tokens (`styles/crew-mode.css`,
 *  `lib/crewWorkers.ts`), so light and dark themes follow without a branch here. */
import { motion, useReducedMotion, type HTMLMotionProps } from 'framer-motion'
import { KiroGhostMark } from '../KiroGhostMark'
import { InstantTip, useInstantTip, type TipPlacement } from '../InstantTip'
import { Zap } from 'lucide-react'
import { i18nT } from '../../i18n/t'
import { CREW_GROUP_LABEL_KEY, CREW_GROUP_TONE, type CrewWorkerGroup } from '../../lib/crewWorkers'

/** The solid ghost on the crew gradient: the breadcrumb's root. */
export function CrewGhostBadge({ size = 28 }: { size?: number }) {
  return (
    <span aria-hidden="true" data-testid="crew-ghost-badge" className="crew-grad shrink-0 rounded-full grid place-items-center" style={{ width: size, height: size }}>
      <KiroGhostMark size={Math.round(size * 0.5)} className="inline-block shrink-0 text-aim-fg" />
    </span>
  )
}

/** A worker's state as a dot and a word. The word is the state, not a hint. */
export function CrewStatusDot({ group, withLabel = true, className = '' }: { group: CrewWorkerGroup; withLabel?: boolean; className?: string }) {
  const label = i18nT(CREW_GROUP_LABEL_KEY[group])
  return (
    <span data-testid="crew-status" data-group={group} className={`inline-flex items-center gap-1 text-[12px] shrink-0 ${CREW_GROUP_TONE[group]} ${className}`}>
      <span aria-hidden="true" className={`w-2 h-2 rounded-full ${group === 'waiting' ? 'border border-current' : 'bg-current'}`} />
      {withLabel ? <span>{label}</span> : <span className="sr-only">{label}</span>}
    </span>
  )
}

/** Up to `max` tiny worker ghosts, one per worker, needs-you first. */
export function CrewWorkerStack({ groups, max = 6 }: { groups: CrewWorkerGroup[]; max?: number }) {
  const shown = groups.slice(0, max)
  const extra = groups.length - shown.length
  return (
    <span aria-hidden="true" data-testid="crew-worker-stack" className="inline-flex items-center -space-x-1">
      {shown.map((g, i) => (
        // Tinted by the worker's own state colour, so the stack reads at a glance.
        <span key={i} data-group={g} className={`w-[18px] h-[18px] rounded-full grid place-items-center border border-current ring-2 ring-bg ${CREW_GROUP_TONE[g]}`}
          style={{ backgroundColor: 'color-mix(in srgb, currentColor 22%, var(--bg-elevated))' }}>
          <KiroGhostMark size={11} className="inline-block shrink-0" />
        </span>
      ))}
      {extra > 0 && <span className="pl-2 text-[11px] text-muted">+{extra}</span>}
    </span>
  )
}

/** A press on a read-only bolt stops at the bolt. The mark can sit inside a
 *  control of its own (a Sessions row switches on click, opens a tab on
 *  middle-click, renames on a title double-click, and starts a drag on mouse
 *  down), and a tap meant to read the mark's name must not do any of that. */
const stopAtMark = (e: React.SyntheticEvent) => { e.stopPropagation() }

/** The one read-only Conductor mode bolt: an icon with no visible text that
 *  shows its accessible name in an `InstantTip` on hover, keyboard focus and
 *  touch tap, so a sighted person can learn what the bolt means. Every
 *  read-only bolt renders through this, so the composer and the Sessions row
 *  cannot drift.
 *
 *  It is a focusable button with no click action, as the resource posture chip
 *  is, so a tap has no click to lose (`openOnTap`). The bubble repeats the
 *  accessible name, so the anchor carries no `aria-describedby`.
 *  `quietAncestorTitle` sets an empty `title` for a mark inside an element
 *  that has a native tooltip of its own (the session title's full name), so
 *  that tooltip does not stack a second bubble on this one. `motionProps` is
 *  the caller's entrance animation, if it has one; `placement` is the side
 *  the bubble opens on. `data-read-only-bolt` is the stable hook a layout
 *  rule reads (the Sessions row hides the mark with the title it leads). */
function ReadOnlyBoltMark({ label, testId, size, className, motionProps, placement, quietAncestorTitle = false }: {
  label: string
  testId: string
  size: number
  className: string
  motionProps?: Pick<HTMLMotionProps<'button'>, 'initial' | 'animate' | 'transition'>
  placement?: TipPlacement
  quietAncestorTitle?: boolean
}) {
  const { tip, tipHandlers, tipId } = useInstantTip({ openOnTap: true, placement })
  const { 'aria-describedby': _bubbleId, ...handlers } = tipHandlers
  void _bubbleId
  return (
    <>
      <motion.button
        type="button"
        data-testid={testId}
        data-read-only-bolt=""
        aria-label={label}
        title={quietAncestorTitle ? '' : undefined}
        {...handlers}
        onPointerDown={e => { handlers.onPointerDown(e); stopAtMark(e) }}
        onPointerUp={e => { handlers.onPointerUp(e); stopAtMark(e) }}
        onMouseDown={stopAtMark}
        onMouseUp={stopAtMark}
        onClick={stopAtMark}
        onDoubleClick={stopAtMark}
        onAuxClick={stopAtMark}
        {...motionProps}
        className={`mc-touch-hit shrink-0 cursor-default select-none focus-visible:outline-2 focus-visible:outline-accent ${className}`}
      >
        <Zap size={size} aria-hidden="true" className="shrink-0 conductor-mode-accent" />
      </motion.button>
      <InstantTip tip={tip} tipId={tipId} className="w-max max-w-[20rem] break-words">{label}</InstantTip>
    </>
  )
}

/** One coloured bolt before a Conductor mode session title, read-only.
 *
 *  The Sessions row places it OUT of the title's line box: `absolute` with no
 *  insets keeps its static position, the start of the title line, and the
 *  title reserves the box's width with a matching `indent-6`. So the 24px
 *  touch box never makes the 20px title line taller, and the title's
 *  `overflow: hidden` cannot clip it. `-mt-0.5` centres the box on the line.
 *  The bubble opens below: above, it would cover the Sessions search field. */
export function ConductorModeTitleMark() {
  return (
    <ReadOnlyBoltMark
      label={i18nT('components.crewMode.chip_off')}
      testId="conductor-mode-title-mark"
      size={14}
      className="absolute -mt-0.5 grid h-6 w-6 place-items-center bg-transparent border-none focus-visible:-outline-offset-2"
      placement="below"
      quietAncestorTitle
    />
  )
}

/** The composer's read-only Conductor mode mark: one lightning bolt whose
 *  accessible name and tip state the chat's mode and offer no action. Before
 *  the first message the composer shows the labelled, pressed `CrewModeChip`
 *  instead, which is the control that turns the mode off. */
export function CrewModeComposerChip() {
  const reduceMotion = useReducedMotion()
  const fade = { initial: reduceMotion ? { opacity: 0 } : false, animate: { opacity: 1 }, transition: { duration: 0.2 } } as const
  return (
    <ReadOnlyBoltMark
      label={i18nT('components.crewMode.composer_chip_label')}
      testId="crew-mode-composer-chip"
      size={16}
      className="inline-grid h-7 w-7 place-items-center"
      motionProps={fade}
    />
  )
}
