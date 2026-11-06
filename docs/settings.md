# Team settings

Repeating flags on every command doesn't scale, and a policy that lives in someone's shell history
can't be reviewed. Put it in `datanyx.toml` at the root of the project and commit it:

```sh
datanyx init            # a starter file with every setting commented out
datanyx init --local    # datanyx.local.toml, for what must stay private (added to .gitignore)
datanyx config          # the settings in effect, and the files they came from
```

```toml
# datanyx.toml
[schema]
map_paths = ["$.users"]                  # hide every key under these paths
keep_names = ["Order Details"]           # show names the rules hid
scale_hints = true                       # row counts as an order of magnitude
infer_keys = true                        # yes/no guesses about unique columns and joins

[anonymize]
model = "en_core_web_lg"                 # or models = ["en_core_web_lg", "fr_core_news_md"]
keep_entities = ["DATE"]
detectors = ["detectors/internal.toml"]  # custom detectors (below)
packs = ["healthcare"]                   # detector packs for a field
backends = ["presidio"]                  # another detector, run as well

[scrub]
allow = ["plan"]                         # quoted names to leave readable in errors
names_from = ["data/customers.csv"]      # ...and the names this input's schema shows
ner = false                              # skip the name model

[scan]
exclude = ["fixtures", "*.lock"]
kinds = ["EMAIL", "SECRET"]
max_size = 5

[mcp]
roots = ["data"]
audit_log = "datanyx-audit.jsonl"
allow_env = ["DATABASE_URL"]             # environment variables the agent may use as env:NAME
```

| Section | Used by |
|---|---|
| `[schema]` | `schema`, `synth`, `check`, `diff`, and `scrub --names-from` |
| `[anonymize]` | `anonymize`, `scrub` (the `hide` terms), `scan`, `eval`, `pseudonymize`'s free text, and the MCP server's `anonymize_file` |
| `[scrub]` | `scrub` and `run` |
| `[scan]` | `scan` |
| `[mcp]` | `datanyx mcp` |

- **Where it's found.** In the current folder or the nearest parent, like git. `--config FILE` or the
  `DATANYX_CONFIG` variable names one instead, and `--no-config` ignores it.
- **Command line wins.** Lists from the command line are added to the file's, and single values such as
  `--model` or `--max-size` override it.
- **Mistakes are errors.** An unknown section or setting is reported with a suggestion
  (`unknown setting 'hid' in [anonymize]; did you mean 'hide'?`), because a misspelled `hide` would
  otherwise protect nothing. A value is never repeated in an error message.
- **Paths** in the file (`detectors`, `names_from`, `roots`, `audit_log`) are relative to the file.
- **Terms to hide stay private.** `datanyx.local.toml` is merged on top of `datanyx.toml`: lists are
  joined and single values in the local file win. Put the client and company names you are protecting
  there, and keep it out of git. `datanyx config` counts them and shows them only with `--reveal`.
  [`datanyx protect`](agents.md#enforcing-it-with-datanyx-protect) stops an agent reading that file or editing
  either one.
