import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, MessageSquare } from 'lucide-react'
import { Btn } from '../../components/ui'
import ErrorNotice from '../../components/ErrorNotice'
import { api, type SlackConfigData, type SlackOwnerClaimState } from '../../api/client'
import { ApiError } from '../../api/apiError'
import { i18nT } from '../../i18n/t'
import { fmtNumber, fmtTime } from '../../i18n/format'

const KEY = ['slack-owner-claim']

/** The route's own `code` (`owner_set`, `org_install_refused`, a Slack error), else "". */
function refusalCode(e: unknown): string {
  if (!(e instanceof ApiError)) return ''
  try {
    const body: unknown = JSON.parse(e.body)
    const code = body && typeof body === 'object' ? (body as { code?: unknown }).code : undefined
    return typeof code === 'string' ? code : ''
  } catch {
    return ''
  }
}

function refusal(e: unknown): string {
  const code = refusalCode(e)
  if (code === 'owner_set') return i18nT('pages.settings.slackOwnerClaim.error_owner_set')
  return i18nT('pages.settings.slackOwnerClaim.error_failed', { reason: code || 'unreachable' })
}

/**
 * "Claim in Slack": the owner sends the bot a DM and confirms it here. Only on
 * the direct-local page; a read-only remote viewer sees none of it (the routes
 * refuse it anyway).
 */
