CREATE TABLE agent_profiles (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  name_key     TEXT NOT NULL UNIQUE,
  harness_id   TEXT NOT NULL,
  model        TEXT NOT NULL,
  effort       TEXT,
  mode         TEXT,
  max_mode     INTEGER NOT NULL DEFAULT 0,
  instructions TEXT NOT NULL DEFAULT '',
  skills_json  TEXT NOT NULL DEFAULT '[]',
  created_at   INTEGER NOT NULL,
  updated_at   INTEGER NOT NULL
, speed TEXT NOT NULL DEFAULT 'off'
  CHECK (speed IN ('off', 'fast', 'ultrafast')));

CREATE TABLE claude_session_seen (
  session_id TEXT PRIMARY KEY,
  seen_at    INTEGER NOT NULL
);

CREATE TABLE harness_usage (
  harness_id  TEXT PRIMARY KEY,
  snapshot_json TEXT NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE "harnesses" (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL CHECK (kind IN ('claude-code', 'codex', 'cursor', 'grok', 'gemini', 'kimi', 'fx', 'antigravity')),
  label      TEXT NOT NULL,
  is_builtin INTEGER NOT NULL DEFAULT 0,
  home       TEXT,
  bin        TEXT,
  env_json   TEXT NOT NULL DEFAULT '{}',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  enabled    INTEGER NOT NULL DEFAULT 1
, profile_mcp_servers INTEGER NOT NULL DEFAULT 1);

CREATE TABLE pipelines (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  name_key    TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  graph       TEXT NOT NULL,
  max_steps   INTEGER NOT NULL DEFAULT 25,
  created_at  INTEGER NOT NULL,
  updated_at  INTEGER NOT NULL
);

CREATE TABLE preferences (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE projects (
  path TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  added_at INTEGER NOT NULL
, branch_config TEXT);

CREATE TABLE retention_swept_tasks (
  task_id   TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
  sweep_key TEXT NOT NULL
);

CREATE TABLE run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
  stream TEXT NOT NULL,
  data TEXT NOT NULL,
  ts INTEGER NOT NULL
, line_uuid TEXT, subagent_id TEXT);

CREATE TABLE runs (
  id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  agent TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at INTEGER NOT NULL,
  ended_at INTEGER,
  exit_code INTEGER
, tmux_session TEXT, claude_session_id TEXT, codex_session_id TEXT, origin TEXT, cursor_session_id TEXT, gemini_session_id TEXT, fx_session_id TEXT, antigravity_session_id TEXT);

CREATE TABLE saved_prompts (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  content    TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE subagents (
  id          TEXT PRIMARY KEY,                       -- claude agentId (basename of agent-<id>.jsonl)
  task_id     TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  run_id      TEXT REFERENCES runs(id) ON DELETE CASCADE, -- parent run active when the subagent was spawned
  parent_kind TEXT NOT NULL DEFAULT 'subagent',       -- 'subagent' | 'bg_session' (future)
  agent_type  TEXT,                                   -- meta.json.agentType (e.g. "Explore")
  description TEXT,                                   -- meta.json.description
  spawn_depth INTEGER NOT NULL DEFAULT 1,             -- meta.json.spawnDepth (1 = spawned by the main agent)
  source_path TEXT NOT NULL,                          -- absolute path to agent-<id>.jsonl
  status      TEXT NOT NULL DEFAULT 'running',        -- running|completed|failed|cancelled|orphaned
  started_at  INTEGER NOT NULL,
  ended_at    INTEGER
, tool_use_id TEXT);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  prompt TEXT NOT NULL,
  column TEXT NOT NULL,
  agent TEXT NOT NULL,
  workdir TEXT NOT NULL,
  run_id TEXT,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
, isolation TEXT NOT NULL DEFAULT 'worktree', branch TEXT, worktree_path TEXT, base_ref TEXT, mode TEXT, model TEXT, attachments TEXT NOT NULL DEFAULT '[]', effort TEXT, refs TEXT NOT NULL DEFAULT '[]', archived_at INTEGER, task_type TEXT NOT NULL DEFAULT 'task', backlog TEXT NOT NULL DEFAULT '[]', branch_source TEXT NOT NULL DEFAULT 'created', draft TEXT, pr_url TEXT, max_mode INTEGER NOT NULL DEFAULT 0, plans TEXT NOT NULL DEFAULT '[]', todo_progress TEXT, last_assistant_event_id INTEGER, last_seen_event_id INTEGER, issue_url TEXT, sent_files TEXT, fx_recovery TEXT, agent_profile_id TEXT, agent_profile TEXT, pipeline_id TEXT, pipeline_run TEXT, pipeline_parent_id TEXT, pipeline_step_id TEXT, checkout_branch TEXT, imported_claude TEXT, imported_claude_session_id TEXT, speed TEXT NOT NULL DEFAULT 'off'
  CHECK (speed IN ('off', 'fast', 'ultrafast')));

CREATE TABLE usage_daily (
  config_dir TEXT NOT NULL,
  day TEXT NOT NULL,            -- UTC date, YYYY-MM-DD
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  cache_write_tokens INTEGER NOT NULL DEFAULT 0,
  cache_read_tokens INTEGER NOT NULL DEFAULT 0,
  message_count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (config_dir, day, model)
);

CREATE TABLE usage_files (
  path TEXT PRIMARY KEY,
  config_dir TEXT NOT NULL,
  mtime_ms REAL NOT NULL,
  size INTEGER NOT NULL,
  offset INTEGER NOT NULL
);

CREATE TABLE usage_seen (
  config_dir TEXT NOT NULL,
  key TEXT NOT NULL,
  PRIMARY KEY (config_dir, key)
) WITHOUT ROWID;

CREATE INDEX idx_agent_profiles_harness_id ON agent_profiles(harness_id);

CREATE INDEX claude_session_seen_at_idx ON claude_session_seen(seen_at);

CREATE INDEX idx_run_events_run ON run_events(run_id, id);

CREATE UNIQUE INDEX run_events_run_sub_line_uuid
  ON run_events(run_id, IFNULL(subagent_id, ''), line_uuid) WHERE line_uuid IS NOT NULL;

CREATE INDEX idx_run_events_user_history
  ON run_events(stream, id DESC) WHERE subagent_id IS NULL;

CREATE INDEX idx_runs_task ON runs(task_id);

CREATE INDEX runs_claude_session_id_idx
  ON runs(claude_session_id) WHERE claude_session_id IS NOT NULL;

CREATE INDEX idx_subagents_task ON subagents(task_id, started_at);

CREATE INDEX idx_subagents_running
  ON subagents(task_id) WHERE status = 'running';

CREATE INDEX idx_tasks_pipeline_parent ON tasks(pipeline_parent_id);

CREATE INDEX idx_tasks_pipeline_id ON tasks(pipeline_id) WHERE pipeline_id IS NOT NULL;

CREATE UNIQUE INDEX tasks_imported_claude_session_uidx
  ON tasks(imported_claude_session_id)
  WHERE imported_claude_session_id IS NOT NULL;

