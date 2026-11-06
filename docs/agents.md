# Using it with an agent

1. **Run DataNyx yourself**, and read the schema before sharing it.
2. **Give the agent only the schema**, and block its access to the data file or
   database. DataNyx produces a schema but doesn't restrict the agent on its own: if it can
   open the file, it can still see the data. [`datanyx protect`](#enforcing-it-with-datanyx-protect)
   sets this up for Claude Code.
3. **Run the agent's code yourself** against the real data. Code that prints rows or
   logs values would expose them if the agent sees that output.

If the agent runs DataNyx itself, its output is still safe to show, as long as you
accept that `--keep-name` lets it [test guesses](schemas.md#adjusting-what-gets-hidden).

## Enforcing it with `datanyx protect`

```sh
datanyx protect ./data                      # shows what it would change
datanyx protect ./data --write              # writes it, then checks that the guard works
datanyx protect ./data --write --command "uv run datanyx"   # if datanyx isn't on Claude Code's PATH
datanyx protect --remove --write            # takes it all out again
```

For Claude Code this writes, and only with `--write`:

- **`.claude/settings.json`** (or `settings.local.json` with `--local`): deny rules for `Read` and
  `Edit` on the paths, plus a `PreToolUse` guard hook. Anything already in the file is kept.
- **`.mcp.json`**: a `datanyx` MCP server limited to those paths (`--no-mcp` skips it).
- **`.datanyx/protect.json`**: the protected paths, which the hook reads.

Running it again replaces its own entries rather than adding more, and a settings file that
isn't valid JSON is left alone. Paths outside the project are covered by the hook only, since
the deny rules are relative to the project.

**What the guard refuses.** Reading, searching or listing a protected path with Read, Grep or
Glob. Shell commands that name one: `cat data/x.csv`, `ls data`, `cd data && ...`, `cat data/*.csv`,
`python -c "open('data/x.csv')"`, and absolute, Windows-style or Git Bash paths. It reads shell syntax,
so `cat<data/x.csv`, `da"ta"/x.csv`, `$(cat data/x.csv)`, `(cat data/x.csv)`, `{data,src}/x.csv`,
`a & cat data/x.csv`, `dd if=data/x.csv` and `bash -c '...'` count too. Git commands that would print the
data: `git show HEAD:data/x.csv` and `git grep`, and `git diff`, `git show` or `git log -p` when git holds
the data, unless narrowed to other paths. Recursive searches from a folder that has protected data inside
it (`grep -r foo .`, `rg foo`, `find . -exec ...`), and a Glob pattern that would list a protected file
(`**/*.csv`, though `**/*.py` is fine if no such file is there); pass `--allow-broad-search` to allow
those. Changes to the protected data and to the files that set the protection up, including deleting or
moving their folders (`rm -rf .datanyx`), writing to them through a redirection, and reverting them with
git (`git checkout -- .`, `git clean`, `git reset --hard`). Every refusal says what to run instead.

**What it lets through**, because they work with the data without showing it: `datanyx schema`,
`synth`, `check`, `scrub`, `anonymize`, `run`, `scan`, `diff`, `audit` and `config`. `run` is refused if it saves raw output to a
file or if the command it starts names the data, and `anonymize --mapping`, `restore`, `pseudonymize`, `eval`, `init` and
`protect` are refused. So are the options that loosen what you set up, since each shows something
hidden or works as a way to test guesses: `--keep-name`, `--keep`, `--keep-entity`, `--allow`,
`--keep-numbers`, `--no-ner`, `--show-paths`, `--include-examples`, `--reveal`, `--allow-urls`,
`--config` and `--no-config`. The agent can't edit `datanyx.toml` or `datanyx.local.toml`, and can't
read the local one, which lists the terms you hide. Running a script that reads the data, like
`datanyx run -- python report.py`, is the intended way to work.

The hook is the command `datanyx hook`. Claude Code runs it before each tool call and reads its verdict; you don't run it yourself, and `datanyx protect --write` is the way to install it.

**It fails closed.** The hook is written with `onFailure: "block"`, so if it can't run, tool calls
are blocked instead of slipping through. `--write` runs the hook once and tells you if the
command doesn't start. A bad command would block everything, so fix it before using Claude
Code in that folder. The hook uses the program-and-arguments form of a hook command, which
needs a recent Claude Code.

**What it isn't.** A guardrail, not a sandbox. It can't see inside a script (one that prints rows
is not caught), follow a path built at run time such as `$DATA/x.csv`, or know what a pipe feeds to
another program. `find . -name '*.csv'` isn't refused either, so file names can still be listed that
way. It governs Claude Code's tools only: other agents, a terminal, or someone
who edits the settings themselves aren't stopped. For hard isolation, use a separate account or
container with no access to the data, or [a database login that can't read rows](security.md#a-login-that-cant-read-rows).

## As an MCP server

The server speaks over stdio to one local agent. To host it for a team over HTTP with a bearer token, see [Installing and deploying](deployment.md#a-server-a-team-shares).

```sh
datanyx mcp --root ./exports        # stdio; needs the `mcp` extra
```

Add it to your agent's MCP configuration:

```json
{
  "mcpServers": {
    "datanyx": {
      "command": "datanyx",
      "args": ["mcp", "--root", "./exports"]
    }
  }
}
```

The agent gets six tools: `describe_data` (a schema for a file, folder or `env:NAME`
database), `synthetic_sample` (up to 200 fake rows that match it), `check_code` (checks its
SQL or pandas code against that schema), `scan_folder` (which files hold personal data or
secrets, by kind and count), `schema_diff` (what changed between two schemas) and
`anonymize_file` (a text file with placeholders).
It's limited by design:

- It can only read under `--root` (default: the current folder), and database URLs are
  refused unless you start the server with `--allow-urls`. Use `env:NAME` instead. A file is under
  the same rule however it is named: an `env:NAME` that holds a path or a `sqlite:///` URL, or a
  `sqlite:///` URL allowed by `--allow-urls`, must still point under `--root`.
- There's no `keep` or `hide` option, because either one tests whether a name exists. Choose
  `--keep-entity`, and the `hide` setting, when you start the server instead.
- `scan_folder` uses the custom detectors, packs and `[scan]` settings the server was started with.
- There's no restore tool and no mapping, since both hold the real values.
- Errors are fixed messages, never the library's own text.

**An audit log.** `--audit-log FILE` (or `audit_log` under `[mcp]`) appends one line per tool call:

```sh
datanyx mcp --root ./data --audit-log audit.jsonl
datanyx audit audit.jsonl --since 2025-01-31         # who asked about what, and what was refused
```

```console
$ datanyx audit audit.jsonl
42 calls in 3 sessions, 2025-01-31T09:12:03+00:00 to 2025-02-02T16:40:51+00:00

By tool: describe_data 20 · check_code 15 · scan_folder 5 · synthetic_sample 2

Paths asked about:
  data/customers.csv  ×27
  ../secrets  ×2

2 calls failed or were refused:
  describe_data: that path is outside the folders this server may read  ×2
```

Each record has the time, a session id, the tool, the paths and settings it was called with, whether it
worked, and sizes. It never has a value or anything the agent was given back: the SQL an agent asked to
have checked is recorded as a length, and a password or token in a database URL is replaced. The file is
readable by its owner only, and the server won't start if it can't make it so. Keep it where the agent can't change it.

The server doesn't stop the agent from opening the files itself. Block that in its
permissions, as above.
