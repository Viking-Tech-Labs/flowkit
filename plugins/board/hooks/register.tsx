import { atom, read, update } from 'claude-code'
import type { CommandRunInput, EngineInterface, Register } from 'claude-code'

import type { Card, Chip, Snapshot } from '../types'
import {
  age,
  buildSnapshot,
  countsOf,
  formeLines,
  isShown,
  openSteps,
  pruneTicks,
  sessionLines,
  stateLabel,
  stateWord,
  storyLines,
  parseStory,
  usageLines,
  usd,
} from './model'
import type { Agent, Stats, Story, Ticks } from './model'

const PANE = 'board'
// A card with more steps than this lists the open ones only, until its button shows all.
const CAP = 5
const REFRESH_MS = 8000
// The sessions come from `claude agents --json --all`: interactive ones carry `status`, background
// ones `state`, and `status` too while their process runs.
const AGENTS = [
  '/bin/sh',
  '-c',
  'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH" exec claude agents --json --all',
]

const snapshot = atom({ plugin: 'board', key: 'snapshot' } as const, null)
const ticks = atom({ plugin: 'board', key: 'ticks' } as const, {})
const isOlderShown = atom({ plugin: 'board', key: 'isOlderShown' } as const, false)
const expanded = atom({ plugin: 'board', key: 'expanded' } as const, {})

const soft = <T,>(work: Promise<T>): Promise<T | undefined> => work.catch(() => undefined)

const widthOf = (e: CommandRunInput): number => Math.min(100, Math.max(60, e.presentation.columns - 6))

const loadStories = async ($: EngineInterface, home: string): Promise<Story[]> => {
  const dir = `${home}/.claude/stories`
  const files = (await $.fs.list(dir)).filter(f => f.kind === 'file' && f.name.endsWith('.md'))
  const rows = await Promise.all(
    files.map(async f => parseStory(f.name.slice(0, -3), await $.fs.read(`${dir}/${f.name}`), f.mtimeMs)),
  )
  return rows.filter((r): r is Story => r !== undefined)
}

const loadTicks = async ($: EngineInterface): Promise<Ticks> => {
  const stored = await soft($.store.get('ticks'))
  return typeof stored === 'object' && stored !== null ? (stored as Ticks) : {}
}

type World = { snap: Snapshot; stories: Story[] | undefined; marks: Ticks }

// Everything the board and the text commands draw from: the sessions, the story files, the stats the
// status line writes, the ticks. A part that cannot be read leaves the rest.
const gather = async ($: EngineInterface): Promise<World> => {
  const home = (await $.env.get('HOME')) ?? ''
  const [now, ran, stories, marks] = await Promise.all([
    $.clock.now(),
    soft($.process.run(AGENTS, { timeoutMs: 5000 })),
    soft(loadStories($, home)),
    loadTicks($),
  ])
  let agents: Agent[] | undefined
  try {
    const parsed: unknown = ran?.exitCode === 0 ? JSON.parse(ran.stdout) : undefined
    if (Array.isArray(parsed)) agents = parsed as Agent[]
  } catch {
    agents = undefined
  }
  const stats = new Map<string, Stats>()
  await Promise.all(
    (agents ?? [])
      .filter(a => a.sessionId !== undefined)
      .map(async a => {
        const text = await soft($.fs.read(`${home}/.claude/session-stats/${a.sessionId}.json`))
        if (text === undefined) return
        try {
          stats.set(a.sessionId ?? '', JSON.parse(text) as Stats)
        } catch {
          // a file caught half-written: the row prints without its figures
        }
      }),
  )
  return { snap: buildSnapshot(stories, agents, stats, now), stories, marks }
}

const refresh = async ($: EngineInterface): Promise<void> => {
  const { snap, stories, marks } = await gather($)
  await update($, snapshot, () => snap)
  const kept = stories === undefined ? marks : pruneTicks(marks, stories)
  if (Object.keys(kept).length !== Object.keys(marks).length) await $.store.set('ticks', kept)
  await update($, ticks, () => kept)
}

const toggle = async ($: EngineInterface, key: string): Promise<void> => {
  const marks = { ...(await loadTicks($)) }
  if (marks[key] === true) delete marks[key]
  else marks[key] = true
  await $.store.set('ticks', marks)
  await update($, ticks, () => marks)
}

const hhmm = (ms: number): string => {
  const d = new Date(ms)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
}

