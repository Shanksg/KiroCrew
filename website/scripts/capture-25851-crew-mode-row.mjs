/**
 * Screenshot harness for the simplified Conductor mode marks.
 *
 * Runs the REAL built SPA (website/dist) behind the shared in-process static
 * server and answers every /api/** call from fixtures via Playwright route
 * interception -- gateway-free, no kiro-cli, no dashboard auth. The root row
 * carries the `crew_mode_root` stamp the gateway writes, so the row and composer
 * render from the same state they read live.
 *
 * Frames (1280x800, 2x), one per theme:
 *   <prefix>-<theme>.png        the Conductor mode Sessions row
 *   <prefix>-<theme>-full.png   the full chat and composer
 *   <prefix>-dark-tip.png          the focused composer bolt and its status
 *   <prefix>-dark-row-tip.png      the focused Sessions bolt, its name below it
 *   <prefix>-<theme>-new.png        a new conductor chat before its first
 *                                  message: the pressed, labelled turn-off chip
 *   <prefix>-<theme>-new-hover.png  the same chip hovered
 *   <prefix>-dark-mobile.png       the narrow chat composer
 *   <prefix>-dark-mobile-tip.png   the tapped composer bolt and its status
 *   <prefix>-dark-drawer.png       the narrow Sessions drawer
 *   <prefix>-dark-drawer-tip.png   the tapped Sessions bolt and its name
 *   <prefix>-dark-drawer-tip-bubble.png  the mobile tooltip at native size
 *
 * Usage (in website/): node scripts/capture-25851-crew-mode-row.mjs <outDir> <prefix>
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'
import { resolve } from 'node:path'
import { serveDist } from './lib/serve-dist.mjs'
import { logPageProblems, stubDashboardApi, json } from './lib/stub-dashboard-api.mjs'

const OUT = resolve(process.argv[2] || '../temp-screenshots/25851')
const PREFIX = process.argv[3] || 'after'
mkdirSync(OUT, { recursive: true })

const ROOT = 'chat-root'
const NEW = 'chat-new'
const STATUS = 'This chat runs in Conductor mode'
const T0 = '2026-10-28T08:00:00Z'
const row = (key, title, extra = {}) => ({
  key, title, messages: 2, running: false, agent: 'kirocrew', agent_kind: 'template', mode: '', origin: 'user',
  memory_mode: 'persistent', created: T0, last_ts: T0, folder_id: '', workspace: 'default', ...extra,
})
const slots = [
  row(ROOT, 'Write three haiku about the sea, trees and rain', { agent: 'kirocrew-conductor', crew_mode_root: true, last_ts: '2026-10-28T08:12:00Z' }),
  row('chat-w1', 'Rain haiku', { agent: 'kirocrew-worker', crew_root: ROOT, created_by: ROOT, last_ts: '2026-10-28T08:11:00Z' }),
  row('chat-w2', 'Tree haiku', { agent: 'kirocrew-worker', crew_root: ROOT, created_by: ROOT, last_ts: '2026-10-28T08:09:00Z' }),
  row('chat-plain', 'Plan the release notes', { last_ts: '2026-10-28T07:50:00Z' }),
  // A conductor chat with no message yet: the composer offers the way back off.
  row(NEW, '', { agent: 'kirocrew-conductor', messages: 0, last_ts: '2026-10-28T07:40:00Z' }),
]
const CATALOG = {
  agents: ['kirocrew', 'kirocrew-conductor', 'kirocrew-worker'].map(name => ({ name, selection_kind: 'template', kiro_agent: '', workspace: 'default', memory_store: 'default', source: 'builtin' })),
  default_agent: 'kirocrew', crew_mode_agent: 'kirocrew-conductor', conductor_agents: ['kirocrew-conductor'],
}
const WORKERS = {
  root: ROOT,
  workers: [
    { key: 'chat-w1', title: 'Rain haiku', agent: 'kirocrew-worker', status: 'idle', group: 'done', last_activity: '2026-10-28T08:11:00Z', created_by: ROOT, dispatched_by: ROOT },
    { key: 'chat-w2', title: 'Tree haiku', agent: 'kirocrew-worker', status: 'idle', group: 'done', last_activity: '2026-10-28T08:09:00Z', created_by: ROOT, dispatched_by: ROOT },
  ],
}

const extra = async (path, route) => {
  if (path === '/api/agents/catalog') { await json(route, CATALOG); return true }
  if (path === `/api/chat/slots/${ROOT}/crew-workers`) { await json(route, WORKERS); return true }
  if (path === `/api/chat/slots/${ROOT}/crew-timeline`) { await json(route, { root: ROOT, lanes: [], gaps: [] }); return true }
  if (path === `/api/chat/slots/${ROOT}`) {
    await json(route, {
      messages: [
        { role: 'user', content: 'Write three haiku about the sea, trees and rain.', ts: '2026-10-28T08:01:00Z' },
        { role: 'assistant', content: 'The crew finished all three haiku.', ts: '2026-10-28T08:12:00Z' },
      ],
      has_more: false,
      total: 2,
    })
    return true
  }
  if (path.startsWith('/api/chat/slots/') && !path.slice('/api/chat/slots/'.length).includes('/')) {
    await json(route, { messages: [], has_more: false, total: 0 }); return true
  }
  if (path === '/api/sessions') { await json(route, { sessions: [], has_more: false }); return true }
  if (path === '/api/chat/pins') { await json(route, { pins: [] }); return true }
  return false
}

let failures = 0
const assert = (ok, label) => { console.log(`ASSERT ${ok ? 'ok  ' : 'FAIL'}: ${label}`); if (!ok) failures++ }

async function main() {
  const { srv, base } = await serveDist()
  const { LD_LIBRARY_PATH: _mise, ...env } = process.env
  const browser = await chromium.launch({ env })
  for (const theme of ['dark', 'light']) {
    const context = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2 })
    const page = await context.newPage()
    logPageProblems(page)
    await stubDashboardApi(page, {
      slots,
      theme,
      localStorageEntries: {
        'mc-active-slot-chat': ROOT,
        'mc-lang': 'en',
        'kc-locked-controls': '[]',
      },
      extra,
    })
    await page.goto(`${base}/chat?sid=${ROOT}`, { waitUntil: 'domcontentloaded' })
    const card = page.locator('[data-crew-root="true"]').first()
    await card.waitFor({ timeout: 20_000 })
    await page.waitForTimeout(1000)
    // The fixture rows are older than the sleeping-session cutoff: show them, so
    // a plain row sits beside the conductor row and the new chat can be opened.
    await page.locator('[data-testid^="stale-expander-"]').first().click()
    await page.locator('[data-session-row$="chat-plain"]').waitFor({ timeout: 5_000 })
    // Off the expander, so its own hint does not cover the rows in the frames.
    await page.mouse.move(640, 790)
    await page.evaluate(() => { document.activeElement?.blur() })
    await page.getByRole('tooltip').waitFor({ state: 'detached', timeout: 5_000 }).catch(() => {})
    const mark = card.getByTestId('conductor-mode-title-mark')
    const composerMark = page.getByTestId('crew-mode-composer-chip')
    const badge = await card.getByTestId('crew-ghost-badge').count()
    const modeText = await card.getByText('Conductor mode', { exact: true }).count()
    const titleWidth = await card.locator('[data-session-title]').first().evaluate(el => el.getBoundingClientRect().width)
    console.log(`${PREFIX}/${theme}: badge=${badge} mode-text=${modeText} title-width=${titleWidth.toFixed(1)}px`)
    if (PREFIX === 'after') {
      assert(badge === 0, `${theme}: no ghost badge before the title`)
      assert(modeText === 0, `${theme}: no repeated Conductor mode text`)
      assert((await mark.count()) === 1, `${theme}: one bolt before the title`)
      // The layout the row promises: the bolt's 24px box sits outside the
      // title's 20px line box, so a conductor row is a plain row's height.
      const geo = await card.evaluate(el => {
        const title = el.querySelector('[data-session-title]')
        const bolt = el.querySelector('[data-testid="conductor-mode-title-mark"]')
        const text = [...title.childNodes].find(n => n.nodeType === 3 && n.textContent.trim())
        const range = document.createRange(); range.selectNodeContents(text)
        const t = title.getBoundingClientRect(), b = bolt.getBoundingClientRect()
        const plain = document.querySelector('[data-session-row$="chat-plain"]')
        return {
          inTitle: title.contains(bolt), titleH: t.height, rowH: el.getBoundingClientRect().height,
          plainH: plain.getBoundingClientRect().height, plainTitleH: plain.querySelector('[data-session-title]').getBoundingClientRect().height,
          // The fixture's plain row has no status line; the root has its summary.
          // So the two rows differ by exactly that line, and by nothing the bolt adds.
          summaryH: el.querySelector('[data-testid="crew-root-summary"]')?.getBoundingClientRect().height ?? 0,
          boltW: b.width, boltH: b.height, boltMid: (b.top + b.bottom) / 2, lineMid: t.top + 10,
          boltRight: b.right, textLeft: range.getClientRects()[0].left,
          hit: document.elementFromPoint(b.left + 1, b.top + 1) === bolt && document.elementFromPoint(b.right - 1, b.bottom - 1) === bolt,
        }
      })
      console.log(`${PREFIX}/${theme}: geometry ${JSON.stringify(geo)}`)
      assert(!geo.inTitle, `${theme}: bolt is outside the title's line box`)
      assert(geo.titleH === geo.plainTitleH && geo.titleH === 20, `${theme}: conductor title line is the plain 20px line`)
      assert(geo.summaryH > 0 && geo.rowH === geo.plainH + geo.summaryH, `${theme}: conductor row is a plain row plus its status line, nothing more`)
      assert(geo.boltW >= 24 && geo.boltH >= 24, `${theme}: Sessions bolt has at least a 24px touch target`)
      assert(geo.hit, `${theme}: the whole 24px box takes the hit (no clipped corner)`)
      assert(Math.abs(geo.boltMid - geo.lineMid) <= 1, `${theme}: bolt is centred on the title line`)
      assert(geo.textLeft >= geo.boltRight, `${theme}: title text starts after the bolt's box`)
      assert((await composerMark.textContent()) === '', `${theme}: composer mark is only a bolt`)
    }
    // The row and the rows around it: the whole session-list width, a little above and below.
    const box = await card.boundingBox()
    const x = Math.max(0, box.x - 12)
    const y = Math.max(0, box.y - 40)
    await page.screenshot({ path: `${OUT}/${PREFIX}-${theme}.png`, clip: { x, y, width: box.width + 24, height: box.height + 140 } })
    await page.screenshot({ path: `${OUT}/${PREFIX}-${theme}-full.png` })
    if (theme === 'dark') {
      await mark.focus()
      const rowTip = page.getByRole('tooltip')
      await rowTip.waitFor({ timeout: 5_000 })
      assert((await rowTip.textContent()) === 'Conductor mode', 'dark: focused Sessions bolt names the mode')
      const [rowTipBox, markBox] = [await rowTip.boundingBox(), await mark.boundingBox()]
      assert(rowTipBox.y >= markBox.y + markBox.height, 'dark: Sessions tip opens below the bolt, clear of search')
      await page.screenshot({ path: `${OUT}/${PREFIX}-dark-row-tip.png` })
      await composerMark.focus()
      const tip = page.getByRole('tooltip')
      await tip.waitFor({ timeout: 5_000 })
      assert((await tip.textContent()) === STATUS, 'dark: focused composer bolt states the mode as a status')
      await page.screenshot({ path: `${OUT}/${PREFIX}-dark-tip.png` })
    }
    // A new conductor chat, before the first message: the turn-off control is
    // the labelled, pressed chip, not the read-only bolt.
    await page.evaluate(() => { document.activeElement?.blur() })
    await page.locator(`[data-session-row$="${NEW}"]`).click()
    const pressed = page.locator('[data-testid="crew-mode-chip"][aria-pressed="true"]')
    await pressed.waitFor({ timeout: 10_000 })
    assert((await page.getByTestId('crew-mode-composer-chip').count()) === 0, `${theme}: no read-only bolt before the first message`)
    assert((await pressed.textContent()).includes('Conductor mode'), `${theme}: pressed turn-off chip keeps its name`)
    const dock = await pressed.evaluate(el => {
      const r = el.closest('[data-testid="composer-dock-root"]').getBoundingClientRect()
      return { x: r.x, y: r.y, width: r.width, height: r.height }
    })
    const near = { x: Math.max(0, dock.x - 8), y: Math.max(0, dock.y - 8), width: dock.width + 16, height: dock.height + 16 }
    await page.mouse.move(0, 0)
    await page.screenshot({ path: `${OUT}/${PREFIX}-${theme}-new.png`, clip: near })
    await pressed.hover()
    // Settled hover: the pointer is on the chip and its transitions have ended.
    await pressed.evaluate(el => el.matches(':hover') && Promise.all(el.getAnimations({ subtree: true }).map(a => a.finished)))
    assert(await pressed.evaluate(el => el.matches(':hover')), `${theme}: pressed chip is hovered`)
    await page.screenshot({ path: `${OUT}/${PREFIX}-${theme}-new-hover.png`, clip: near })
    await context.close()

    if (theme === 'dark') {
      const mobileContext = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, hasTouch: true })
      const mobile = await mobileContext.newPage()
      logPageProblems(mobile)
      await stubDashboardApi(mobile, {
        slots,
        theme,
        localStorageEntries: {
          'mc-active-slot-chat': ROOT,
          'mc-lang': 'en',
          'kc-locked-controls': '[]',
        },
        extra,
      })
      await mobile.goto(`${base}/chat?sid=${ROOT}`, { waitUntil: 'domcontentloaded' })
      await mobile.getByTestId('crew-mode-composer-chip').waitFor({ timeout: 20_000 })
      await mobile.waitForTimeout(800)
      await mobile.screenshot({ path: `${OUT}/${PREFIX}-dark-mobile.png` })
      await mobile.getByTestId('crew-mode-composer-chip').tap()
      const composerTip = mobile.getByRole('tooltip')
      await composerTip.waitFor({ timeout: 5_000 })
      assert((await composerTip.textContent()) === STATUS, 'dark mobile: tapped composer bolt states the mode')
      await mobile.screenshot({ path: `${OUT}/${PREFIX}-dark-mobile-tip.png` })
      await mobile.evaluate(() => { document.activeElement?.blur() })
      await mobile.getByTestId('mobile-topbar-sessions-toggle').click()
      await mobile.locator('[data-crew-root="true"]').first().waitFor({ timeout: 10_000 })
      await mobile.screenshot({ path: `${OUT}/${PREFIX}-dark-drawer.png` })
      const mobileMark = mobile.getByTestId('conductor-mode-title-mark')
      await mobileMark.tap()
      const mobileTip = mobile.getByRole('tooltip')
      await mobileTip.waitFor({ timeout: 5_000 })
      assert((await mobileTip.textContent()) === 'Conductor mode', 'dark mobile: tapped Sessions bolt names the mode')
      const mobileTipBox = await mobileTip.boundingBox()
      assert(mobileTipBox !== null && mobileTipBox.width > 0 && mobileTipBox.height > 0, 'dark mobile: tapped Sessions tooltip has a painted box')
      assert((await mobile.locator('[data-crew-root="true"]').count()) > 0, 'dark mobile: tapping the Sessions bolt keeps the drawer open')
      await mobileTip.screenshot({ path: `${OUT}/${PREFIX}-dark-drawer-tip-bubble.png` })
      await mobile.screenshot({ path: `${OUT}/${PREFIX}-dark-drawer-tip.png` })
      await mobileContext.close()
    }
  }
  await browser.close()
  srv.close()
  console.log(failures ? `FAILED: ${failures} assertion(s)` : 'all assertions passed')
  process.exit(failures ? 1 : 0)
}

main().catch(e => { console.error(e); process.exit(2) })
