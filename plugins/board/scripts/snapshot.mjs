// flowkit-board's work: reads what the board reads (README "What it reads") and prints its stories
// as JSON. A part that cannot be read leaves the rest, as on the board.
import { execFileSync } from 'node:child_process'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { homedir } from 'node:os'

import { buildSnapshot, exportOf, parkedOf, parseStory, parseTests, watchesOf } from '../hooks/model.ts'

const USAGE = `flowkit-board snapshot [--agents -]

Prints one JSON object: the stories of ~/.claude/stories as the board's cards, and the live
sessions of no story. The sessions come from \`claude agents --json --all\`; with \`--agents -\`
they are read from stdin instead (that command's JSON, for a caller that already has it).`

const TESTS = '.tests.md'
const args = process.argv.slice(2)
if (args[0] !== 'snapshot' || (args.length > 1 && args.slice(1).join(' ') !== '--agents -')) {
  console.error(USAGE)
  process.exit(2)
}

const soft = work => {
  try {
    return work()
  } catch {
    return undefined
  }
}
const env = process.env
const root = env.CLAUDE_CONFIG_DIR || `${homedir()}/.claude`
const text = path => soft(() => readFileSync(path, 'utf8'))
const json = raw => (raw === undefined ? undefined : soft(() => JSON.parse(raw)))

const loadStories = () => {
  const dir = `${root}/stories`
  let names
  try {
    names = readdirSync(dir)
  } catch (e) {
    return e?.code === 'ENOENT' ? [] : undefined
  }
  const stories = []
  for (const name of names.filter(n => n.endsWith('.md') && !n.endsWith(TESTS))) {
    const body = text(`${dir}/${name}`)
    const mtimeMs = soft(() => statSync(`${dir}/${name}`).mtimeMs)
    const story = body === undefined || mtimeMs === undefined ? undefined : parseStory(name.slice(0, -3), body, mtimeMs)
    if (story === undefined) continue
    const tests = text(`${dir}/${story.name}${TESTS}`)
    if (tests !== undefined) story.tests = parseTests(tests)
    stories.push(story)
  }
  return stories
}

const loadAgents = () => {
  const raw =
    args.length > 1
      ? text(0)
      : soft(() =>
          execFileSync('claude', ['agents', '--json', '--all'], {
            encoding: 'utf8',
            timeout: 5000,
            stdio: ['ignore', 'pipe', 'ignore'],
            env: { ...env, PATH: `${env.PATH ?? ''}:${homedir()}/.local/bin:/opt/homebrew/bin:/usr/local/bin` },
          }),
        )
  const parsed = json(raw)
  return Array.isArray(parsed) ? parsed : undefined
}

const now = Date.now()
const stories = loadStories()
const agents = loadAgents()
const stats = new Map()
for (const a of agents ?? []) {
  const found = typeof a?.sessionId === 'string' ? json(text(`${root}/session-stats/${a.sessionId}.json`)) : undefined
  if (found !== undefined && found !== null && typeof found === 'object') stats.set(a.sessionId, found)
}
const status = json(text(`${root}/capacity/status.json`))
const watches = (status !== undefined && soft(() => watchesOf(status))) || []
const parked = parkedOf(text(`${root}/flowkit/parked.json`) ?? '', now)
console.log(JSON.stringify(exportOf(stories, buildSnapshot(stories, agents, stats, now, watches, parked))))
