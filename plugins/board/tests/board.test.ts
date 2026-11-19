import type { CommandSpec, On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'

import status from './fixtures/capacity-status'
import {
  buildSnapshot,
  capacityLines,
  capacityParts,
  formeLines,
  isShown,
  openSteps,
  parkedOf,
  parseStory,
  parseTests,
  pruneTicks,
  sessionLines,
  stepsOf,
  storyLines,
  testLines,
  usageLines,
  watchLines,
  watchesOf,
} from '../hooks/model'
import type { Agent, Story } from '../hooks/model'
import type { CapacityStatus } from '../types'

const HOUR = 3_600_000
const DAY = 24 * HOUR
const MIN = 60_000
// Local time, as the closes type their stamps.
const NOW = new Date(2026, 9, 16, 14, 0).getTime()
const stamp = (msAgo: number): string => {
  const d = new Date(NOW - msAgo)
  const p = (n: number): string => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

const close = (name: string, msAgo: number, next: string, body = ''): string =>
  `## ${name} · ${stamp(msAgo)}\nSession: ${name}-id · Repo: /w/app\nNext: ${next}\n\nDONE\n- a slice\n\n${body}\nOPEN\n- Nothing blocked.\n\n`

const CHAIN = `# chain — closes\n\n${close('c-s1', 3 * HOUR, 'started c-s2', 'FOR ME\n1. Merge the branch when you choose.\n2. Tap through the new screen on the device,\n   then say which of the two layouts stays.\n   - the first layout is the old one\n\nMR: https://gitlab.com/g/p/-/merge_requests/93.\n')}${close('c-s2', HOUR, 'started c-s3', 'FOR ME\n1. Merge the branch when you choose.\n2. Run the migration.\n')}`

const stories = (): Story[] => {
  const parse = (name: string, text: string, ago: number): Story | undefined => parseStory(name, text, NOW - ago)
  return [
    parse('chain', CHAIN, 20 * MIN),
    parse('stuck', close('k-s1', HOUR, 'started k-s2', 'FOR ME\n- Say go.\n'), HOUR),
    parse('fresh', close('f-s1', 2 * MIN, 'started f-s2'), 2 * MIN),
    parse('idle', close('i-s1', HOUR, 'started i-s2'), HOUR),
    parse('needy', close('n-s1', 30 * MIN, 'started n-s2'), 30 * MIN),
    parse('pend', close('p-s1', 5 * HOUR, 'pending p-s2 (saved prompt)', 'FOR ME\n1. Launch: claude -r p-s2\n'), 5 * HOUR),
    parse('fin', close('x-s1', 6 * HOUR, 'none', 'FOR ME\n- Push main.\n'), 6 * HOUR),
    parse('gone', close('e-s1', 2 * HOUR, 'started e-s2'), 2 * HOUR),
    parse(
      'reported',
      `${close('r-s1', 9 * HOUR, 'none', 'FOR ME\n1. An old step.\n')}## Final report · ${stamp(8 * HOUR)}\nDONE\n- all of it\n\n**FOR ME**\n- Push main.\n\n**OPEN**\n- nothing\n`,
      8 * HOUR,
    ),
    parse('old-stuck', close('o-s1', 30 * DAY, 'started o-s2'), 30 * DAY),
    parseStory('draft', 'a prompt draft with FOR ME in it, no close section\n', NOW),
  ].filter((s): s is Story => s !== undefined)
}

const AGENTS: Agent[] = [
  { kind: 'interactive', status: 'idle', name: 'c-s1', sessionId: 's1', startedAt: NOW - 3 * HOUR, cwd: '/w/app' },
  { kind: 'interactive', status: 'busy', name: 'c-s3', sessionId: 's3', startedAt: NOW - 20 * MIN, cwd: '/w/app' },
  { kind: 'interactive', status: 'idle', name: 'i-s2', sessionId: 's4', startedAt: NOW - HOUR, cwd: '/w/app' },
  {
    kind: 'interactive',
    status: 'waiting',
    name: 'n-s2',
    sessionId: 's5',
    startedAt: NOW - 20 * MIN,
    cwd: '/w/a-repository-with-a-long-name',
    waitingFor: 'permission prompt',
  },
  { kind: 'background', state: 'failed', name: 'e-s2', sessionId: 's6', startedAt: NOW - 2 * HOUR },
  { kind: 'background', state: 'done', name: 'not-in-a-story', sessionId: 's7' },
  { kind: 'interactive', status: 'waiting', name: 'brain-55', sessionId: 's8', startedAt: NOW - HOUR, cwd: '/w/brain', waitingFor: 'input needed' },
  { kind: 'interactive', status: 'idle', name: 'loose-idle', sessionId: 's9', startedAt: NOW - HOUR, cwd: '/w/app' },
]

const snap = (agents: Agent[] = AGENTS) =>
  buildSnapshot(stories(), agents, new Map([['s5', { ctx_pct: 61, cost_usd: 141.2 }]]), NOW)
const card = (name: string) => snap().cards.find(c => c.story === name)

test('a story counts its closes, its Next, its steps and its MR links, and skips what is not a close', () => {
  const story = parseStory('chain', CHAIN, NOW)
  expect(story?.sections.map(s => [s.name, s.next.kind, s.next.names])).toEqual([
    ['c-s1', 'started', ['c-s2']],
    ['c-s2', 'started', ['c-s3']],
  ])
  expect(story?.sections[0]?.forMe).toEqual([
    'Merge the branch when you choose.',
    'Tap through the new screen on the device, then say which of the two layouts stays. - the first layout is the old one',
  ])
  expect(story?.sections[0]?.links).toEqual(['https://gitlab.com/g/p/-/merge_requests/93'])
  expect(parseStory('x', '## x · 2026-10-16 10:00\nNext: spawned a, b (both)\n', 0)?.sections[0]?.next).toMatchObject({
    kind: 'started',
    names: ['a', 'b'],
  })
  expect(parseStory('x', '## x · 2026-10-16 10:00\nNext: none (the merge waits)\nFOR ME — none.\n', 0)?.sections[0]).toMatchObject({
    next: { kind: 'none' },
    forMe: [],
  })
  expect(parseStory('x', '# a plan\n\n## Scope\nFOR ME\n1. x\n\n## review · 2026-10-16 10:00 — findings\n', 0)).toBeUndefined()
  expect(parseStory('x', '## Final report · 2026-10-16 10:00\nDONE\n- x\n', 0)?.sections[0]?.isFinal).toBe(true)
})

test('the steps of a story merge the closes and fold a repeat, and the final report replaces them', () => {
  const chain = parseStory('chain', CHAIN, NOW)
  const steps = stepsOf(chain as Story)
  expect(steps.map(s => [s.from, s.text.slice(0, 20)])).toEqual([
    ['c-s2', 'Merge the branch whe'],
    ['c-s2', 'Run the migration.'],
    ['c-s1', 'Tap through the new '],
  ])
  expect(stepsOf(stories().find(s => s.name === 'reported') as Story).map(s => s.text)).toEqual(['Push main.'])
})

test('each card names the state a person acts on: needs you, stalled, running, done', () => {
  const states = Object.fromEntries(snap().cards.map(c => [c.story, c.state]))
  expect(states).toEqual({
    needy: 'needs-you',
    pend: 'needs-you',
    stuck: 'stalled',
    idle: 'stalled',
    gone: 'stalled',
    'old-stuck': 'stalled',
    chain: 'running',
    fresh: 'running',
    fin: 'done',
    reported: 'done',
  })
  expect(card('needy')?.why).toBe('n-s2 waits (permission prompt)')
  expect(card('pend')?.why).toBe('launch pending: p-s2')
  expect(card('stuck')?.why).toBe('k-s2: no session, no close')
  expect(card('idle')?.why).toBe('i-s2 sits idle, no close')
  expect(card('gone')?.why).toBe('e-s2 failed, no close')
  expect(card('chain')?.why).toBe('c-s3 working')
  expect(card('fresh')?.why).toBe('f-s2 starting')
  expect(card('fin')?.why).toBe('last close says Next: none')
  expect(card('reported')?.why).toBe('final report')
  expect(card('draft')).toBeUndefined()
})

test('cards sort by what needs a person first, and a session outside every story stays loose', () => {
  const s = snap()
  expect(s.cards.map(c => c.state)).toEqual([
    'needs-you',
    'needs-you',
    'stalled',
    'stalled',
    'stalled',
    'stalled',
    'running',
    'running',
    'done',
    'done',
  ])
  expect(s.loose.map(c => c.name)).toEqual(['brain-55', 'loose-idle'])
  expect(card('needy')?.chips[0]).toMatchObject({ name: 'n-s2', state: 'needs-you', detail: 'permission prompt', ctx: 61, cost: 141.2 })
  expect(card('gone')?.chips[0]).toMatchObject({ name: 'e-s2', state: 'ended', detail: 'failed' })
  expect(card('chain')?.links).toEqual(['https://gitlab.com/g/p/-/merge_requests/93'])
})

test('a background session reads its status while its process runs, else its state', () => {
  const bg = (state: string, status?: string, waitingFor?: string): Agent => ({ kind: 'background', state, status, waitingFor, name: 'k-s2', sessionId: 'k' })
  const stuck = (a: Agent) => snap([a]).cards.find(c => c.story === 'stuck')
  expect(stuck(bg('working'))?.state).toBe('running')
  expect(stuck(bg('working', 'busy'))?.state).toBe('running')
  expect(stuck(bg('blocked', 'waiting', 'permission prompt'))?.why).toBe('k-s2 waits (permission prompt)')
  expect(stuck(bg('done', 'idle'))?.why).toBe('k-s2 sits idle, no close')
  expect(stuck(bg('stopped'))?.why).toBe('k-s2 stopped, no close')
})

test('a story whose promised session is working is running, not stalled, even long after its close', () => {
  const agents: Agent[] = [{ kind: 'interactive', status: 'busy', name: 'k-s2', sessionId: 'k' }]
  expect(snap(agents).cards.find(c => c.story === 'stuck')?.state).toBe('running')
  expect(buildSnapshot(stories(), undefined, new Map(), NOW).isAgentsKnown).toBe(false)
  expect(buildSnapshot(undefined, AGENTS, new Map(), NOW).isStoriesKnown).toBe(false)
})

test('quiet stories fold away, a done story with an open step stays, ticks prune with their story', () => {
  const s = snap()
  const shownNames = (ticks: Record<string, true>) => s.cards.filter(c => isShown(c, ticks, NOW)).map(c => c.story)
  expect(shownNames({})).not.toContain('old-stuck')
  expect(shownNames({})).toContain('fin')
  const old = { ...(card('fin') as NonNullable<ReturnType<typeof card>>), touchedMs: NOW - 5 * DAY }
  expect(isShown(old, {}, NOW)).toBe(true)
  const key = old.steps[0]?.key ?? ''
  expect(isShown(old, { [key]: true }, NOW)).toBe(false)
  expect(openSteps(old, { [key]: true })).toEqual([])
  expect(pruneTicks({ [key]: true, 'gone-story|x|y': true }, stories().map(s => s.name))).toEqual({ [key]: true })
  // A file that is there but parsed to nothing (caught half-written) keeps its ticks.
  expect(pruneTicks({ 'half|x|y': true }, ['half'])).toEqual({ 'half|x|y': true })
})

test('a close is read to its own end: other headings, one-line blocks, names with a note, real steps', () => {
  const text = [
    '## a-s1 (re-pointed to main) · 2026-10-16 10:00',
    'Next: started a-s2 (opus/xhigh), a-review (fable/xhigh)',
    'FOR ME',
    '1. Nothing now. At S3: your word on the merge.',
    '2. Nothing.',
    `3. ${'x'.repeat(80)} first`,
    `4. ${'x'.repeat(80)} second`,
    'OPEN: nothing blocked',
    '- not a step',
    '## a-s1 · 2026-10-16 10:00 (addendum)',
    'Next: none',
    'FOR ME',
    '- not a step either',
  ].join('\n')
  const [sec, ...rest] = parseStory('a', text, 0)?.sections ?? []
  expect(rest).toEqual([])
  expect(sec?.name).toBe('a-s1')
  expect(sec?.next).toMatchObject({ kind: 'started', names: ['a-s2', 'a-review'] })
  expect(sec?.forMe).toEqual(['Nothing now. At S3: your word on the merge.', `${'x'.repeat(80)} first`, `${'x'.repeat(80)} second`])
  expect(stepsOf(parseStory('a', text, 0) as Story)).toHaveLength(3)
})

test('a session named in two stories belongs to the one whose newest section names it', () => {
  const older = parseStory('older', close('shared-s1', 5 * HOUR, 'none'), NOW - 5 * HOUR)
  const newer = parseStory('newer', close('n-s1', HOUR, 'started shared-s1'), NOW - HOUR)
  const agents: Agent[] = [{ kind: 'interactive', status: 'busy', name: 'shared-s1', sessionId: 'x' }]
  const s = buildSnapshot([older, newer].filter((x): x is Story => x !== undefined), agents, new Map(), NOW)
  expect(s.cards.find(c => c.story === 'newer')?.chips.map(c => c.name)).toEqual(['shared-s1'])
  expect(s.cards.find(c => c.story === 'older')?.chips).toEqual([])
})

test('the session that needs a person leads, with what it waits for, and no line passes the width', () => {
  const chips = [...snap().cards.flatMap(c => c.chips), ...snap().loose]
  const lines = sessionLines(chips, 's3', NOW, 100)
  expect(lines[0]).toContain('6 live: 2 need you, 1 working, 3 idle')
  expect(lines[2]).toMatch(/^! (n-s2|brain-55) /)
  expect(lines.join('\n')).toMatch(/\n\* c-s3 +working +20m +app$/m)
  expect(lines.join('\n')).not.toContain('e-s2')
  for (const width of [60, 74, 100]) {
    for (const line of sessionLines(chips, 's3', NOW, width).slice(1)) expect(line.length).toBeLessThanOrEqual(width)
  }
})

test('each usage window says how long until its reset, and the Fable week is named as unseen', () => {
  const lines = usageLines(
    [
      { kind: 'five_hour', percentUsed: 51, resetsAt: new Date(NOW + 2 * HOUR + 14 * MIN).toISOString() },
      { kind: 'seven_day', percentUsed: 44.5, resetsAt: new Date(NOW + 3 * DAY + 4 * HOUR).toISOString() },
      { kind: 'spend_limit', percentUsed: 7 },
    ],
    NOW,
  )
  expect(lines[1]).toBe('  5-hour    51%  resets in 2h 14m')
  expect(lines[2]).toBe('  week    44.5%  resets in 3d 4h')
  expect(lines[3]).toBe('  spend_limit    7%')
  expect(lines[4]).toContain('Fable week: not visible to a mod')
  expect(usageLines([{ kind: 'five_hour', percentUsed: 80, resetsAt: new Date(NOW - 90 * MIN).toISOString() }], NOW)[1]).toBe(
    '  5-hour    80%  window reset 1h 30m ago, no reading since',
  )
  expect(usageLines(undefined, NOW)[1]).toContain('no reading yet')
})

test('the story table counts the states, names why a card needs a person, and folds the quiet ones', () => {
  const lines = storyLines(snap().cards, {}, NOW, false)
  expect(lines[0]).toBe('STORIES  2 need you, 4 stalled, 2 running, 2 done')
  expect(lines.join('\n')).toMatch(/\n {2}needy +NEEDS YOU +1 +0 +30m\n +n-s2 waits \(permission prompt\)\n/)
  expect(lines.join('\n')).not.toContain('old-stuck')
  expect(lines[lines.length - 1]).toBe('  1 more (1 not done), quiet: /where all')
  expect(storyLines(snap().cards, {}, NOW, true).join('\n')).toContain('old-stuck')
})

test('/forme wraps the open steps of each story touched in the last three days, or of the one named', () => {
  const cards = snap().cards
  const key = card('chain')?.steps[0]?.key ?? ''
  const lines = formeLines(cards, { [key]: true }, NOW, 60, '')
  expect(lines).toContain('chain  running, touched 20m ago, 1 ticked')
  expect(lines).toContain('  1. Run the migration.')
  expect(lines.join('\n')).not.toContain('Merge the branch')
  expect(lines).toContain('  1. Push main.')
  expect(lines.join('\n')).not.toContain('An old step')
  for (const line of lines.slice(1)) expect(line.length).toBeLessThanOrEqual(60)
  expect(formeLines(cards, {}, NOW, 60, 'old-stuck').join('\n')).toContain('No steps: old-stuck')
  expect(formeLines(cards, {}, NOW, 60, 'nope')).toContain('  no story "nope" with a close section')
})

const QUEUE = `# chain test queue

- Login screen · !93 · iPhone 15 · ready to test · 14:30
Settings toggle · !94 · Pixel 8 · building · 15:10
Vehicles list
`

test('a test queue reads one item per line, skips headings and blanks, and leaves a missing field empty', () => {
  expect(parseTests(QUEUE)).toEqual([
    { item: 'Login screen', mr: '!93', device: 'iPhone 15', state: 'ready to test', eta: '14:30', notes: '', gate: '' },
    { item: 'Settings toggle', mr: '!94', device: 'Pixel 8', state: 'building', eta: '15:10', notes: '', gate: '' },
    { item: 'Vehicles list', mr: '', device: '', state: '', eta: '', notes: '', gate: '' },
  ])
  expect(parseTests('a · b · c · d · e · f · g')[0]).toMatchObject({ eta: 'e', notes: 'f · g', gate: '' })
  expect(parseTests('\n# only a heading\n')).toEqual([])
})

test('the gate of a test-queue line is its first whole field that starts "Gate:"', () => {
  const gate = (line: string) => parseTests(line)[0]
  expect(gate('a · !1 · sim · ready to test · 14:30 · on build 412 · Gate: device test (iOS) · Gate: later')).toMatchObject({
    eta: '14:30',
    notes: 'on build 412 · Gate: device test (iOS) · Gate: later',
    gate: 'device test (iOS)',
  })
  expect(gate('a · !1 · Gate: your look')).toMatchObject({ mr: '!1', device: '', state: '', eta: '', notes: 'Gate: your look', gate: 'your look' })
  expect(gate('a · !1 · gate: merges on green · building · 15:00 · a note')).toMatchObject({
    device: 'building',
    state: '15:00',
    eta: 'a note',
    notes: 'gate: merges on green',
    gate: 'merges on green',
  })
  expect(gate('a · held at the gate: his word · the Gate: is review')).toMatchObject({ mr: 'held at the gate: his word', gate: '' })
  expect(gate('Gate: not an item name')).toMatchObject({ item: 'Gate: not an item name', gate: '' })
})

test('/where shows a gate column only when a line has one, and never passes 100 columns', () => {
  const old = parseTests(QUEUE)
  expect(testLines(old, '      ')).toEqual([
    '      Login screen     !93  iPhone 15  ready to test  14:30',
    '      Settings toggle  !94  Pixel 8    building       15:10',
    '      Vehicles list',
  ])
  const gated = parseTests(`${QUEUE}Charging history · !95 · Pixel 8 · ready to test ·  · Gate: device test on the release build\n`)
  expect(testLines(gated, '      ')).toEqual([
    '      Login screen      !93  iPhone 15  ready to test  14:30',
    '      Settings toggle   !94  Pixel 8    building       15:10',
    '      Vehicles list',
    '      Charging history  !95  Pixel 8    ready to test         device test o…',
  ])
  const wide = 'w'.repeat(60)
  const full = testLines(parseTests(`${wide} · ${wide} · ${wide} · ${wide} · ${wide} · Gate: ${wide}\nshort · !1`), '      ')
  expect(full.map(line => line.length)).toEqual([100, 22])
  expect(full[0]).toMatch(/^ {6}w{11}… {2}w{17}… {2}w{13}… {2}w{13}… {2}w{11}… {2}w{13}…$/)
  const plain = testLines(parseTests(`${wide} · ${wide} · ${wide} · ${wide} · ${wide} · ${wide}`), '      ')
  expect(plain[0]).toMatch(/^ {6}w{27}… {2}w{17}… {2}w{13}… {2}w{13}… {2}w{11}…$/)
  expect(plain[0]?.length).toBe(100)
})

test('a queued launch waits for a slot, not for you: running until its session comes up, then stalls like a started one', () => {
  const queued = parseStory('q', close('q-s1', 3 * HOUR, 'queued q-s2'), NOW - 3 * HOUR)
  expect(queued?.sections[0]?.next).toEqual({ kind: 'queued', names: ['q-s2'], text: 'queued q-s2' })
  const of = (agents: Agent[]) => buildSnapshot(queued === undefined ? [] : [queued], agents, new Map(), NOW).cards[0]
  expect([of([])?.state, of([])?.why]).toEqual(['running', 'queued: q-s2, launches when a slot frees'])
  const up = (status: string): Agent[] => [{ kind: 'interactive', status, name: 'q-s2', sessionId: 'q2', startedAt: NOW - MIN }]
  expect(of(up('busy'))?.state).toBe('running')
  expect([of(up('idle'))?.state, of(up('idle'))?.why]).toEqual(['stalled', 'q-s2 sits idle, no close'])
})

// handoff's capacity.py test writes the same shape: one fixture for the writer and this reader.
const at = (s: number): string => {
  const d = new Date(s * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}
const STATUS = status as CapacityStatus

test('the capacity block reads the shared status.json fixture: slots, queue, each resource, stalled, how old', () => {
  expect(capacityParts(STATUS, STATUS.at * 1000)).toEqual([
    'slots 2/3',
    `queued 1 (next ~${at(1786703100)})`,
    `emulator: you, test hold until ${at(1786701800)}`,
    `simulator: demo-s2 since ${at(1786700000)} (take)`,
    'backend free',
  ])
  const later = {
    ...STATUS,
    agents: 'unknown' as const,
    queue: [],
    chains: [{ story: 'demo', sessions: [{ name: 'demo-s2', stalled: true }] }],
  }
  expect(capacityParts(later, STATUS.at * 1000 + 6 * MIN)).toEqual([
    'slots ?/3',
    `emulator: you, test hold until ${at(1786701800)}`,
    `simulator: demo-s2 since ${at(1786700000)} (take)`,
    'backend free',
    'stalled: demo-s2',
    `as of ${at(STATUS.at)}`,
  ])
  expect(capacityParts({ ...STATUS, version: 2 }, NOW)).toEqual(['status.json version 2, this board reads 3'])
  const lines = capacityLines(capacityParts(STATUS, STATUS.at * 1000), 60)
  expect(lines[0]?.startsWith('CAPACITY  slots 2/3 · queued 1')).toBe(true)
  expect(lines.slice(1).every(l => l.startsWith(' '.repeat(10)) && !l.startsWith(' '.repeat(11)))).toBe(true)
  for (const line of lines) expect(line.length).toBeLessThanOrEqual(60)
})

const MR = 'https://gitlab.com/g/p/-/merge_requests'
const green = (name: string, msAgo: number): string =>
  `## ${name} · ${stamp(msAgo)}\nWatch: pipeline green · sha abc1234 · 41 min · ${MR}/7\nNext: none\nDONE - pipeline green\nFOR ME - nothing new\nOPEN - nothing\nLEFT - nothing left\n\n`

test('a registered watch is no session: the card runs while its name has no section, and is done after', () => {
  const text = close('w-s1', 3 * HOUR, `watching app!7 (${MR}/7)`)
  const story = parseStory('w', text, NOW - 3 * HOUR)
  expect(story?.sections[0]?.next).toMatchObject({ kind: 'watching', names: ['app!7'] })
  expect(story?.sections[0]?.links).toEqual([`${MR}/7`])
  const of = (t: string, agents: Agent[] = []) => {
    const parsed = parseStory('w', t, NOW - HOUR)
    return buildSnapshot(parsed === undefined ? [] : [parsed], agents, new Map(), NOW).cards[0]
  }
  expect([of(text)?.state, of(text)?.why]).toEqual(['running', 'watching app!7'])
  const idle: Agent[] = [{ kind: 'interactive', status: 'idle', name: 'w-s1', sessionId: 'w1', startedAt: NOW - 4 * HOUR }]
  expect(of(text, idle)?.state).toBe('running')
  const done = of(text + green('app!7', 2 * HOUR))
  expect([done?.state, done?.why, done?.closes]).toEqual(['done', 'last close says Next: none', 2])
  // a red: the watch's section starts the fix slice, whose close registers the next arm
  const red = `${text}## app!7 · ${stamp(2 * HOUR)}\nWatch: pipeline red\nNext: started w-fix-1\n\n`
  expect(of(red)?.why).toBe('w-fix-1: no session, no close')
  expect(of(red + close('w-fix-1', HOUR, `watching app!7#2 (${MR}/7)`))?.why).toBe('watching app!7#2')
})

test('the open watches of status.json keep a story running whose last close is another repo\'s', () => {
  const text = `${close('t-app', 3 * HOUR, `watching app!7 (${MR}/7)`)}${close('t-lib', 3 * HOUR, `watching lib!4 (${MR}/4)`)}${green('app!7', 2 * HOUR)}`
  const parsed = parseStory('two', text, NOW - 2 * HOUR)
  const of = (watches: CapacityStatus['watches']) => buildSnapshot(parsed === undefined ? [] : [parsed], [], new Map(), NOW, watches).cards[0]
  expect(of([])?.state).toBe('done')
  const arm = (name: string, story: string) => ({ ...(STATUS.watches[0] as CapacityStatus['watches'][number]), name, story })
  expect([of([arm('lib!4', 'two')])?.state, of([arm('lib!4', 'two')])?.why]).toEqual(['running', 'watching lib!4'])
  expect(of([arm('lib!4', 'other')])?.state).toBe('done')
  // an arm that has its section (a red one, kept while its fix runs) is the fix session's to carry
  expect(of([arm('app!7', 'two')])?.state).toBe('done')
  expect(watchesOf(STATUS).map(w => w.name)).toEqual(['app!12', 'lib!9', 'lib!10'])
  expect(watchesOf({ ...STATUS, version: 2 })).toEqual([])
  expect(watchLines(STATUS.watches, 100)).toEqual([
    `WATCHES   app!12 · demo · pending · armed ${at(1786699400)}`,
    `          lib!9 · side · fixing (side-fix-1 started) · armed ${at(1786696400)}`,
    `          lib!10 · next · waiting-base (lib!9) · armed ${at(1786697000)}`,
  ])
  const merging = [{ ...arm('app!7', 'two'), phase: 'merge' }]
  expect(watchLines(merging, 100)).toEqual([`WATCHES   app!7 · two · pending (merge) · armed ${at(1786699400)}`])
  expect(watchLines(merging, 30)).toEqual(['WATCHES   app!7 · two · pendi…'])
  expect(watchLines([], 100)).toEqual([])
})

test('a pane parked at a start-up prompt makes its story need you, until the file drops it or the tick stops', () => {
  const pane = { pane: '%7', tab: 'chain', index: '2', story: 'chain', line: 'Accessing workspace:', since: NOW / 1000 - 90 }
  const file = (msAgo: number, panes: unknown[]): string => JSON.stringify({ at: (NOW - msAgo) / 1000, panes })
  const chain = (text: string) =>
    buildSnapshot(stories(), AGENTS, new Map(), NOW, [], parkedOf(text, NOW)).cards.find(c => c.story === 'chain')
  expect([chain(file(5000, [pane]))?.state, chain(file(5000, [pane]))?.why]).toEqual([
    'needs-you',
    'parked at a start-up prompt: Accessing workspace:',
  ])
  expect(chain(file(5000, [{ ...pane, line: 'x'.repeat(80) }]))?.why).toBe(`parked at a start-up prompt: ${'x'.repeat(60)}`)
  expect(chain(file(5000, []))?.why).toBe('c-s3 working')
  expect(chain(file(5000, [{ ...pane, story: '' }, { ...pane, story: 'another' }]))?.state).toBe('running')
  expect(chain(file(6 * MIN, [pane]))?.state).toBe('running')
  expect(parkedOf('{', NOW)).toEqual([])
  expect(parkedOf(JSON.stringify({ at: NOW / 1000, panes: [null, { story: 3 }, { story: 'chain' }] }), NOW)).toEqual([])
  const needy = buildSnapshot(stories(), AGENTS, new Map(), NOW, [], [{ story: 'needy', line: 'x' }]).cards.find(c => c.story === 'needy')
  expect(needy?.why).toBe('n-s2 waits (permission prompt)')
})

const stubWorld = (on: On, agentsOut: string = JSON.stringify(AGENTS), capacity: CapacityStatus = STATUS, parked?: string) => {
  const commands: CommandSpec[] = []
  const store: Record<string, unknown> = {}
  const files: Record<string, string> = {
    '/home/n/.claude/stories/chain.md': CHAIN,
    '/home/n/.claude/stories/chain.tests.md': QUEUE,
    '/home/n/.claude/stories/needy.md': close('n-s1', 30 * MIN, 'started n-s2'),
    '/home/n/.claude/stories/fin.md': close('x-s1', 6 * HOUR, 'none', 'FOR ME\n- Push main.\n'),
    '/home/n/.claude/stories/draft.md': 'a prompt draft with FOR ME in it, no close section\n',
    '/home/n/.claude/session-stats/s5.json': JSON.stringify({ ctx_pct: 61, cost_usd: 141.2 }),
    '/home/n/.claude/session-stats/s1.json': '{"ctx_pct": 4',
    '/home/n/.claude/capacity/status.json': JSON.stringify({ ...capacity, at: NOW / 1000 }),
    ...(parked === undefined ? {} : { '/home/n/.claude/flowkit/parked.json': parked }),
  }
  const mtimes: Record<string, number> = {
    'chain.md': NOW - 20 * MIN,
    'chain.tests.md': NOW,
    'needy.md': NOW - 30 * MIN,
    'fin.md': NOW - 6 * HOUR,
    'draft.md': NOW,
  }
  mock.env(on, { HOME: '/home/n' })
  mock.clock(on, { now: NOW })
  on('store.get', (_, e) => ({ value: store[e.key] }))
  on('store.set', (_, e) => {
    store[e.key] = e.value
    return { value: undefined }
  })
  on('session.start', (_, e) => ({ cwd: e.cwd }))
  on('command.register', (_, e) => {
    commands.push(e)
    return { value: { command: e.name } }
  })
  on('session.id', () => ({ value: 's3' }))
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('session.usage', () => ({
    value: {
      startedAt: NOW - HOUR,
      context: { window: 1_000_000 },
      rateLimits: [{ kind: 'five_hour', percentUsed: 51, resetsAt: new Date(NOW + 2 * HOUR + 14 * MIN).toISOString() }],
    },
  }))
  on('process.run', (_, e) =>
    e.argv.join(' ').includes('claude agents --json --all')
      ? { value: { exitCode: 0, stdout: agentsOut, stderr: '', isStdoutTruncated: false, isStderrTruncated: false } }
      : { deny: `unexpected ${e.argv.join(' ')}` },
  )
  on('fs.list', (_, e) =>
    e.path === '/home/n/.claude/stories'
      ? {
          value: [
            ...Object.keys(mtimes).map(name => ({ name, kind: 'file' as const, size: 1, mtimeMs: mtimes[name] ?? 0, isLink: false })),
            { name: 'assets', kind: 'dir' as const, size: 0, mtimeMs: 0, isLink: false },
          ],
        }
      : { deny: `unexpected ${e.path}` },
  )
  on('fs.read', (_, e) => {
    const text = files[e.path]
    return text === undefined ? { deny: `ENOENT ${e.path}` } : { value: text }
  })
  return { commands, store }
}

const run = { args: '', origin: { kind: 'composer' }, presentation: { isFullscreen: true, columns: 120 } } as const

test('/board, /where and /forme register as immediate and answer from the stubbed machine', async ($, on) => {
  const { commands } = stubWorld(on)
  await $.session.start({ cwd: '/w/app', surface: 'terminal', isInteractive: true })
  expect(commands.map(c => [c.name, c.immediate])).toEqual([
    ['board', true],
    ['where', true],
    ['forme', true],
  ])

  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  const lines = board.split('\n')
  expect(lines[0]).toContain('live: 2 need you, 1 working')
  expect(board).toMatch(/\n! (n-s2|brain-55) .*NEEDS YOU/)
  expect(board).toMatch(/\n\* c-s3 +working +20m +app\n/)
  expect(board).toContain('  5-hour    51%  resets in 2h 14m')
  expect(board).toContain('STORIES  1 need you, 0 stalled, 1 running, 1 done')
  expect(board).toMatch(
    /\n\nCAPACITY {2}slots 2\/3 · queued 1 \(next ~\d\d:\d\d\) · emulator: you, test hold until .*\n(?: {10}\S.*\n)*WATCHES {3}app!12 · demo · pending · armed \d\d:\d\d\n {10}lib!9 · side · fixing \(side-fix-1 started\) · armed \d\d:\d\d\n {10}lib!10 · next · waiting-base \(lib!9\) · armed \d\d:\d\d\n\nSTORIES /,
  )
  expect(board).not.toContain('draft')
  expect(board).toMatch(
    /\n {2}chain +running .*\n {4}test queue\n {6}Login screen {5}!93 {2}iPhone 15 {2}ready to test {2}14:30\n {6}Settings toggle {2}!94 {2}Pixel 8 {4}building {7}15:10\n {6}Vehicles list\n/,
  )
  expect(board.match(/test queue/g)?.length).toBe(1)
  for (const line of lines) expect(line.length).toBeLessThanOrEqual(100)

  const steps = (await $.command.run({ ...run, command: 'forme' })).text ?? ''
  expect(steps).toContain('  1. Merge the branch when you choose.')
  expect(steps).toContain('  1. Push main.')
  const one = (await $.command.run({ ...run, command: 'forme', args: ' fin ' })).text ?? ''
  expect(one).toContain('FOR ME  the steps of fin')
  expect(one).not.toContain('Merge the branch')
})

test('/where counts a story with an open watch as running, though its last close says Next: none', async ($, on) => {
  const watch = { ...(STATUS.watches[0] as CapacityStatus['watches'][number]), name: 'app!3', story: 'fin' }
  stubWorld(on, JSON.stringify(AGENTS), { ...STATUS, watches: [watch] })
  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  expect(board).toContain('STORIES  1 need you, 0 stalled, 2 running, 0 done')
  expect(board).toMatch(/\nWATCHES {3}app!3 · fin · pending · armed \d\d:\d\d\n\nSTORIES /)
})

test('/where names the start-up prompt a story is parked at', async ($, on) => {
  const pane = { pane: '%7', tab: 'fin', index: '1', story: 'fin', line: 'Accessing workspace:', since: NOW / 1000 - 90 }
  stubWorld(on, JSON.stringify(AGENTS), STATUS, JSON.stringify({ at: NOW / 1000, panes: [pane] }))
  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  expect(board).toContain('STORIES  2 need you, 0 stalled, 1 running, 0 done')
  expect(board).toMatch(/\n {2}fin +NEEDS YOU .*\n +parked at a start-up prompt: Accessing workspace:\n/)
})

test('a section that cannot be read says so and the rest of the board still prints', async ($, on) => {
  mock.env(on, { HOME: '/home/n' })
  mock.clock(on, { now: NOW })
  on('store.get', () => ({ value: undefined }))
  on('session.id', () => ({ value: 's3' }))
  on('session.usage', () => ({ deny: 'no ledger' }))
  on('process.run', () => ({
    value: { exitCode: 127, stdout: '', stderr: 'claude: not found', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('fs.list', (_, e) => ({ deny: e.path.endsWith('/.claude/stories') ? 'EACCES' : 'ENOENT' }))

  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  expect(board).toContain('SESSIONS  `claude agents --json` did not answer')
  expect(board).toContain('no reading yet')
  expect(board).toContain('STORIES  ~/.claude/stories could not be read')
})

test('no stories directory yet is no story, not an error', async ($, on) => {
  mock.env(on, { HOME: '/home/n' })
  mock.clock(on, { now: NOW })
  on('store.get', () => ({ value: undefined }))
  on('session.id', () => ({ value: 's3' }))
  on('session.usage', () => ({ deny: 'no ledger' }))
  on('process.run', () => ({
    value: { exitCode: 0, stdout: '[]', stderr: '', isStdoutTruncated: false, isStderrTruncated: false },
  }))
  on('fs.list', () => ({ deny: 'ENOENT' }))
  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  expect(board).toContain('STORIES  0 need you, 0 stalled, 0 running, 0 done')
})

test('an answer that is not a list of sessions costs only the sessions block', async ($, on) => {
  stubWorld(on, '{"error":"busy"}')
  const board = (await $.command.run({ ...run, command: 'where' })).text ?? ''
  expect(board).toContain('SESSIONS  `claude agents --json` did not answer')
  expect(board).toContain('STORIES  0 need you, 2 stalled, 0 running, 1 done')
})

for (const surface of ['terminal', 'desktop', 'vscode'] as const) {
  test(`the pane draws a card per story on ${surface} and a tick persists`, async ($, on) => {
    const { store } = stubWorld(on)
    await $.session.start({ cwd: '/w/app', surface, isInteractive: true })
    const opened = await $.command.run({ ...run, command: 'board' })
    expect(opened.text).toBeDefined()

    const ui = await $.ui.mount({
      plugin: 'board',
      surface,
      component: 'Pane',
      requestId: 'board',
      props: { title: 'Board', isFocused: false, bodyColumns: 80, placement: 'dock', scroll: { offset: 0, bodyRows: 40 }, view: {} },
      viewport: { columns: 100, rows: 40, isFullscreen: true },
    })
    const all = (await ui.findAll({ type: 'Text' })).map(t => t.text).join('\n')
    expect(all).toContain('BOARD')
    expect(all).toMatch(/CAPACITY[\s\S]*slots 2\/3 · queued 1/)
    expect(all).toContain('NEEDS YOU')
    expect(all).toContain('needy')
    expect(all).toContain('chain')
    expect(all).toContain('c-s3 · working')
    expect(all).toContain('n-s2 waits (permission prompt)')
    expect(all).toContain('TEST QUEUE\nLogin screen · !93 · iPhone 15 · ready to test · 14:30\nSettings toggle · !94 · Pixel 8 · building · 15:10\nVehicles list')
    expect(all.match(/TEST QUEUE/g)?.length).toBe(1)
    expect((await ui.findAll({ type: 'Link' })).map(l => [l.props.href, l.props.label])).toEqual([
      ['https://gitlab.com/g/p/-/merge_requests/93', 'MR !93  p'],
    ])

    const buttons = await ui.findAll({ type: 'Button' })
    expect(buttons.filter(b => b.key?.startsWith('text:')).map(b => b.text)).toEqual([
      'Merge the branch when you choose.',
      'Run the migration.',
      'Tap through the new screen on the device, then say which of the two layouts stays. - the first layout is the old one',
      'Push main.',
    ])
    const boxes = buttons.filter(b => b.key?.startsWith('step:'))
    expect(boxes.map(b => b.text)).toEqual(['[ ]', '[ ]', '[ ]', '[ ]'])
    const first = boxes[0]?.key ?? ''
    await ui.press({ key: first })
    expect(Object.keys(store.ticks as Record<string, true>)).toEqual([first.replace('step:', '')])
    expect((await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('step:')).map(b => b.text)).toEqual(['[x]', '[ ]', '[ ]', '[ ]'])

    await ui.press({ key: first.replace('step:', 'text:') })
    expect(store.ticks).toEqual({})
    await ui.unmount()
  })
}
