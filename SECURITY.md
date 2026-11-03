# Security Policy

hermcp exposes the Hermes control surface over the network, so security reports are taken seriously.

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Use GitHub's private vulnerability reporting: open the **Security** tab of this repository and
choose **Report a vulnerability**. Include:

- what you found and the impact you expect,
- steps or a minimal proof of concept,
- the hermcp commit/version and your Hermes version.

You can expect an acknowledgement within a few days. Fixes are released first, details are
published afterwards, and reporters are credited unless they prefer otherwise.

## Scope

In scope: authentication/authorization bypass, scope escalation (a key doing more than its
`access`/`toolsets`/`profiles`/flags allow), secret exposure in tool results or the audit log,
Host-header / DNS-rebinding issues, OAuth flow weaknesses.

Out of scope: an **admin-scoped key with `toolsets: *` doing admin things** (that is effectively
shell access on the Hermes install by design, see "Security model" in the README), and
vulnerabilities in Hermes Agent itself (report those upstream).

## Hardening reminders

- Bind to loopback and put a TLS reverse proxy in front.
- Keep `HERMCP_ALLOWED_HOSTS` tight.
- Give each client its own key with the narrowest scopes; rotate with `keys rotate`.