const linkLabel = (href: string): string => {
  const m = /([^/]+)\/(merge_requests|pull)\/(\d+)$/.exec(href.replace('/-/', '/'))
  return m === null ? href.replace(/^https?:\/\//, '') : `${m[2] === 'pull' ? 'PR #' : 'MR !'}${m[3]}  ${m[1]}`
}

const COLOR = { 'needs-you': 'warning', stalled: 'error', running: 'success', done: 'inactive' } as const

const chipLine = (c: Chip, now: number): string =>
  [
    c.isReporter ? `${c.name} (reporter)` : c.name,
    stateLabel(c),
    c.ctx === undefined ? '' : `ctx ${c.ctx}%`,
    c.cost === undefined ? '' : usd(c.cost),
    c.startedAt === undefined ? '' : age(now - c.startedAt),
  ]
    .filter(part => part !== '')
    .join(' · ')

const answer = (work: Promise<string>): Promise<{ text: string }> =>
  work.then(
    text => ({ text }),
    (error: unknown) => ({ text: `could not answer: ${String(error)}` }),
  )

const where = async ($: EngineInterface, e: CommandRunInput): Promise<string> => {
  const [{ snap, marks }, me, usage] = await Promise.all([gather($), $.session.id(), soft($.session.usage())])
  const chips = [...snap.cards.flatMap(c => c.chips), ...snap.loose]
  return [
    ...(snap.isAgentsKnown
      ? sessionLines(chips, me, snap.at, widthOf(e))
      : ['SESSIONS  `claude agents --json` did not answer']),
    '',
    ...usageLines(usage?.rateLimits, snap.at),
    '',
    ...(snap.isStoriesKnown
      ? storyLines(snap.cards, marks, snap.at, e.args.trim() === 'all')
      : ['STORIES  ~/.claude/stories could not be read']),
  ].join('\n')
}

const forme = async ($: EngineInterface, e: CommandRunInput): Promise<string> => {
  const { snap, marks } = await gather($)
  return formeLines(snap.cards, marks, snap.at, widthOf(e), e.args.trim()).join('\n')
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    // A reload fires session.start again; the commands are registered by then, which is no fault.
    await soft(
      $.command.register({
        name: 'board',
        description: 'Open the board: a card per story, its FOR ME steps to tick, its MR links',
        immediate: true,
      }),
    )
    await soft(
      $.command.register({
        name: 'where',
        description: 'Live sessions, usage windows and stories, now (no model turn)',
        argumentHint: '[all]',
        immediate: true,
      }),
    )
    await soft(
      $.command.register({
        name: 'forme',
        description: 'The FOR ME steps of each recent story, or of one (no model turn)',
        argumentHint: '[story]',
        immediate: true,
      }),
    )
    $.clock.every(REFRESH_MS, async () => {
      // Only a pane that is open costs a `claude agents` call.
      if ((await soft($.ui.panes()))?.some(p => p.id === PANE)) await soft(refresh($))
    })
    return next(e)
  })

  on('command.run', { command: 'board' }, async $ => {
    const opened = await $.ui.open({ id: PANE, title: 'Board' })
    await soft(refresh($))
    return { text: opened.isPlaced ? 'Board opened.' : `The board could not be placed: ${opened.reason}` }
  })
  on('command.run', { command: 'where' }, ($, e) => answer(where($, e)))
  on('command.run', { command: 'forme' }, ($, e) => answer(forme($, e)))

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button, Link } = $.ui.resolve(e)
    const snap = await read($, snapshot)
    const marks = await read($, ticks)
    const isOlder = await read($, isOlderShown)
    const wide = await read($, expanded)

    if (snap === null) return <Text dimColor>Reading the sessions and the story files…</Text>

    const counts = countsOf(snap.cards)
    const quiet = snap.cards.filter(c => !isShown(c, marks, snap.at))
    const shown = isOlder ? snap.cards : snap.cards.filter(c => !quiet.includes(c))
    const waitingLoose = snap.loose.filter(c => c.state === 'needs-you')
    let hot = 0

    const card = (c: Card) => {
      const open = openSteps(c, marks)
      const live = c.chips.filter(chip => chip.state === 'working' || chip.state === 'needs-you')
      const idle = c.chips.filter(chip => chip.state === 'idle')
      const isFull = c.steps.length <= CAP || wide[c.story] === true
      const listed = isFull ? c.steps : open.slice(0, CAP)
      return (
        <Box key={`card:${c.story}`} flexDirection="column" borderStyle="round" borderColor={COLOR[c.state]} paddingX={1}>
          <Box flexDirection="row" justifyContent="space-between">
            <Text>
              <Text color={COLOR[c.state]} weight="bold">
                {stateWord(c.state)}
              </Text>{' '}
              <Text weight="bold">{c.story}</Text>
            </Text>
            <Text dimColor>
              {c.closes} {c.closes === 1 ? 'close' : 'closes'} · {age(snap.at - c.touchedMs)}
            </Text>
          </Box>
          <Text dimColor wrap="truncate-end">
            {c.why}
          </Text>
          {live.map(chip => (
            <Text key={`chip:${chip.id || chip.name}`} color={chip.state === 'needs-you' ? 'warning' : undefined} wrap="truncate-end">
              {chip.state === 'needs-you' ? '! ' : '● '}
              {chipLine(chip, snap.at)}
            </Text>
          ))}
          {idle.length > 0 && (
            <Text dimColor wrap="truncate-end">
              idle at the prompt: {idle.map(chip => chip.name).join(', ')}
            </Text>
          )}
          {c.steps.length > 0 && (
            <Box flexDirection="column" marginTop={1}>
              <Text weight="bold">
                FOR ME{' '}
                <Text dimColor weight="regular">
                  {open.length} of {c.steps.length} to do
                </Text>
              </Text>
              {listed.map(step => {
                const isDone = marks[step.key] === true
                hot += 1
                return (
                  <Box key={`row:${step.key}`} flexDirection="row" gap={1}>
                    <Button
                      key={`step:${step.key}`}
                      plain
                      dimColor={isDone}
                      hotkey={hot <= 9 ? String(hot) : undefined}
                      onPress={() => toggle($, step.key)}
                    >
                      {isDone ? '[x]' : '[ ]'}
                    </Button>
                    <Box flexShrink={1}>
                      <Button key={`text:${step.key}`} plain dimColor={isDone} onPress={() => toggle($, step.key)}>
                        {step.text}
                      </Button>
                    </Box>
                  </Box>
                )
              })}
              {c.steps.length > CAP && (
                <Button
                  plain
                  dimColor
                  key={`all:${c.story}`}
                  onPress={() =>
                    update($, expanded, v => {
                      const next = { ...v }
                      if (next[c.story] === true) delete next[c.story]
                      else next[c.story] = true
                      return next
                    })
                  }
                >
                  {isFull ? 'show the open steps only' : `show all ${c.steps.length} steps (${c.steps.length - listed.length} more)`}
                </Button>
              )}
            </Box>
          )}
          {c.links.length > 0 && (
            <Box flexDirection="column" marginTop={1}>
              {c.links.map(href => (
                <Link key={`link:${href}`} href={href} label={linkLabel(href)} />
              ))}
            </Box>
          )}
        </Box>
      )
    }

    return (
      <Box flexDirection="column">
        <Text>
          <Text weight="bold">BOARD</Text>{' '}
          <Text dimColor>
            {counts['needs-you'] + waitingLoose.length} need you · {counts.stalled} stalled · {counts.running} running · {counts.done}{' '}
            done · {hhmm(snap.at)}
          </Text>
        </Text>
        {!snap.isStoriesKnown && <Text color="error">~/.claude/stories could not be read</Text>}
        {!snap.isAgentsKnown && <Text color="error">`claude agents` did not answer: session states are missing</Text>}
        {snap.isStoriesKnown && snap.cards.length === 0 && (
          <Text dimColor>No story yet: a /handoff:close in a session with a Story line writes ~/.claude/stories/&lt;story&gt;.md.</Text>
        )}
        {waitingLoose.length > 0 && (
          <Box flexDirection="column" borderStyle="round" borderColor="warning" paddingX={1}>
            <Text color="warning" weight="bold">
              NEEDS YOU · sessions outside a story
            </Text>
            {waitingLoose.map(chip => (
              <Text key={`loose:${chip.id || chip.name}`} wrap="truncate-end">
                ! {chipLine(chip, snap.at)}
              </Text>
            ))}
          </Box>
        )}
        {shown.map(card)}
        {quiet.length > 0 && (
          <Button plain dimColor key="older" onPress={() => update($, isOlderShown, v => !v)}>
            {isOlder ? 'hide' : 'show'} {quiet.length} quiet {quiet.length === 1 ? 'story' : 'stories'}
          </Button>
        )}
        {snap.loose.length > 0 && (
          <Text dimColor>
            {snap.loose.filter(c => c.state === 'working').length} working and {snap.loose.filter(c => c.state === 'idle').length}{' '}
            idle outside a story: /where lists them
          </Text>
        )}
      </Box>
    )
  })
}
