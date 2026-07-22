# app/data/ - runtime state directory

Nothing in here is committed except this note and `settings.example.json`.
At runtime the app creates, in this same directory:

| File | What it is |
| --- | --- |
| `bidboard.db` (+ `-wal`, `-shm`) | SQLite database: companies, sites, scan runs, bid rows, flags, build audit |
| `settings.json` | the live preference document, schema-validated on every write |
| `server.lock` | single-instance guard carrying the chosen port and pid |
| `.setup-complete` | marker written by SETUP once dependencies are installed |
| `logs/server-log.txt` | rolling runtime log |

## settings.example.json

A complete, schema-valid settings document you can copy to `settings.json`
to start from a known-good state. You do not have to: launching the app with
no `settings.json` writes `DEFAULT_SETTINGS` from
`app/src/bidboard/settings.py`. Every key is validated against
`SETTINGS_JSON_SCHEMA` in that same module, and unknown keys are rejected
(`additionalProperties: false` throughout), so extend the schema and the
defaults together.

`advanced.respect_robots` is `true` here and in the in-code defaults: this
build honours robots.txt unless you explicitly turn it off. The toggle is
exposed in Advanced settings for operators who have confirmed they are
permitted to fetch a particular site; turning it off is your call and your
responsibility.
