// capacity/status.json as handoff's capacity.py writes it: the board reads this, and handoff's
// test_capacity.py parses what follows `export default` as JSON and checks its shape against the writer.
export default {
  "version": 3,
  "at": 1786700000.0,
  "agents": "known",
  "slots": 3,
  "used": 2,
  "repos": [
    {
      "path": "/Users/me/app",
      "slots": 1,
      "used": 1
    }
  ],
  "chains": [
    {
      "tree": "/Users/me/app-wt",
      "repo": "/Users/me/app",
      "story": "demo",
      "sessions": [
        {
          "name": "demo-s2",
          "session": "aaaaaaaa-5555-5555-5555-555555555555",
          "status": "busy",
          "stalled": false
        },
        {
          "name": "demo-review",
          "session": "bbbbbbbb-5555-5555-5555-555555555555",
          "status": "idle",
          "stalled": false
        }
      ],
      "pid": null,
      "state": "running"
    },
    {
      "tree": "/Users/me/lib",
      "repo": "/Users/me/lib",
      "story": "side",
      "sessions": [],
      "state": "launching",
      "pid": "4242"
    }
  ],
  "resources": [
    {
      "name": "emulator",
      "session": "me",
      "holder": "me",
      "story": "",
      "since": 1786700000.0,
      "why": "test hold",
      "until": 1786701800.0
    },
    {
      "name": "simulator",
      "session": "aaaaaaaa-5555-5555-5555-555555555555",
      "holder": "demo-s2",
      "story": "demo",
      "since": 1786700000.0,
      "why": "take",
      "until": null
    },
    {
      "name": "backend",
      "session": null,
      "holder": null,
      "story": null,
      "since": null,
      "why": null,
      "until": null
    }
  ],
  "queue": [
    {
      "id": 7,
      "name": "next-s1",
      "story": "next",
      "tree": "/Users/me/lib",
      "needs": [
        "simulator"
      ],
      "left": 2,
      "queued": 1786700100.0,
      "by": "coordinator",
      "continuation": false,
      "why": "slots 2/3",
      "eta": 1786703100.0
    }
  ],
  "watches": [
    {
      "name": "app!12",
      "story": "demo",
      "state": "pending",
      "phase": "pipeline",
      "url": "https://gitlab.com/g/p/-/merge_requests/12",
      "sha": "1111111111111111111111111111111111111111",
      "armed": 1786699400.0,
      "polled": 1786699940.0,
      "answer": "pipeline running",
      "fix": null,
      "waits": null
    },
    {
      "name": "lib!9",
      "story": "side",
      "state": "fixing",
      "phase": "pipeline",
      "url": "https://gitlab.com/g/p/-/merge_requests/9",
      "sha": "2222222222222222222222222222222222222222",
      "armed": 1786696400.0,
      "polled": 1786698200.0,
      "answer": "pipeline failed",
      "fix": {
        "name": "side-fix-1",
        "state": "started"
      },
      "waits": null
    },
    {
      "name": "lib!10",
      "story": "next",
      "state": "waiting-base",
      "phase": "pipeline",
      "url": "https://gitlab.com/g/p/-/merge_requests/10",
      "sha": "3333333333333333333333333333333333333333",
      "armed": 1786697000.0,
      "polled": 1786698260.0,
      "answer": "pipeline failed",
      "fix": null,
      "waits": "lib!9"
    }
  ]
}
