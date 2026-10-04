/* Synthetic fixtures only. No terminal or agent is connected. */
window.MOCK_SESSIONS = [
  {
    "id": "atlas-1",
    "device": "Studio Mac",
    "project": "zudo-agent-dashboard",
    "name": "gallery-capture",
    "title": "Which capture layout should I keep?",
    "workflow": "review",
    "primaryPane": "%3",
    "panes": [
      {
        "id": "%3",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "waiting",
          "confidence": "high",
          "age": "8 sec ago",
          "freshness": "fresh",
          "evidence": "Prompt requests a choice before continuing."
        },
        "lines": [
          "Reviewing gallery capture boundaries…",
          "✓ Capture fixture refreshed",
          "✓ Draft stays local to this browser",
          "",
          "Choose the next layout:",
          "  1. Gallery-first inspection",
          "  2. Workflow board",
          "Waiting for your selection ▍"
        ]
      },
      {
        "id": "%30",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "8 sec ago",
          "freshness": "fresh",
          "evidence": "Synthetic shell pane; no agent process is present."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-agent-dashboard]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "atlas-2",
    "device": "Studio Mac",
    "project": "zudo-agent-dashboard",
    "name": "status-model",
    "title": "Separate observed state from workflow",
    "workflow": "progress",
    "primaryPane": "%8",
    "panes": [
      {
        "id": "%8",
        "name": "claude-code",
        "agent": "Claude Code",
        "observation": {
          "activity": "working",
          "confidence": "high",
          "age": "12 sec ago",
          "freshness": "fresh",
          "evidence": "Progress lines changed across two fixture snapshots."
        },
        "lines": [
          "Reading session event fixtures…",
          "Mapped 6 observed activity states",
          "No completion inferred from silence",
          "",
          "› Comparing prompt-boundary evidence",
          "  src/session-observation.ts",
          "  tests/observation-fixtures.test.ts",
          "Analyzing next fixture…"
        ]
      },
      {
        "id": "%31",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "12 sec ago",
          "freshness": "fresh",
          "evidence": "Synthetic shell pane; no agent process is present."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-agent-dashboard]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "atlas-3",
    "device": "Studio Mac",
    "project": "zudo-doc-cloud",
    "name": "sidebar-tree",
    "title": "Tree keyboard navigation",
    "workflow": "review",
    "primaryPane": "%12",
    "panes": [
      {
        "id": "%12",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "completed",
          "confidence": "high",
          "age": "35 sec ago",
          "freshness": "fresh",
          "evidence": "Explicit final response and successful fixture exit."
        },
        "lines": [
          "✓ 24 / 24 tree navigation tests passed",
          "✓ Roving focus stays within the tree",
          "✓ Parent expansion is preserved",
          "",
          "Task completed.",
          "Changed 3 fixture files.",
          "Ready for review."
        ]
      },
      {
        "id": "%32",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "35 sec ago",
          "freshness": "fresh",
          "evidence": "Synthetic shell pane; no agent process is present."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-doc-cloud]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "atlas-4",
    "device": "Studio Mac",
    "project": "zudo-text",
    "name": "editor-idle",
    "title": "Editor draft restoration",
    "workflow": "progress",
    "primaryPane": "%16",
    "panes": [
      {
        "id": "%16",
        "name": "claude-code",
        "agent": "Claude Code",
        "observation": {
          "activity": "idle",
          "confidence": "medium",
          "age": "1 min ago",
          "freshness": "fresh",
          "evidence": "Agent process present; no output change in sampled interval."
        },
        "lines": [
          "Restored the synthetic editor fixture",
          "Viewport state retained",
          "",
          "Last output: 68 seconds ago",
          "Agent process is present.",
          "No active progress signal observed.",
          "",
          "Completion has not been established."
        ]
      }
    ]
  },
  {
    "id": "forge-1",
    "device": "Linux Devbox",
    "project": "zudo-agent-dashboard",
    "name": "terminal-shell",
    "title": "Terminal smoke checks",
    "workflow": "inbox",
    "primaryPane": "%2",
    "panes": [
      {
        "id": "%2",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "18 sec ago",
          "freshness": "fresh",
          "evidence": "Only an interactive shell is present in the fixture."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-agent-dashboard]",
          "$ git status --short",
          "",
          "Working tree clean.",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "forge-2",
    "device": "Linux Devbox",
    "project": "zudo-doc-cloud",
    "name": "publish-check",
    "title": "Publish check · connection lost",
    "workflow": "inbox",
    "primaryPane": "%5",
    "panes": [
      {
        "id": "%5",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "unknown",
          "confidence": "unknown",
          "age": "14 min ago",
          "freshness": "stale",
          "evidence": "Capture is stale; current agent presence and activity cannot be established."
        },
        "lines": [
          "Preparing synthetic publication checks…",
          "✓ Manifest parsed",
          "Checking bundle integrity…",
          "",
          "[Last captured output]",
          "No newer snapshot is available.",
          "Current activity is unknown."
        ]
      },
      {
        "id": "%35",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "unknown",
          "confidence": "unknown",
          "age": "14 min ago",
          "freshness": "stale",
          "evidence": "Capture is stale; current agent presence and activity cannot be established."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-doc-cloud]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "forge-3",
    "device": "Linux Devbox",
    "project": "zudo-text",
    "name": "export-worker",
    "title": "Markdown export edge cases",
    "workflow": "progress",
    "primaryPane": "%7",
    "panes": [
      {
        "id": "%7",
        "name": "claude-code",
        "agent": "Claude Code",
        "observation": {
          "activity": "waiting",
          "confidence": "medium",
          "age": "42 sec ago",
          "freshness": "fresh",
          "evidence": "A confirmation-like prompt is visible; no structured event."
        },
        "lines": [
          "4 export fixtures need review",
          "Nested lists",
          "Mixed-language headings",
          "Long filenames",
          "",
          "Continue with the proposed fallback? [y/n]",
          "▍"
        ]
      }
    ]
  },
  {
    "id": "forge-4",
    "device": "Linux Devbox",
    "project": "zudo-agent-dashboard",
    "name": "gallery-performance-long-session-name",
    "title": "Thumbnail virtualization & long names",
    "workflow": "done",
    "primaryPane": "%11",
    "panes": [
      {
        "id": "%11",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "completed",
          "confidence": "high",
          "age": "2 min ago",
          "freshness": "fresh",
          "evidence": "Explicit final summary reports the fixture task finished."
        },
        "lines": [
          "✓ 120 simulated captures rendered",
          "✓ Scroll position retained on filtering",
          "✓ Card metadata updated without reflow",
          "",
          "Task completed.",
          "Fixture performance report is ready.",
          "No live terminal was accessed."
        ]
      },
      {
        "id": "%37",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "2 min ago",
          "freshness": "fresh",
          "evidence": "Synthetic shell pane; no agent process is present."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-agent-dashboard]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "travel-1",
    "device": "Travel Laptop",
    "project": "zudo-text",
    "name": "draft-recovery",
    "title": "Recover unsent drafts",
    "workflow": "progress",
    "primaryPane": "%1",
    "panes": [
      {
        "id": "%1",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "working",
          "confidence": "high",
          "age": "21 sec ago",
          "freshness": "fresh",
          "evidence": "Fresh progress output is present in the fixture."
        },
        "lines": [
          "Reading draft recovery fixtures…",
          "✓ Compose text persists while inspecting",
          "Checking IME commit boundaries",
          "",
          "› Running focused fixture checks",
          "  11 / 16 checks complete",
          "Working…"
        ]
      },
      {
        "id": "%38",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "21 sec ago",
          "freshness": "fresh",
          "evidence": "Synthetic shell pane; no agent process is present."
        },
        "lines": [
          "$ pwd",
          "[synthetic root: zudo-text]",
          "$ git status --short",
          "",
          "$ ▍"
        ]
      }
    ]
  },
  {
    "id": "travel-2",
    "device": "Travel Laptop",
    "project": "zudo-doc-cloud",
    "name": "docs-shell",
    "title": "Documentation review",
    "workflow": "done",
    "primaryPane": "%4",
    "panes": [
      {
        "id": "%4",
        "name": "shell",
        "agent": "None",
        "observation": {
          "activity": "no-agent",
          "confidence": "high",
          "age": "55 sec ago",
          "freshness": "fresh",
          "evidence": "Fixture process list contains a shell and no agent."
        },
        "lines": [
          "$ cd zudo-doc-cloud",
          "$ ls docs",
          "architecture.md  navigation.md",
          "",
          "$ ▍",
          "",
          "Manual Done does not prove an agent completed."
        ]
      }
    ]
  },
  {
    "id": "travel-3",
    "device": "Travel Laptop",
    "project": "zudo-agent-dashboard",
    "name": "ambiguous-prompt",
    "title": "Ambiguous terminal prompt",
    "workflow": "inbox",
    "primaryPane": "%6",
    "panes": [
      {
        "id": "%6",
        "name": "codex",
        "agent": "Codex",
        "observation": {
          "activity": "unknown",
          "confidence": "low",
          "age": "28 sec ago",
          "freshness": "fresh",
          "evidence": "Prompt text is ambiguous; no confident classification."
        },
        "lines": [
          "Reviewing a partial capture…",
          "…the previous response is outside the viewport",
          "",
          "> next",
          "",
          "No structured activity event is available.",
          "Observed activity: unknown.",
          "No action is sent from this dashboard."
        ]
      }
    ]
  },
  {
    "id": "travel-4",
    "device": "Travel Laptop",
    "project": "zudo-text",
    "name": "notes-review",
    "title": "Review notes & shortcuts",
    "workflow": "review",
    "primaryPane": "%9",
    "panes": [
      {
        "id": "%9",
        "name": "claude-code",
        "agent": "Claude Code",
        "observation": {
          "activity": "idle",
          "confidence": "medium",
          "age": "3 min ago",
          "freshness": "fresh",
          "evidence": "Unchanged output while the agent process remains present."
        },
        "lines": [
          "Saved the synthetic notes fixture",
          "Reviewing keyboard shortcut notes",
          "",
          "No new output during the sampled interval.",
          "The agent remains present.",
          "",
          "Idle does not mean task completed."
        ]
      }
    ]
  }
];
