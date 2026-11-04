# hermcp-restart: restart hermcp after a Hermes update

Units in this directory:

| File | Role |
|---|---|
| `hermcp-restart.path` | Watches `/usr/local/lib/hermes-agent/install-stamp.json` (`PathChanged`). Hermes replaces that file atomically at the end of each update (`hermes_cli/source_stamp.py`, tempfile + `os.replace`). |
| `hermcp-restart.service` | Oneshot. Waits 60 s (debounce), then runs `hermcp-restart.sh`. `StartLimitBurst=3` per 600 s stops restart loops. |
| `hermcp-restart.sh` | Restarts `hermcp.service` only if the stamp is a new build (`builtAt`, not `adoptedAt`) and newer than hermcp's start. Otherwise it does nothing. |

It never touches the gateway or the dashboard.

## Install

`hermes hermcp install-service` renders `hermcp-restart.path.template` and `hermcp-restart.service.template` for this
install and writes them next to `hermcp.service` (opt out with `--no-auto-restart`, preview with `--dry-run`). The
Hermes checkout is detected, not hardcoded; see the README section "Auto-restart after Hermes updates".
The static `hermcp-restart.path` / `.service` files stay for the manual route below, which assumes the reference
layout (`/usr/local/lib/hermes-agent`, plugin in `~/.hermes/plugins/hermcp`).

### Manual (once, root)

    cd ~/.hermes/plugins/hermcp/deploy/systemd
    systemd-analyze verify hermcp-restart.path hermcp-restart.service   # no errors for these two
    systemctl link "$PWD/hermcp-restart.path" "$PWD/hermcp-restart.service"   # links, no copy
    systemctl daemon-reload
    systemctl enable --now hermcp-restart.path
    systemctl is-enabled hermcp-restart.path; systemctl is-active hermcp-restart.path

`systemctl link` keeps the repo as the single source. Units stay in `/etc/systemd/system/`.

## Test without a restart

The script is a no-op for an old stamp, so a trigger on the live stamp is harmless:

    cp -p /usr/local/lib/hermes-agent/install-stamp.json ~/.hermes/cache/scratch/stamp.bak
    cp /usr/local/lib/hermes-agent/install-stamp.json /usr/local/lib/hermes-agent/.stamp-test.tmp
    mv -f /usr/local/lib/hermes-agent/.stamp-test.tmp /usr/local/lib/hermes-agent/install-stamp.json
    journalctl -u hermcp-restart.service -n 5 --no-pager   # expect "already runs this build ... skipping"

Hermes-core is not modified: the stamp content stays byte-identical.

## Uninstall / rollback

    systemctl disable --now hermcp-restart.path
    systemctl stop hermcp-restart.service 2>/dev/null; true
    systemctl revert hermcp-restart.path hermcp-restart.service
    systemctl daemon-reload

Tip: tag your repo state before installing, so you can roll back.
