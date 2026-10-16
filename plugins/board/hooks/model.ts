import type { Card, CardState, Chip, Snapshot, Step } from '../types'

export const DAY = 86_400_000
// A session promised by a close has this long to come up (trust prompt, start-up) before the
// story counts as stalled.
export const GRACE_MS = 10 * 60_000
export const QUIET_DAYS = 7
export const DONE_DAYS = 3
const MAX_LINKS = 6

export type Agent = {
  kind?: string
  status?: string
  state?: string
  name?: string
  sessionId?: string
  startedAt?: number
  cwd?: string
  waitingFor?: string | null
}
export type Stats = { ctx_pct?: number | null; cost_usd?: number | null }
export type Limit = { kind: string; percentUsed: number; resetsAt?: string }
export type Ticks = Readonly<Record<string, true>>

export type Next = { kind: 'started' | 'pending' | 'none' | 'other'; names: string[]; text: string }
export type Section = {
  name: string
  at: number
  isFinal: boolean
  next: Next
  forMe: string[]
  links: string[]
}
export type Story = { name: string; mtimeMs: number; sections: Section[] }

// The sections of a story file: "## <name> · <YYYY-MM-DD HH:MM>", as the handoff close writes them.
const HEADING = /^## (.+?) · (\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})\s*$/
const FORME = /^(?:\*\*)?FOR ME(?:\*\*)?\s*(?:[:—–-]\s*(.*))?$/
const BLOCK_END = /^(?:\*\*)?(?:DONE|OPEN|LEFT)(?:\*\*)?(?:\s*$|\s*:|\s+[—–(-])|^## |^Next:/
const ITEM = /^(?: {0,3}\d+[.)]|[-*])\s+(.*)$/
// Only a marker on its own: "Nothing now. At S3: your word" is a step.
const NOTHING = /^(?:nothing|none|n\/a|no steps)\b[^.;:]*[.;:]?\s*$/i
const NEXT = /^Next:\s*(.*)$/
const LINK = /https?:\/\/[^\s)>\]]+\/(?:merge_requests|pull)\/\d+/g

const clean = (text: string): string => text.replace(/\*\*/g, '').trim()

const parseNext = (rest: string): Next => {
  const text = rest.trim()
  const named = /^(started|spawned|pending)\s+(.+)$/i.exec(text)
  if (named !== null) {
    const names = (named[2] ?? '')
      .replace(/\([^)]*\)/g, '')
      .replace(/\s*\(.*$/, '')
      .split(/\s*,\s*/)
      .map(n => n.trim())
      .filter(n => n !== '' && n !== 'none')
    return { kind: named[1]?.toLowerCase() === 'pending' ? 'pending' : 'started', names, text }
  }
  return { kind: /^none\b/i.test(text) ? 'none' : 'other', names: [], text }
}

export const parseStory = (name: string, text: string, mtimeMs: number): Story | undefined => {
  const sections: Section[] = []
  let cur: Section | undefined
  let steps: string[] | undefined
  let isInBlock = false
  for (const line of text.split('\n')) {
    const head = HEADING.exec(line)
    if (head !== null) {
      const title = head[1] ?? ''
      cur = undefined
      steps = undefined
      isInBlock = false
      // "— findings" lists are reviews, not closes.
      if (!/ — findings$/.test(title)) {
        const [, , y, mo, d, h, mi] = head
        cur = {
          // "## s3 (re-pointed …) · stamp" is the close of s3.
          name: title.replace(/\s*\(.*\)\s*$/, ''),
          at: new Date(Number(y), Number(mo) - 1, Number(d), Number(h), Number(mi)).getTime(),
          isFinal: title === 'Final report',
          next: { kind: 'none', names: [], text: '' },
          forMe: [],
          links: [],
        }
        sections.push(cur)
        steps = cur.forMe
      }
      continue
    }
    if (line.startsWith('## ')) {
      // Any other heading (an addendum, a note) ends the section: its lines are not the close's.
      cur = undefined
      steps = undefined
      continue
    }
    if (cur === undefined || steps === undefined) continue
    for (const url of line.match(LINK) ?? []) {
      const link = url.replace(/[.,;:]+$/, '')
      if (!cur.links.includes(link)) cur.links.push(link)
    }
    const next = NEXT.exec(line)
    if (next !== null) {
      cur.next = parseNext(next[1] ?? '')
      isInBlock = false
      continue
    }
    const forMe = FORME.exec(line)
    if (forMe !== null) {
      isInBlock = true
      if (forMe[1]) steps.push(clean(forMe[1]))
    } else if (BLOCK_END.test(line)) isInBlock = false
    else if (isInBlock) {
      const item = ITEM.exec(line)?.[1]
      if (item !== undefined) steps.push(clean(item))
      else if (/^\s+\S/.test(line) && steps.length > 0) steps[steps.length - 1] += ` ${clean(line)}`
    }
  }
  if (sections.length === 0) return undefined
  for (const s of sections) s.forMe = s.forMe.filter(step => step !== '' && !NOTHING.test(step))
  return { name, mtimeMs, sections }
}

