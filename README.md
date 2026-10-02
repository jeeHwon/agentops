# Codex AgentOps

Codex AgentOps adds turn-level MLflow observability to harness agents that run in the standard Codex CLI. Users edit only the agent instructions and skills; the installed plugin handles tracing, token usage, redaction, retries, and background quality evaluation.

## What it provides

- Standard harness-agent folders with `AGENTS.md`, `spec.md`, and local `SKILL.md` files
- Normal `codex` CLI workflow without a custom chat wrapper
- One MLflow trace for each completed Codex turn
- Nested spans for supported tools, MCP calls, and subagents
- Exact input, output, cached-input, cache-write, reasoning, and total token usage
- Background `RelevanceToQuery`, `Safety`, and `Completeness` feedback
- Secret and PII redaction, a 15,000-character content limit, and a durable SQLite outbox
- Diagnostics and retry commands that do not block the Codex response

## Requirements

- macOS or Linux
- Python 3.11 or later
- [Codex CLI](https://learn.chatgpt.com/docs/hooks) with plugin and hook support
- [Databricks CLI](https://docs.databricks.com/aws/en/dev-tools/cli/) with at least one authenticated profile
- [`uv`](https://docs.astral.sh/uv/)
- An existing Databricks MLflow experiment and a SQL warehouse available to the authenticated user

## Install

```bash
git clone https://github.com/jeeHwon/agentops.git
cd agentops
./install.sh
```

The installer adds the `codex-agentops` Python CLI and installs the repository as a local Codex plugin marketplace.

## Configure

```bash
codex-agentops configure \
  --profile <databricks-profile> \
  --experiment /Shared/codex-agentops
```

Use `--warehouse-id <id>` to select a specific SQL warehouse. Without it, the CLI selects the first available warehouse. If `--profile` is omitted, the CLI asks you to select one and never silently chooses a profile.

Configuration performs real checks: Databricks authentication, SQL warehouse access, MLflow experiment lookup, a write-and-read test trace, Codex configuration parsing, and background scorer registration.

The default experiment is `/Shared/codex-agentops`. The experiment must already exist. Use `--no-content` if prompts, responses, and supported tool bodies must not be stored.

## Create and test an agent

```bash
codex-agentops init my-agent
cd my-agent
codex-agentops validate
codex
```

Open `/hooks` once in Codex and review and trust the `codex-agentops` hooks. Continue using the normal `codex` command. Each `Stop` or `Interrupt` closes the current turn and starts an asynchronous MLflow upload; the session does not need to end.

The generated folder contains only the editable agent definition:

```text
my-agent/
├── agent.yaml
├── README.md
├── spec.md
├── AGENTS.md
├── CLAUDE.md
└── .agents/skills/
    └── example-skill/
        └── SKILL.md
```

Edit `spec.md`, `AGENTS.md`, and the `SKILL.md` files to implement business behavior. Runtime, server, and monitoring code stays in the installed package and plugin.

## Validate and diagnose

`validate` checks the agent manifest and local skill structure:

```bash
codex-agentops validate [agent-folder]
```

`doctor` checks the installation, plugin, hooks, Databricks authentication, MLflow access, OTel status, and local outbox:

```bash
codex-agentops doctor --write-test-trace
codex-agentops status
codex-agentops flush
```

## Trace model

Each completed user turn becomes an `agent.turn` root trace. Supported subagents become `AGENT` spans, and their supported tool and MCP calls become child `TOOL` spans. The root token usage includes both the root turn and its completed subagents; each subagent span also carries its own token attributes.

Codex AgentOps reads prompt, response, and supported tool content only from stable hook payloads. It does not reconstruct those bodies from the transcript. Transcript parsing is limited to versioned `token_usage_record` entries as a guarded token fallback.

When the local collector receives Codex OTel logs, `response.completed` supplies token usage and `codex.tool_result` supplies actual tool duration and success. An existing external OTel exporter is preserved; in that configuration, exact tool duration is unavailable unless the same events are also routed to Codex AgentOps. `doctor` reports this condition.

Hosted tools such as `WebSearch` do not use the local function-tool hook path and therefore do not appear as tool spans. See the official [Codex Hooks documentation](https://learn.chatgpt.com/docs/hooks) and [Codex OTel configuration](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).

## Privacy

Content capture is enabled by default and can be disabled during configuration with `--no-content`. Before storage, the runtime masks common secrets, email addresses, phone numbers, and identity-number patterns, then limits each body to 15,000 characters. Review these defaults before using the project with regulated or highly sensitive data.

Local state is stored under the platform XDG config and data directories. Override them with `CODEX_AGENTOPS_CONFIG_DIR` and `CODEX_AGENTOPS_DATA_DIR`.

## Update or uninstall

```bash
./scripts/update.sh
./scripts/uninstall.sh
```

Uninstalling removes the CLI, plugin, marketplace entry, and the managed OTel block. It preserves local configuration and queued telemetry data.

## Development

```bash
uv sync --dev
uv run pytest
```

## License

MIT