export function SlackOwnerClaim({ config, onOwnerChange }: {
  config: SlackConfigData
  /** The owner ID a Confirm wrote or a Clear removed, so the panel's own owner
   *  draft follows it and a later Save does not send the old value back. */
  onOwnerChange: (ownerId: string) => void
}) {
  const qc = useQueryClient()
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const enabled = !config.read_only
  const q = useQuery({
    queryKey: KEY,
    queryFn: api.getSlackOwnerClaim,
    enabled,
    // Poll only while a window is open, so a new DM shows up without a reload.
    refetchInterval: (query) => ((query.state.data as SlackOwnerClaimState | undefined)?.open ? 2000 : false),
  })
  const done = (next?: SlackOwnerClaimState) => {
    if (next) qc.setQueryData(KEY, next)
    void qc.invalidateQueries({ queryKey: KEY })
  }
  const start = useMutation({
    mutationFn: api.startSlackOwnerClaim,
    onMutate: () => { setError(''); setNotice('') },
    onSuccess: done,
    onError: (e: unknown) => setError(refusal(e)),
  })
  const cancel = useMutation({
    mutationFn: api.cancelSlackOwnerClaim,
    onMutate: () => setError(''),
    onSuccess: done,
    onError: (e: unknown) => setError(refusal(e)),
  })
  const confirm = useMutation({
    mutationFn: (userId: string) => api.confirmSlackOwnerClaim(userId),
    onMutate: () => setError(''),
    onSuccess: (res) => {
      setNotice(i18nT('pages.settings.slackOwnerClaim.confirmed'))
      onOwnerChange(res.owner_id)
      done()
      void qc.invalidateQueries({ queryKey: ['slack-config'] })
    },
    onError: (e: unknown) => { setError(refusal(e)); done() },
  })
  const clear = useMutation({
    mutationFn: () => api.saveSlackConfig({ owner_id: '' }),
    onSuccess: () => {
      setNotice(i18nT('pages.settings.slackOwnerClaim.cleared_restart'))
      onOwnerChange('')
      void qc.invalidateQueries({ queryKey: ['slack-config'] })
    },
    onError: (e: unknown) => setError(refusal(e)),
  })

  if (!enabled) return null
  const state = q.data
  // A failed poll must not leave old candidates reading as current.
  const shownError = error || (q.isError ? refusal(q.error) : '')

  if (config.owner_id) {
    const mine = state?.confirmed && state.confirmed.user_id === config.owner_id ? state.confirmed : null
    return (
      <div data-testid="slack-owner-claim" className="mb-3">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-[13px] text-text">
            {mine
              ? i18nT('pages.settings.slackOwnerClaim.owner_handle', { handle: mine.handle, workspace: mine.team_name })
              : i18nT('pages.settings.slackOwnerClaim.owner_id', { id: config.owner_id })}
          </span>
          <Btn data-testid="slack-owner-clear" onClick={() => clear.mutate()} disabled={clear.isPending}>
            {i18nT('pages.settings.slackOwnerClaim.clear')}
          </Btn>
        </div>
        {notice && <p className="text-[12px] text-warn mt-1">{notice}</p>}
        {/* No hand-off: the Slack panel around this block holds unsaved token
            fields and settings drafts, and navigating to the chat would discard them. */}
        <ErrorNotice message={shownError} className="mt-2" />
      </div>
    )
  }

  const tokens = config.bot_token_set && config.app_token_set
  return (
    <div data-testid="slack-owner-claim" className="mb-3 rounded-md border border-border bg-bg-elevated px-3 py-2">
      <p className="text-[13px] font-medium text-text-strong m-0">{i18nT('pages.settings.slackOwnerClaim.title')}</p>
      <p className="text-[12px] text-muted mt-1 mb-2">{i18nT('pages.settings.slackOwnerClaim.intro')}</p>
      {!state?.open && (
        <Btn data-testid="slack-owner-claim-start" onClick={() => start.mutate()} disabled={!tokens || start.isPending}>
          <MessageSquare size={13} />
          {start.isPending ? i18nT('pages.settings.slackOwnerClaim.starting') : i18nT('pages.settings.slackOwnerClaim.start')}
        </Btn>
      )}
      {!tokens && <p className="text-[12px] text-muted mt-1">{i18nT('pages.settings.slackOwnerClaim.needs_tokens')}</p>}
      {state?.open && (
        <div>
          <p className="text-[12px] text-text m-0">
            {i18nT('pages.settings.slackOwnerClaim.open_hint', { time: fmtTime(state.expires_at * 1000) })}
          </p>
          {state.multiple && (
            <p data-testid="slack-owner-claim-multiple" className="text-[12px] text-warn mt-1 flex items-center gap-1.5">
              <AlertTriangle size={12} className="flex-none" />
              {i18nT('pages.settings.slackOwnerClaim.multiple_warning')}
            </p>
          )}
          {state.candidates.length === 0 && (
            <p className="text-[12px] text-muted mt-1">{i18nT('pages.settings.slackOwnerClaim.waiting')}</p>
          )}
          <ul className="list-none p-0 m-0 mt-2 flex flex-col gap-2">
            {state.candidates.map(c => (
              <li key={c.user_id} data-testid="slack-owner-candidate" className="flex items-start gap-2 rounded-md border border-border px-2 py-1.5">
                {c.avatar && <img src={c.avatar} alt="" referrerPolicy="no-referrer" className="w-8 h-8 rounded flex-none" />}
                <div className="flex-1 min-w-0">
                  <div className="text-[13px] text-text-strong">
                    <span translate="no">{c.display_name}</span>{' '}
                    <span className="text-muted" translate="no">@{c.handle}</span>
                  </div>
                  <div className="text-[11px] text-muted" translate="no">{c.team_name} · {c.user_id}</div>
                  <blockquote className="text-[12px] text-text m-0 mt-1 break-words" translate="no">{c.text}</blockquote>
                  <div className="text-[11px] text-muted">
                    {i18nT('pages.settings.slackOwnerClaim.received', { time: fmtTime(c.received_at * 1000) })}
                  </div>
                </div>
                <Btn data-testid="slack-owner-confirm" onClick={() => confirm.mutate(c.user_id)} disabled={confirm.isPending}>
                  <Check size={13} />
                  {i18nT('pages.settings.slackOwnerClaim.confirm')}
                </Btn>
              </li>
            ))}
          </ul>
          {state.ignored > 0 && (
            <p className="text-[11px] text-muted mt-1">
              {i18nT('pages.settings.slackOwnerClaim.ignored', { total: fmtNumber(state.ignored) })}
            </p>
          )}
          <Btn data-testid="slack-owner-claim-cancel" className="mt-2" onClick={() => cancel.mutate()} disabled={cancel.isPending}>
            {i18nT('pages.settings.slackOwnerClaim.cancel')}
          </Btn>
        </div>
      )}
      {notice && <p className="text-[12px] text-ok mt-1">{notice}</p>}
      {/* No hand-off: the same unsaved token fields and settings drafts as above. */}
      <ErrorNotice message={shownError} className="mt-2" />
    </div>
  )
}