const normal = (text: string): string => text.toLowerCase().replace(/\s+/g, ' ')

// The steps of a story, newest first: the final report's own list when it is the newest section
// (the reporter merged and pruned it), else every close's steps, a repeat folded into its newest
// section. A step is one tick key: story, the section it was listed in, its text.
export const stepsOf = (story: Story): Step[] => {
  const last = story.sections[story.sections.length - 1]
  const from = last?.isFinal === true ? [last] : story.sections.filter(s => !s.isFinal)
  const seen = new Map<string, Step>()
  for (const s of [...from].reverse()) {
    for (const text of s.forMe) {
      const id = normal(text)
      if (!seen.has(id)) seen.set(id, { key: `${story.name}|${s.name}|${id}`, text, from: s.name })
    }
  }
  return [...seen.values()]
}

const linksOf = (story: Story): string[] => {
  const links: string[] = []
  for (const s of [...story.sections].reverse()) {
    for (const link of s.links) if (!links.includes(link)) links.push(link)
  }
  return links.slice(0, MAX_LINKS)
}

// A background session carries `state` (working, blocked, done, stopped, failed) and, while its
// process runs, the `status` an interactive one has: `done` with a status sits idle at its prompt.
const stateOf = (a: Agent): Chip['state'] => {
  if (a.kind === 'background' && a.status === undefined) {
    return a.state === 'blocked' ? 'needs-you' : a.state === 'working' ? 'working' : 'ended'
  }
  return a.status === 'waiting' ? 'needs-you' : a.status === 'busy' ? 'working' : 'idle'
}

const chipOf = (a: Agent, stats: ReadonlyMap<string, Stats>, reporter: string): Chip => {
  const st = stats.get(a.sessionId ?? '')
  const state = stateOf(a)
  return {
    id: a.sessionId ?? '',
    name: a.name ?? '?',
    state,
    detail: state === 'needs-you' ? (a.waitingFor ?? undefined) : state === 'ended' ? (a.state ?? 'ended') : undefined,
    ctx: st?.ctx_pct == null ? undefined : Math.round(st.ctx_pct),
    cost: st?.cost_usd == null ? undefined : st.cost_usd,
    startedAt: a.startedAt,
    repo: (a.cwd ?? '').split('/').pop() || undefined,
    isReporter: a.name === reporter,
  }
}

const list = (names: readonly string[], max = 2): string =>
  names.length <= max ? names.join(', ') : `${names.slice(0, max).join(', ')} +${names.length - max}`

