import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Plus, X } from 'lucide-react'
import { SettingsToggle } from '../../components/settings'
import { Input, Btn } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { useConfirm } from '../../components/ConfirmDialog'
import { useImeGuard } from '../../hooks/useImeGuard'
import { api, type SlackSharedUser } from '../../api/client'
import { ApiError } from '../../api/apiError'
import { i18nT } from '../../i18n/t'
import { splitOnPlaceholder } from '../../lib/splitOnPlaceholder'

/** Query key of `GET /api/slack/shared-access`. */
export const SLACK_SHARED_ACCESS_KEY = ['slack-shared-access'] as const

/** A Slack member id: U or W, then upper-case letters and digits (the server's rule). */
function isSlackUserId(v: string): boolean {
  if (v.length < 2 || v.length > 20 || (v[0] !== 'U' && v[0] !== 'W')) return false
  for (const ch of v.slice(1)) {
    if (!((ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9'))) return false
  }
  return true
}

/** The refusal codes the shared-access routes answer with that name a reason. */
const REASON_CODES = new Set([
  'user_not_found', 'bot_user', 'deactivated', 'other_workspace', 'owner', 'malformed_id',
  'unverifiable', 'not_applied',
])

/** The plain-words reason Slack did not confirm an id, from the server's code. */
function reasonText(code: string): string {
  switch (code) {
    case 'user_not_found': return i18nT('pages.settings.slackSharedAccess.reason_not_found')
    case 'bot_user': return i18nT('pages.settings.slackSharedAccess.reason_bot')
    case 'deactivated': return i18nT('pages.settings.slackSharedAccess.reason_deactivated')
    case 'other_workspace': return i18nT('pages.settings.slackSharedAccess.reason_other_workspace')
    case 'owner': return i18nT('pages.settings.slackSharedAccess.reason_owner')
    case 'malformed_id': return i18nT('pages.settings.slackSharedAccess.reason_malformed')
    case 'not_applied': return i18nT('pages.settings.slackSharedAccess.not_applied')
    default: return i18nT('pages.settings.slackSharedAccess.reason_unverifiable')
  }
}

/** The codes that mean the check itself failed (Slack unreachable), not an answer about the id. */
const CHECK_FAILURES = new Set(['unverifiable'])

/** Whether *code* says the check failed rather than refused the id; an unknown code counts as failed. */
function checkFailed(code: string): boolean {
  return CHECK_FAILURES.has(code) || !REASON_CODES.has(code)
}

/** The server's refusal code, or '' when the body carries none. */
function refusalCode(e: unknown): string {
  if (!(e instanceof ApiError)) return ''
  try {
    const body = JSON.parse(e.body) as { code?: unknown }
    return typeof body.code === 'string' ? body.code : ''
  } catch {
    return ''
  }
}

/**
 * The confirm's line for a listed person Slack did not confirm, by the same name and ID
 * their list row shows: the reason first, then that they get access later on their own
 * and how to keep them out with what the page has (cancel, remove, then turn on).
 */
function wontGetAccess(u: SlackSharedUser): string {
  const reason = reasonText(u.reason)
  return u.name
    ? i18nT('pages.settings.slackSharedAccess.wont_get_access', { name: u.name, id: u.user_id, reason })
    : i18nT('pages.settings.slackSharedAccess.wont_get_access_id', { id: u.user_id, reason })
}

/** What the owner must read before sharing, shown on the page and in the confirm. */
function SharedAccessWarning({ id }: { id?: string }) {
  return (
    <div id={id} role="note" className="flex items-start gap-2 px-3 py-2 my-2 text-[13px] rounded-md bg-warn/10 text-warn-text border border-warn/30">
      <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
      <div className="min-w-0">
        <div className="font-semibold">{i18nT('pages.settings.slackSharedAccess.warning_title')}</div>
        <ul className="list-disc pl-4 mt-1 space-y-0.5">
          <li>{i18nT('pages.settings.slackSharedAccess.warning_acts_as_you')}</li>
          <li>{i18nT('pages.settings.slackSharedAccess.warning_threads')}</li>
          <li>{i18nT('pages.settings.slackSharedAccess.warning_memory')}</li>
          {/* Named, not linked: this warning sits in the Slack settings form and its
              confirm, whose unsaved fields a navigation away would unmount and lose. */}
          <li>
            {splitOnPlaceholder(i18nT('pages.settings.slackSharedAccess.warning_tools'), 'page').map((part, i) =>
              part === null ? (
                <strong key="page">{i18nT('nav.schedule')}</strong>
              ) : (
                <span key={i}>{part}</span>
              ),
            )}
          </li>
          <li>{i18nT('pages.settings.slackSharedAccess.warning_trust')}</li>
        </ul>
      </div>
    </div>
  )
}

function SharedUserRow({ user, readOnly, sharing, onRemove, removing }: {
  user: SlackSharedUser
  readOnly: boolean
  /** Sharing is on: only then can a Slack answer later give this person access. */
  sharing: boolean
  onRemove: (id: string) => void
  removing: boolean
}) {
  const label = user.name || user.user_id
  // While off, a Slack answer gives nobody access: the row says only the reason.
  const later = sharing ? ` ${i18nT('pages.settings.slackSharedAccess.not_confirmed')}` : ''
  return (
    <li className="flex items-center gap-2 py-1 text-[13px]">
      <span className="min-w-0 flex-1">
        {user.name && <span className="font-semibold text-text">{user.name}</span>}
        <span className="ml-2 font-mono text-[12px] text-muted break-all">{user.user_id}</span>
        {!user.confirmed && checkFailed(user.reason) && (
          /* No hand-off: the member-ID draft and the Slack settings form around it
             are unsaved; a hand-off to the chat unmounts this panel and loses them. */
          <span className="block">
            <ErrorNotice impact="partial" variant="inline"
              message={`${reasonText(user.reason)}${later}`} />
          </span>
        )}
        {!user.confirmed && !checkFailed(user.reason) && (
          <span className="block text-[12px] text-warn-text">
            {reasonText(user.reason)}
            {/* The owner's own ID needs no consequence line: they already have access. */}
            {user.reason !== 'owner' && later}
          </span>
        )}
      </span>
      {!readOnly && (
        <button type="button" onClick={() => onRemove(user.user_id)} disabled={removing}
          className="shrink-0 text-muted hover:text-danger transition-colors disabled:opacity-40"
          aria-label={i18nT('pages.settings.slackPanel.remove', { name: label })}>
          <X size={14} />
        </button>
      )}
    </li>
  )
}

/**
 * Settings → Messaging Channels → Slack → Share Slack access (`slack.shared_access`).
 *
 * Off by default. The warning stays on the page whatever the switch says, and
 * turning it on goes through a confirm that names who gets access and repeats it;
 * with nobody listed it does not turn on. Changes apply at once
 * (the gateway reads them on the next Slack event); there is no separate Save.
 */
export function SlackSharedAccess() {
  const qc = useQueryClient()
  const { confirm, confirmDialog } = useConfirm()
  const ime = useImeGuard()
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  // An input hint (a malformed ID, caught before any request), shown beside the
  // field; `error` is reserved for a request that failed.
  const [hint, setHint] = useState('')
  // Why the switch did not turn on (nobody listed yet), shown under it.
  const [switchHint, setSwitchHint] = useState('')
  // Re-read on every visit and window focus: the owner may change access from
  // another window, and this section must not show a grant that has changed.
  // shortcut: no push from the server; add a refresh frame if that proves too slow.
  const { data, error: loadError } = useQuery({
    queryKey: SLACK_SHARED_ACCESS_KEY,
    queryFn: api.getSlackSharedAccess,
    refetchOnMount: 'always',
    refetchOnWindowFocus: 'always',
  })
  const refresh = () => qc.invalidateQueries({ queryKey: SLACK_SHARED_ACCESS_KEY })
  const fail = (fallback: string) => (e: unknown) => {
    const code = refusalCode(e)
    setError(REASON_CODES.has(code) ? reasonText(code) : fallback)
    // A 409 means the file was saved but the gateway kept the old list: re-read
    // what the gateway reports rather than show the change as made.
    void refresh()
  }
  const switchMut = useMutation({
    mutationFn: (enabled: boolean) => api.setSlackSharedAccess(enabled),
    onSuccess: () => { setError(''); void refresh() },
    onError: fail(i18nT('pages.settings.slackSharedAccess.save_failed')),
  })
  const addMut = useMutation({
    mutationFn: (id: string) => api.addSlackSharedUser(id),
    onSuccess: () => { setError(''); setDraft(''); setSwitchHint(''); void refresh() },
    onError: fail(i18nT('pages.settings.slackSharedAccess.save_failed')),
  })
  const removeMut = useMutation({
    mutationFn: (id: string) => api.removeSlackSharedUser(id),
    onSuccess: () => { setError(''); void refresh() },
    onError: fail(i18nT('pages.settings.slackSharedAccess.save_failed')),
  })

  // No hand-off: this notice sits inside the Slack settings form, whose unsaved
  // fields a hand-off to the chat would unmount and lose.
  if (loadError) {
    // The inline notice draws no action row, so Try again sits beside it; it re-reads the section.
    return (
      <div className="flex flex-wrap items-center gap-2">
        <ErrorNotice message={i18nT('pages.settings.slackSharedAccess.load_failed')} variant="inline" />
        <Btn onClick={() => { void refresh() }}>{i18nT('components.errorBoundary.try_again')}</Btn>
      </div>
    )
  }
  if (!data) return null
  // A gateway older than this route answers it with some other body: show nothing.
  if (typeof data.enabled !== 'boolean') return null
  const users = Array.isArray(data.users) ? data.users : []
  const ro = data.read_only !== false
  const busy = switchMut.isPending || addMut.isPending || removeMut.isPending
  // "People with access" only while sharing is on: while off, nobody listed has it.
  const peopleLabel = i18nT(data.enabled
    ? 'pages.settings.slackSharedAccess.people_label'
    : 'pages.settings.slackSharedAccess.people_label_off')

  const onToggle = async (next: boolean) => {
    setSwitchHint('')
    if (next) {
      // The confirm names who gets access: only people Slack confirmed do.
      if (users.length === 0) { setSwitchHint(i18nT('pages.settings.slackSharedAccess.people_first')); return }
      const granted = users.filter(u => u.confirmed)
      // The owner's own ID on the list already has access: it is neither granted nor refused here.
      const refused = users.filter(u => !u.confirmed && u.reason !== 'owner')
      if (granted.length === 0) { setSwitchHint(i18nT('pages.settings.slackSharedAccess.people_none_confirmed')); return }
      const ok = await confirm({
        title: i18nT('pages.settings.slackSharedAccess.confirm_title', { count: granted.length }),
        body: (
          <>
            <ul className="list-disc pl-4 text-[13px]" aria-label={i18nT('pages.settings.slackSharedAccess.people_label')}>
              {granted.map(u => <li key={u.user_id}>{u.name || u.user_id}</li>)}
            </ul>
            {refused.map(u => checkFailed(u.reason) ? (
              /* No hand-off: this sits in the confirm dialog over the unsaved Slack
                 settings form; a hand-off to the chat would unmount and lose it. */
              <div key={u.user_id} className="mt-1">
                <ErrorNotice impact="partial" variant="inline" message={wontGetAccess(u)} />
              </div>
            ) : (
              // Warn text, never muted: this person can still get access later.
              <div key={u.user_id} className="mt-1 text-[12px] text-warn-text">
                {wontGetAccess(u)}
              </div>
            ))}
            <SharedAccessWarning />
          </>
        ),
        confirmLabel: i18nT('pages.settings.slackSharedAccess.confirm_label', { count: granted.length }),
        danger: true,
      })
      if (!ok) return
    }
    switchMut.mutate(next)
  }
  const add = () => {
    const v = draft.trim()
    if (!v) return
    if (!isSlackUserId(v)) { setHint(reasonText('malformed_id')); return }
    addMut.mutate(v)
  }

  return (
    <div className="border-t border-border mt-4 pt-4" data-testid="slack-shared-access">
      <SettingsToggle
        label={i18nT('pages.settings.slackSharedAccess.toggle_label')}
        description={i18nT('pages.settings.slackSharedAccess.toggle_desc')}
        checked={data.enabled}
        onChange={v => { void onToggle(v) }}
        disabled={ro || busy}
        configKey="slack.shared_access.enabled"
        describedBy="slack-shared-access-warning"
      />
      {/* Said at the switch, so a list under the always-on warning never reads as a live grant. */}
      {!data.enabled && <div className="text-[12px] text-text">{i18nT('pages.settings.slackSharedAccess.off_note')}</div>}
      {switchHint && <div role="status" className="text-[12px] text-warn-text">{switchHint}</div>}
      <SharedAccessWarning id="slack-shared-access-warning" />
      {/* A remote session changes nothing here, so the owner-only line has nothing to say. */}
      {!ro && <div className="text-[12px] text-muted mb-2">{i18nT('pages.settings.slackSharedAccess.owner_only')}</div>}
      {/* The label changes with the switch; a `key:` highlight link finds this list by its config key. */}
      <div data-setting-label={peopleLabel} data-setting-key="slack.shared_access.user_ids" className="flex flex-col gap-1.5 py-1.5">
        <span className="text-[13px] font-semibold text-text">{peopleLabel}</span>
        {users.length === 0
          ? <div className="text-[12px] text-muted">{i18nT('pages.settings.slackSharedAccess.people_empty')}</div>
          : (
            <ul className="divide-y divide-border">
              {users.map(u => (
                <SharedUserRow key={u.user_id} user={u} readOnly={ro} sharing={data.enabled} removing={removeMut.isPending} onRemove={id => removeMut.mutate(id)} />
              ))}
            </ul>
          )}
        {!ro && (
          <div className="flex items-center gap-2">
            <Input value={draft} placeholder={i18nT('pages.settings.slackPanel.u0123abc456')} className="min-w-0 flex-1 font-mono"
              aria-label={i18nT('pages.settings.slackSharedAccess.add_label')}
              aria-describedby={hint ? 'slack-shared-access-hint' : undefined}
              onChange={e => { setDraft(e.target.value); setError(''); setHint('') }}
              {...ime.bindEnter({ onEnter: add })} />
            <Btn onClick={add} disabled={!draft.trim() || busy} className="shrink-0"><Plus size={13} /> {i18nT('pages.settings.slackPanel.add')}</Btn>
          </div>
        )}
        {hint && <div id="slack-shared-access-hint" className="text-[12px] text-danger">{hint}</div>}
        {ro && <div className="text-[12px] text-muted">{i18nT('pages.settings.slackSharedAccess.read_only')}</div>}
        {/* No hand-off: the member-ID draft and the Slack settings form around it
            are unsaved; a hand-off to the chat unmounts this panel and loses them. */}
        <ErrorNotice message={error} variant="inline" />
      </div>
      {confirmDialog}
    </div>
  )
}
