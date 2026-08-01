# Security Policy

## Supported version

Security fixes target the latest GitHub release and the default branch.

## Report a vulnerability

Use this repository's GitHub private security advisory flow. Do not open a
public issue containing secrets, local configuration, prompt content, command
history, memory notes, or event files.

Include the affected revision, operating system, reproduction steps, expected
behavior, and the smallest redacted evidence that proves the issue.

## Trust boundary

This project installs user-level Codex and Claude Hooks and therefore executes
for every managed lifecycle event. Review source and tests before installation.
The runtime is not an operating-system sandbox and does not replace repository
authorization, branch protection, CI, account permissions, or provider-side
credential controls.

The intended privacy guarantee is narrower: runtime evidence never stores raw
prompts, commands, tool payloads, tool outputs, secrets, cookies, or full paths.
Treat any violation of that guarantee as a security issue.