const cardOf = (story: Story, chips: readonly Chip[], now: number): Card => {
  const closes = story.sections.filter(s => !s.isFinal)
  const last = story.sections[story.sections.length - 1]
  const lastClose = closes[closes.length - 1]
  const work = chips.filter(c => !c.isReporter)
  const waiting = work.filter(c => c.state === 'needs-you')
  const working = work.filter(c => c.state === 'working')
  const have = new Set(closes.map(s => s.name))
  const missing: { name: string; since: number }[] = []
  for (const s of closes) {
    for (const n of s.next.kind === 'started' ? s.next.names : []) {
      if (!have.has(n) && !missing.some(m => m.name === n)) missing.push({ name: n, since: s.at })
    }
  }
  const stalls: string[] = []
  const starting: string[] = []
  for (const m of missing) {
    const chip = work.find(c => c.name === m.name)
    if (chip?.state === 'working' || chip?.state === 'needs-you') continue
    if (now - m.since < GRACE_MS) starting.push(m.name)
    else if (chip?.state === 'idle') stalls.push(`${m.name} sits idle, no close`)
    else if (chip !== undefined) stalls.push(`${m.name} ${chip.detail ?? 'ended'}, no close`)
    else stalls.push(`${m.name}: no session, no close`)
  }
  let state: CardState
  let why: string
  if (waiting.length > 0) {
    state = 'needs-you'
    const first = waiting[0]
    why = `${first?.name ?? '?'} waits${first?.detail ? ` (${first.detail})` : ' for you'}${waiting.length > 1 ? ` +${waiting.length - 1}` : ''}`
  } else if (working.length > 0) {
    state = 'running'
    why = `${list(working.map(c => c.name))} working`
  } else if (last?.isFinal === true) {
    state = 'done'
    why = 'final report'
  } else if (stalls.length > 0) {
    state = 'stalled'
    why = `${stalls[0] ?? ''}${stalls.length > 1 ? ` (+${stalls.length - 1})` : ''}`
  } else if (starting.length > 0) {
    state = 'running'
    why = `${list(starting)} starting`
  } else if (lastClose?.next.kind === 'pending') {
    state = 'needs-you'
    why = `launch pending: ${list(lastClose.next.names)}`
  } else if (lastClose?.next.kind === 'other') {
    state = 'needs-you'
    why = `next: ${lastClose.next.text.slice(0, 60)}`
  } else {
    state = 'done'
    why = 'last close says Next: none'
  }
  return {
    story: story.name,
    state,
    why,
    closes: closes.length,
    touchedMs: story.mtimeMs,
    next: lastClose?.next.text.slice(0, 60) ?? '',
    chips: [...chips],
    steps: stepsOf(story),
    links: linksOf(story),
  }
}

const RANK: Record<CardState, number> = { 'needs-you': 0, stalled: 1, running: 2, done: 3 }

export const buildSnapshot = (
  stories: readonly Story[] | undefined,
  agents: readonly Agent[] | undefined,
  stats: ReadonlyMap<string, Stats>,
  now: number,
): Snapshot => {
  // A session named in several stories belongs to the one whose newest section names it.
  const owner = new Map<string, { story: string; at: number }>()
  const claim = (name: string, story: string, at: number): void => {
    const held = owner.get(name)
    if (held === undefined || at > held.at) owner.set(name, { story, at })
  }
  for (const s of stories ?? []) {
    claim(`${s.name}-report`, s.name, s.mtimeMs)
    for (const sec of s.sections) for (const n of [...(sec.isFinal ? [] : [sec.name]), ...sec.next.names]) claim(n, s.name, sec.at)
  }
  const byStory = new Map<string, Chip[]>()
  const loose: Chip[] = []
  for (const a of agents ?? []) {
    const story = owner.get(a.name ?? '')?.story
    const chip = chipOf(a, stats, `${story ?? ''}-report`)
    if (story === undefined) {
      if (chip.state !== 'ended') loose.push(chip)
    } else byStory.set(story, [...(byStory.get(story) ?? []), chip])
  }
  const cards = (stories ?? [])
    .map(s => cardOf(s, byStory.get(s.name) ?? [], now))
    .sort((a, b) => RANK[a.state] - RANK[b.state] || b.touchedMs - a.touchedMs)
  return { at: now, cards, loose, isAgentsKnown: agents !== undefined, isStoriesKnown: stories !== undefined }
}

export const openSteps = (card: Card, ticks: Ticks): Step[] => card.steps.filter(s => ticks[s.key] !== true)

// A card the board shows by default: anything alive or recent. Done stories leave after three days
// unless a step of theirs is still unticked; the rest fold into one line.
export const isShown = (card: Card, ticks: Ticks, now: number): boolean => {
  const age = now - card.touchedMs
  const isLive = card.chips.some(c => c.state !== 'ended' && c.state !== 'idle')
  if (card.state === 'done') return age < DONE_DAYS * DAY || (openSteps(card, ticks).length > 0 && age < QUIET_DAYS * DAY)
  return isLive || age < QUIET_DAYS * DAY
}

