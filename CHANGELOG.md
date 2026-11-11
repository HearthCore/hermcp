# Changelog

All notable changes to hermcp are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). hermcp has no tagged releases yet, so
entries are grouped by the day they landed on `main`; every line links the commit (and the pull
request, where there is one). `CONTRIBUTING.md` explains how to keep this file current.

## [Unreleased]

### Added
- `HERMCP_PROFILES_IGNORE`: a host-wide list of profiles hermcp leaves out of `doctor`, `profile-keys`, `profiles_list` and the status tools; profile-targeted tools reject them with `profile_ignored`. It is not a key scope, so keys stay unrestricted ([`acd5774`](https://github.com/HearthCore/hermcp/commit/acd5774), [#44](https://github.com/HearthCore/hermcp/pull/44))

### Tests
- Make the Ledger fixtures relative to the current time instead of fixed dates, and keep `HERMCP_DEBUG_CAPTURE` from a local `.env` out of the default-behaviour tests

_Add your entry here, in the same pull request that makes the change._

### Added
- This changelog, backfilled from the first 44 commits, and the rule to update it in every pull request ([#41](https://github.com/HearthCore/hermcp/issues/41))

## 2026-11-10

### Added
- Opt-in read-only situation page on hermcp's own port ([`5ad2805`](https://github.com/HearthCore/hermcp/commit/5ad2805), [#29](https://github.com/HearthCore/hermcp/pull/29))
- Key-less mode for private networks, and rename Lagebild -> Ledger ([`6445c8a`](https://github.com/HearthCore/hermcp/commit/6445c8a), [#31](https://github.com/HearthCore/hermcp/pull/31))
- `HERMCP_TRUSTED_PROXIES` — one place that decides who the client is ([`9008161`](https://github.com/HearthCore/hermcp/commit/9008161), [#33](https://github.com/HearthCore/hermcp/pull/33))
- Pass model, provider and model_options per Hermes run ([`b67d257`](https://github.com/HearthCore/hermcp/commit/b67d257), [#32](https://github.com/HearthCore/hermcp/pull/32))
- Dismiss failures in the view, and mark superseded cron runs ([`f896491`](https://github.com/HearthCore/hermcp/commit/f896491), [#34](https://github.com/HearthCore/hermcp/pull/34))
- Runs and run events name their client and purpose in plain words ([`c982b56`](https://github.com/HearthCore/hermcp/commit/c982b56), [#37](https://github.com/HearthCore/hermcp/pull/37))
- `clients_overview` tool and a Clients block on the Ledger page ([`9ad50d5`](https://github.com/HearthCore/hermcp/commit/9ad50d5), [#39](https://github.com/HearthCore/hermcp/pull/39))
- Kanban link for dashboard mode ({base}/kanban?profile=default) ([`c8db19d`](https://github.com/HearthCore/hermcp/commit/c8db19d), [#40](https://github.com/HearthCore/hermcp/pull/40))

### Fixed
- `hermes_status` counts the served profile; overview lists the default board without board.json ([`809165e`](https://github.com/HearthCore/hermcp/commit/809165e), [#27](https://github.com/HearthCore/hermcp/pull/27))
- Absolute endpoint paths and drop the key from the address ([`6aef7aa`](https://github.com/HearthCore/hermcp/commit/6aef7aa), [#30](https://github.com/HearthCore/hermcp/pull/30))
- Run without PyYAML — one yaml surface over PyYAML or ruamel.yaml ([`3314467`](https://github.com/HearthCore/hermcp/commit/3314467))

## 2026-11-09

### Added
- Run ledger and full output by run id ([`d491896`](https://github.com/HearthCore/hermcp/commit/d491896), [#28](https://github.com/HearthCore/hermcp/pull/28))

## 2026-11-07

### Added
- Opt-in request capture to discover client session identifiers ([`1ca0574`](https://github.com/HearthCore/hermcp/commit/1ca0574), [#13](https://github.com/HearthCore/hermcp/pull/13))
- Track read state per consumer instead of globally ([`851858c`](https://github.com/HearthCore/hermcp/commit/851858c), [#14](https://github.com/HearthCore/hermcp/pull/14))
- `hermes_status` level-0 digest, slim hermes_health default, count-only ack all ([`3ab4bf0`](https://github.com/HearthCore/hermcp/commit/3ab4bf0), [#15](https://github.com/HearthCore/hermcp/pull/15))
- Bearer-token fingerprint + report grouped by x-anthropic-client ([`5f79145`](https://github.com/HearthCore/hermcp/commit/5f79145), [#16](https://github.com/HearthCore/hermcp/pull/16))
- Run progress from the gateway; own vs other work in hermes_status ([`2693f55`](https://github.com/HearthCore/hermcp/commit/2693f55), [#17](https://github.com/HearthCore/hermcp/pull/17))
- Attribute runs to a client and a client-chosen session handle ([`f33ffd2`](https://github.com/HearthCore/hermcp/commit/f33ffd2), [#18](https://github.com/HearthCore/hermcp/pull/18))
- Ledger of client sessions (cards, handle shaping, foreign-run guard, recover, limit) ([`6a4a481`](https://github.com/HearthCore/hermcp/commit/6a4a481), [#19](https://github.com/HearthCore/hermcp/pull/19))
- Session situation report, claim of unclaimed runs, notices to the former owner ([`a0e4143`](https://github.com/HearthCore/hermcp/commit/a0e4143), [#20](https://github.com/HearthCore/hermcp/pull/20))
- Keys rename and keys scopes ([`0dde080`](https://github.com/HearthCore/hermcp/commit/0dde080), [#21](https://github.com/HearthCore/hermcp/pull/21))

### Fixed
- Park on invalid config, keep doctor output to one line ([`70876f0`](https://github.com/HearthCore/hermcp/commit/70876f0), [#12](https://github.com/HearthCore/hermcp/pull/12))

### Documentation
- Rewrite install flow, document the `hermes hermcp` CLI, refresh features ([`12a9ca1`](https://github.com/HearthCore/hermcp/commit/12a9ca1), [#22](https://github.com/HearthCore/hermcp/pull/22))
- Split the README into docs/, add the architecture diagram ([`521cf58`](https://github.com/HearthCore/hermcp/commit/521cf58), [#24](https://github.com/HearthCore/hermcp/pull/24))

## 2026-11-06

### Added
- Configurable systemd managers for the gateway and for hermcp itself ([`0a11fc0`](https://github.com/HearthCore/hermcp/commit/0a11fc0))

### Fixed
- Start the approvals bridge once the gateway is up, not on the first message ([`6bda391`](https://github.com/HearthCore/hermcp/commit/6bda391))
- An empty served_profiles roster means the gateway serves its own home ([`dc2d61e`](https://github.com/HearthCore/hermcp/commit/dc2d61e))
- Report an invalid systemd scope readably instead of a traceback ([`b291536`](https://github.com/HearthCore/hermcp/commit/b291536))

### Documentation
- Document scripts/run_tests.sh and fix the stale test command ([`084ca43`](https://github.com/HearthCore/hermcp/commit/084ca43))
- Document HERMCP_SYSTEMD_SCOPE and the approvals bridge autostart ([`620d5e5`](https://github.com/HearthCore/hermcp/commit/620d5e5))

### Tests
- Add scripts/run_tests.sh for a reproducible test environment ([`2eddb27`](https://github.com/HearthCore/hermcp/commit/2eddb27))

## 2026-11-05

### Added
- Agents_live tool and running-agents preview for restarts ([`ee27296`](https://github.com/HearthCore/hermcp/commit/ee27296), [#10](https://github.com/HearthCore/hermcp/pull/10))

## 2026-11-04

### Added
- Install the auto-restart units by default and detect the Hermes checkout ([`d64fb1b`](https://github.com/HearthCore/hermcp/commit/d64fb1b), [#5](https://github.com/HearthCore/hermcp/pull/5))
- Resolve the api_server key per profile and add `profile-keys` ([`47d5012`](https://github.com/HearthCore/hermcp/commit/47d5012))
- Approvals bridge for a multiplex gateway (mode detection + profile filter) ([`381b117`](https://github.com/HearthCore/hermcp/commit/381b117))

### Fixed
- Count per-profile approvals on the shared multiplex socket ([`074e0af`](https://github.com/HearthCore/hermcp/commit/074e0af))

### Documentation
- Note AGENTS.md setup flow tested with GitHub Copilot CLI ([`93f6244`](https://github.com/HearthCore/hermcp/commit/93f6244))
- Fix stale tool counts and git clone placeholder in README ([`09e7a8a`](https://github.com/HearthCore/hermcp/commit/09e7a8a), [#4](https://github.com/HearthCore/hermcp/pull/4))

### Tests
- Assert the api_server key rows, not doctor's global exit code ([`fb199be`](https://github.com/HearthCore/hermcp/commit/fb199be))
- Cover the refusal over the real bridge socket (integration) ([`bfb5a7f`](https://github.com/HearthCore/hermcp/commit/bfb5a7f))

## 2026-11-03

### Changed
- Initial public release of hermcp ([`6d30221`](https://github.com/HearthCore/hermcp/commit/6d30221))

### Documentation
- Replace hard-coded home paths with generic wording ([`5a9974c`](https://github.com/HearthCore/hermcp/commit/5a9974c))
- Add AGENTS.md setup guide for coding agents ([`5c5eff7`](https://github.com/HearthCore/hermcp/commit/5c5eff7))
