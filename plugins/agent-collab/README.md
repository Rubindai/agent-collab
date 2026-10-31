# Agent Collab

Agent Collab brings an independent Claude Code or Codex peer into code review, planning, research, and debugging. The host and peer examine the task separately, then the host verifies the findings and combines the evidence. A finite deadline and helper budget bound each run.

## Install and use

Install this dual-host package through the repository's Codex or Claude Code marketplace. Follow the [setup and installation guide](https://github.com/Rubindai/agent-collab#install), then ask your host to "use agent collab to review this diff" or "use agent collab to critique this plan". Both provider CLIs and Python 3.11 or newer must be available. Linux full-capability runs also require working Bubblewrap and FUSE OverlayFS.

In Claude Code, install from the GitHub marketplace with:

```text
/plugin install agent-collab --marketplace Rubindai/agent-collab
```

The default peer is Claude Opus 5.5 at xhigh from a Codex host, or GPT-6 Astra at xhigh from a Claude host. Every launch checks the requested model and effort; an unavailable pair stops the run without a fallback. Model access and provider usage charges depend on your accounts.

## Permissions and data

The plugin runs local Python scripts and the installed provider CLIs using your existing provider authentication. The host's task brief and any files, command output, or research the peer reads enter the selected provider's context. Provider tools, MCP servers, plugins, and general networking remain subject to the provider's configuration and policy. Local settings, run reports, and diagnostic output are persisted in the [documented state locations](https://github.com/Rubindai/agent-collab#storage-locations). Reports may contain source code or other task data.

Full capability is the default, including Claude's `bypassPermissions`; edit authorization is separate and starts disabled. Bypass permissions do not prevent prompt injection or unintended actions induced by repository text, tool results, or online sources; a no-edit prompt and snapshots are not preventive controls. Anthropic recommends bypass mode only in isolated containers or VMs without internet access where host damage is impossible. Agent Collab's Linux disposable workspace retains networking and reachable host service sockets: writes through these channels can persist and are not observed. Overlay file writes are discarded. macOS and Windows default to direct workspaces with bounded snapshots that detect included changes after execution; they do not prevent or undo changes.

Request safe mode to reduce provider permissions. Claude uses native plan permissions. Codex uses its native sandbox, with an initialization check on Linux; Codex safe mode is unavailable on native Windows. Durable delegated edits require an explicitly authorized direct workspace. See the [full documentation](https://github.com/Rubindai/agent-collab#readme) for platform limits, settings, cleanup, and evidence boundaries.

## License

MIT. The included [LICENSE](LICENSE) permits reuse, modification, and redistribution while requiring preservation of the copyright and license notices.
