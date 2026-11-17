export type CardState = 'needs-you' | 'stalled' | 'running' | 'done'

export type Chip = {
  id: string
  name: string
  state: 'working' | 'needs-you' | 'idle' | 'ended'
  detail?: string
  ctx?: number
  cost?: number
  startedAt?: number
  repo?: string
  isReporter: boolean
}

export type Step = { key: string; text: string; from: string }

// A line of the story's test queue, <story>.tests.md: item · MR · device · state · ETA.
export type TestLine = { item: string; mr: string; device: string; state: string; eta: string }

export type Card = {
  story: string
  state: CardState
  why: string
  closes: number
  touchedMs: number
  next: string
  chips: Chip[]
  steps: Step[]
  links: string[]
  tests: TestLine[]
}

// capacity/status.json, as handoff's capacity.py writes it (version 3); the board only reads it.
export type CapacityStatus = {
  version: number
  at: number
  agents: 'known' | 'unknown'
  slots: number
  used: number | null
  chains: { story: string; sessions: { name: string; stalled: boolean }[] }[]
  resources: { name: string; session: string | null; holder: string | null; since: number | null; why: string | null; until: number | null }[]
  queue: { name: string; eta: number | null }[]
  watches: Watch[]
}

// An open watch of handoff's watch.py: a pipeline a script polls for a story, until its own section is recorded.
export type Watch = {
  name: string
  story: string
  state: string
  phase: string
  armed: number
  fix: { name: string; state: string } | null
  waits: string | null
}

export type Snapshot = {
  at: number
  cards: Card[]
  loose: Chip[]
  isAgentsKnown: boolean
  isStoriesKnown: boolean
  capacity?: string[]
  watches?: Watch[]
}

declare module 'claude-code' {
  interface PluginState {
    board: {
      snapshot: Snapshot | null
      ticks: Record<string, true>
      isOlderShown: boolean
      expanded: Record<string, true>
    }
  }
}
