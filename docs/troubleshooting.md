# Troubleshooting

The symptoms worth knowing before opening an issue.

[← README](../README.md) · [docs index](../README.md#documentation)

---

| Symptom | Likely cause / fix |
|---|---|
| `403 host not allowed` | The public hostname is missing in `HERMCP_ALLOWED_HOSTS`; add it (and `HERMCP_PUBLIC_URL`). |
| `401` on `/mcp` | Missing or wrong `Authorization: Bearer hmcp_...`; behind Pangolin make sure resource-level SSO/basic auth is **off** (it swallows the header). |
| Tool missing in `tools/list` | The key's `access`/`toolsets`/`profiles` or a `deny_tools` entry hides it; check `keys list`. |
| `serve` exits with code 75 | Another instance holds the lock or the port; see [Single instance](install.md#single-instance) and `hermes hermcp status`. |
| `ModuleNotFoundError` / odd `mcp` import errors | Run with the Hermes virtualenv's Python and unset a foreign `PYTHONPATH` (`env -u PYTHONPATH ...`). |
| Many tests fail locally (`No module named 'ruamel'` in a probe) | Run `scripts/run_tests.sh`; see Development. |
| Anything else | `hermes hermcp doctor` checks imports, permissions, lock, unit and proxy settings. |

## `413` on `/mcp` or `/inbox`

`POST /mcp` accepts bodies up to 1 MB, `POST /inbox` up to 8 KB. The limit is enforced while the body arrives: a
`Content-Length` over the limit is refused before any byte is read, and without the header (chunked uploads) the read
stops as soon as the running total passes the limit, so the process never buffers more than the limit plus one chunk.
Split a large batch into several requests.
