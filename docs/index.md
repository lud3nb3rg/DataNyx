# DataNyx

**Let an AI agent work with your data without ever seeing it.**

DataNyx turns files and databases into value-free schemas an agent can write code against, and
replaces names and identifiers in free text with placeholders. Start with [Getting started](getting-started.md),
or pick the job you have:

| I want to… | Read | Command |
|---|---|---|
| Show an agent the shape of a file or database | [Schemas](schemas.md) | `datanyx schema` |
| Let it try code on realistic fake rows | [Synthetic data](synthetic-data.md) | `datanyx synth` |
| Let it fix a script that fails on the real data | [Debugging](debugging.md) | `datanyx run` |
| Catch wrong column names before anything runs | [Checking code](checking-code.md) | `datanyx check` |
| Find personal data or secrets in a folder before sharing it | [Scanning](scanning.md) | `datanyx scan` |
| Give it text with the names and ids removed | [Text anonymization](anonymization.md) | `datanyx anonymize` |
| Share a copy of a table with consistent fake values | [Pseudonymizing tables](pseudonymization.md) | `datanyx pseudonymize` |
| Measure and improve what gets detected | [Detection quality](detection.md) | `datanyx eval` |
| Notice that a data feed changed shape | [Schema diff](schema-diff.md) | `datanyx diff` |
| Stop an agent from reading the data directly | [Using it with an agent](agents.md) | `datanyx protect` |
| Run it in a container, a pre-commit hook, CI or a shared server | [Installing and deploying](deployment.md) | `datanyx mcp --http` |
| Share one policy across a team | [Team settings](settings.md) | `datanyx.toml` |

## Where things are

- **[Features](features.md)**: what it does, in one page.
- **[Supported sources](sources.md)**: file formats and databases, and the extra to install for each.
- **[Privacy model](privacy-model.md)**: what is guaranteed, what is only best effort, and the known limits.
- **[Command reference](cli-reference.md)**: every command and option, generated from the code.
- **[Python API](python-api.md)**: using DataNyx from your own code.
- **[Contributing](contributing.md)**: the architecture, how to add a source, and how to run the tests.
