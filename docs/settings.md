# Team settings

Repeating flags on every command doesn't scale, and a policy that lives in someone's shell history
can't be reviewed. Put it in `datanyx.toml` at the root of the project and commit it:

```sh
datanyx init            # a starter file of the common settings, commented out
datanyx init --local    # datanyx.local.toml, for what must stay private (added to .gitignore)
datanyx config          # the settings in effect, and the files they came from
```

Every setting there is:

```toml
# datanyx.toml
[schema]
map_paths = ["$.users"]                  # hide every key under these paths
keep_names = ["Order Details"]           # show names the rules hid
public_columns = ["country", "status"]   # columns whose labels may be listed (the one setting that shows values)
scale_hints = true                       # row counts as an order of magnitude
infer_keys = true                        # yes/no guesses about unique columns and joins
column_hints = true                      # ranges for distinct values, missing values and text length
views = true                             # describe database views too
max_rows = 100000                        # read only this many rows of a data file

[anonymize]
model = "en_core_web_lg"                 # or models = ["en_core_web_lg", "fr_core_news_md"]
hide = ["Acme"]                          # always hide these terms (better in datanyx.local.toml)
keep = ["Northwind Support"]             # leave these exact terms visible
keep_entities = ["DATE"]                 # leave these kinds visible
detectors = ["detectors/internal.toml"]  # custom detectors (see Detection quality)
packs = ["healthcare"]                   # detector packs for a field
backends = ["presidio"]                  # another detector, run as well

[scrub]
allow = ["plan"]                         # quoted names to leave readable in errors
names_from = ["data/customers.csv"]      # ...and the names this input's schema shows
keep_numbers = false                     # true: don't replace long numbers
ner = false                              # skip the name model

[scan]
exclude = ["fixtures", "*.lock"]
kinds = ["EMAIL", "SECRET"]
max_size = 5                             # MB
include_examples = false                 # true: also count example.com emails and documentation IPs
ner = false                              # true: also find names, organisations and places

[mcp]
roots = ["data"]
allow_urls = false                       # true: let the agent name database and file URLs
allow_env = ["DATABASE_URL"]             # environment variables the agent may use as env:NAME
audit_log = "datanyx-audit.jsonl"
debug_log = "datanyx-debug.jsonl"        # where unexpected failures happened, for you
```

Custom detectors can also be written straight into the file as `[[pattern]]` and `[[dictionary]]` entries
([custom detectors](detection.md#custom-detectors)).

| Section | Read by |
|---|---|
| `[schema]` | Everything that describes data: `schema`, `synth`, `check`, `diff`, `scrub` and `run` (for `--names-from` and `--describe-output`), and `datanyx mcp` for every tool |
| `[anonymize]` | `anonymize`, `eval` and `pseudonymize`'s free text. The model, detectors, packs and backends are also used by `scrub`, `run` and `scan`, and `hide` by `scrub` and `run`. `datanyx mcp` uses all but `keep` |
| `[scrub]` | `scrub` and `run` |
| `[scan]` | `scan`. The MCP server's `scan_folder` uses `exclude`, `max_size` and `include_examples` |
| `[mcp]` | `datanyx mcp` |

- **Where it's found.** In the current folder or the nearest parent, like git. `--config FILE` or the
  `DATANYX_CONFIG` variable names one instead, and `--no-config` ignores it.
- **Command line wins.** Lists from the command line are added to the file's, and single values such as
  `--model` or `--max-size` override it.
- **Mistakes are errors.** An unknown section or setting is reported with a suggestion
  (`unknown setting 'hid' in [anonymize]; did you mean 'hide'?`), because a misspelled `hide` would
  otherwise protect nothing. A value is never repeated in an error message.
- **Paths** in the file (`detectors`, `names_from`, `roots`, `audit_log`, `debug_log`) are relative to the file.
- **Terms to hide stay private.** `datanyx.local.toml` is merged on top of `datanyx.toml`: lists are
  joined and single values in the local file win. Put the client and company names you are protecting
  there, and keep it out of git. `datanyx config` counts them and shows them only with `--reveal`.
  [`datanyx protect`](agents.md#enforcing-it-with-datanyx-protect) stops an agent reading that file or editing
  either one.
