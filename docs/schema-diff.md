# Watching a data feed for changes

Code written against a data feed breaks when the feed changes shape. Save its schema once, then
compare against it whenever a new export arrives:

```sh
datanyx schema feed.csv -o schema.snapshot.json     # once; commit the snapshot
datanyx diff schema.snapshot.json feed.csv          # on every new export, in CI
```

```console
$ datanyx diff schema.snapshot.json feed.csv
2 breaking, 1 compatible

breaking:
  ~ $[].email: string<email> -> string<email> | null  (can now be null)
  - $[].notes: removed

compatible:
  + $[].score: added  (number, required)
```

Either side can be a saved schema or any input DataNyx reads: another file, a folder, a database.

| Change | Counted as |
|---|---|
| A field or table removed | **breaking** |
| A type widened: a number may now be text, a field may now be null, an integer may now have decimals | **breaking** |
| A format lost, or changed (`email` to none, `date` to `date-time`) | **breaking** |
| A field that was always there is now optional | **breaking** |
| A field or table added | compatible |
| A type narrowed, a format gained, an optional field now always present | compatible |
| A database type changed, a primary or foreign key changed, key names newly hidden | info |

`--fail-on breaking` (the default) exits with 1 when anything breaking changed, `--fail-on any` for any
difference and `--fail-on never` to just report. `--format json` is for scripts, and the options for
reading inputs (`--source`, `--schema`, `-r`, `--map-path`, `--keep-name`) work as in `datanyx schema`.
Nested changes have paths such as `$.user.address.zip` or `users[].tags[]`. The MCP server has a
`schema_diff` tool.

A schema built from a file is built from what the file contains, so a difference can come from the
data in a new export, not only from its design: a column that was never empty can show up as nullable.