// Ticks of a story whose file is gone. By file name, so a file caught half-written keeps its ticks.
export const pruneTicks = (ticks: Ticks, files: readonly string[]): Record<string, true> => {
  const names = new Set(files)
  return Object.fromEntries(Object.entries(ticks).filter(([key]) => names.has(key.split('|')[0] ?? '')))
}

export const age = (ms: number): string => {
  const s = Math.max(0, Math.floor(ms / 1000))
  if (s < 3600) return `${Math.floor(s / 60)}m`
  if (s < 2 * 86_400) return `${Math.floor(s / 3600)}h`
  return `${Math.floor(s / 86_400)}d`
}

const span = (ms: number): string => {
  const m = Math.max(0, Math.floor(ms / 60_000))
  if (m < 60) return `${m}m`
  if (m < 1440) return `${Math.floor(m / 60)}h ${m % 60}m`
  return `${Math.floor(m / 1440)}d ${Math.floor((m % 1440) / 60)}h`
}

export const usd = (cost: number): string => (cost >= 100 ? `$${Math.round(cost)}` : `$${cost.toFixed(2)}`)

const cut = (text: string, width: number): string =>
  text.length <= width ? text : `${text.slice(0, Math.max(0, width - 1))}…`

const wrap = (text: string, width: number, indent: string): string[] => {
  const lines: string[] = []
  let line = ''
  for (const word of text.split(/\s+/)) {
    if (line !== '' && indent.length + line.length + 1 + word.length > width) {
      lines.push(indent + line)
      line = word
    } else line = line === '' ? word : `${line} ${word}`
  }
  if (line !== '') lines.push(indent + line)
  return lines
}

export const stateLabel = (c: Chip): string =>
  c.state === 'needs-you' ? (c.detail ? `NEEDS YOU (${c.detail})` : 'NEEDS YOU') : c.state === 'working' ? 'working' : c.state

export const sessionLines = (chips: readonly Chip[], me: string, now: number, width: number): string[] => {
  const rank = (c: Chip): number => (c.state === 'needs-you' ? 0 : c.state === 'working' ? 1 : 2)
  const live = chips
    .filter(c => c.state !== 'ended')
    .sort((a, b) => rank(a) - rank(b) || (a.startedAt ?? 0) - (b.startedAt ?? 0))
  const rows = live.map(c => ({
    mark: c.state === 'needs-you' ? '!' : c.id === me ? '*' : ' ',
    name: c.name,
    state: stateLabel(c),
    ctx: c.ctx === undefined ? '' : `${c.ctx}%`,
    cost: c.cost === undefined ? '' : usd(c.cost),
    up: c.startedAt === undefined ? '' : age(now - c.startedAt),
    repo: c.repo ?? '',
  }))
  const count = (state: Chip['state']): number => live.filter(c => c.state === state).length
  const head = `SESSIONS  ${live.length} live: ${count('needs-you')} need you, ${count('working')} working, ${count('idle')} idle   (! needs you, * this one)`
  if (rows.length === 0) return [head]
  // mark, name, state, ctx (4), cost (7), up (3) and repo, two spaces apart: 26 beside the three
  // that give way. The state keeps its room first, then the name (12 at least), then the repo (8).
  const stateW = Math.min(30, width - 46, Math.max(5, ...rows.map(r => r.state.length)))
  const nameW = Math.min(28, width - stateW - 34, Math.max(7, ...rows.map(r => r.name.length)))
  const repoW = width - nameW - stateW - 26
  const line = (mark: string, name: string, state: string, ctx: string, cost: string, up: string, repo: string) =>
    `${mark} ${cut(name, nameW).padEnd(nameW)}  ${cut(state, stateW).padEnd(stateW)}  ${ctx.padStart(4)}  ${cost.padStart(7)}  ${up.padStart(3)}  ${cut(repo, repoW)}`.trimEnd()
  return [
    head,
    line(' ', 'session', 'state', 'ctx', 'cost', 'up', 'repo'),
    ...rows.map(r => line(r.mark, r.name, r.state, r.ctx, r.cost, r.up, r.repo)),
  ]
}

