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

export type Snapshot = {
  at: number
  cards: Card[]
  loose: Chip[]
  isAgentsKnown: boolean
  isStoriesKnown: boolean
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