export const usageLines = (limits: readonly Limit[] | undefined, now: number): string[] => {
  const label = (kind: string): string => (kind === 'five_hour' ? '5-hour' : kind === 'seven_day' ? 'week' : kind)
  const rows = (limits ?? []).map(l => {
    const at = l.resetsAt === undefined ? Number.NaN : Date.parse(l.resetsAt)
    const reset = Number.isNaN(at)
      ? ''
      : at > now
        ? `resets in ${span(at - now)}`
        : `window reset ${span(now - at)} ago, no reading since`
    return `  ${label(l.kind).padEnd(7)} ${`${l.percentUsed}%`.padStart(5)}  ${reset}`.trimEnd()
  })
  return [
    "USAGE  as of this session's last reply",
    ...(rows.length > 0 ? rows : ['  no reading yet: the windows arrive with a reply']),
    '  Fable week: not visible to a mod, /usage has it',
  ]
}

export const countsOf = (cards: readonly Card[]): Record<CardState, number> => {
  const counts: Record<CardState, number> = { 'needs-you': 0, stalled: 0, running: 0, done: 0 }
  for (const c of cards) counts[c.state] += 1
  return counts
}

const STATE_WORD: Record<CardState, string> = { 'needs-you': 'NEEDS YOU', stalled: 'STALLED', running: 'running', done: 'done' }
export const stateWord = (state: CardState): string => STATE_WORD[state]

export const storyLines = (cards: readonly Card[], ticks: Ticks, now: number, isAll: boolean): string[] => {
  const shown = cards.filter(c => isAll || isShown(c, ticks, now))
  const n = countsOf(cards)
  const head = `STORIES  ${n['needs-you']} need you, ${n.stalled} stalled, ${n.running} running, ${n.done} done`
  const nameW = Math.max(5, ...shown.map(c => c.story.length))
  const line = (name: string, state: string, closes: string, forMe: string, touched: string) =>
    `  ${name.padEnd(nameW)}  ${state.padEnd(9)}  ${closes.padStart(6)}  ${forMe.padStart(6)}  ${touched.padStart(7)}`
  const folded = cards.filter(c => !shown.includes(c))
  return [
    head,
    ...(shown.length > 0 ? [line('story', 'state', 'closes', 'FOR ME', 'touched')] : []),
    ...shown.flatMap(c => [
      line(c.story, stateWord(c.state), String(c.closes), String(openSteps(c, ticks).length), age(now - c.touchedMs)),
      ...(c.state === 'stalled' || c.state === 'needs-you' ? [`  ${' '.repeat(nameW)}  ${c.why}`] : []),
    ]),
    ...(folded.length > 0
      ? [`  ${folded.length} more (${folded.filter(c => c.state !== 'done').length} not done), quiet: /where all`]
      : []),
  ]
}

export const formeLines = (
  cards: readonly Card[],
  ticks: Ticks,
  now: number,
  width: number,
  only: string,
): string[] => {
  const picked = cards
    .filter(c => (only === '' ? now - c.touchedMs < DONE_DAYS * DAY : c.story === only))
    .sort((a, b) => Number(a.state === 'done') - Number(b.state === 'done') || b.touchedMs - a.touchedMs)
  const withSteps = picked.filter(c => c.steps.length > 0)
  const empty = picked.filter(c => c.steps.length === 0)
  return [
    only === ''
      ? `FOR ME  the steps of each story touched in the last ${DONE_DAYS} days, ticks kept`
      : `FOR ME  the steps of ${only}, ticks kept`,
    ...(picked.length > 0 ? [] : [only === '' ? '  no story was touched' : `  no story "${only}" with a close section`]),
    ...withSteps.flatMap(c => {
      const open = openSteps(c, ticks)
      return [
        '',
        `${c.story}  ${stateWord(c.state)}, touched ${age(now - c.touchedMs)} ago${open.length < c.steps.length ? `, ${c.steps.length - open.length} ticked` : ''}`,
        ...(open.length === 0 ? ['  all ticked'] : []),
        ...open.flatMap((step, i) =>
          wrap(step.text, width, '     ').map((l, k) => (k === 0 ? `${`  ${i + 1}.`.padEnd(5)}${l.slice(5)}` : l)),
        ),
      ]
    }),
    ...(empty.length > 0 ? ['', ...wrap(`No steps: ${empty.map(c => c.story).join(', ')}`, width, '')] : []),
  ]
}
