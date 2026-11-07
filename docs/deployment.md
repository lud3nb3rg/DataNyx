# Installing and deploying

Ways to run DataNyx beyond a local clone: from PyPI, in a container, as a pre-commit hook, in GitHub
Actions, and as a server a team shares.

## From PyPI

Releases are published to [PyPI](https://pypi.org/project/datanyx/) from a version tag:

```sh
uv tool install datanyx                # CSV, JSON, XML and SQLite
uv tool install "datanyx[all]"         # every reader, driver and the MCP server
```

To use it as a library in a project, `uv add datanyx`. The [sources table](sources.md) lists the extra for each format.

## In a container

The `Dockerfile` builds an image with the common readers, the MCP server and the text tools. The image
runs as a non-root user and starts in `/data`, so mount what you want to read there, read-only:

```sh
docker build -t datanyx .
docker run --rm -v "$PWD:/data:ro" datanyx scan /data
docker run --rm -v "$PWD:/data:ro" datanyx schema /data/customers.csv --format text
```

The spaCy models are large and left out. To add one, build with `--build-arg MODEL=en_core_web_sm`
and pass `--model en_core_web_sm`. Tagged releases are also pushed to `ghcr.io`. The base image is pinned
by digest and the dependencies by the hashes in `uv.lock`, so a build installs exactly what was tested.

## Checking a release

Every release is built by [a workflow in this repository](https://github.com/lud3nb3rg/DataNyx/blob/main/.github/workflows/release.yml),
and what it publishes can be checked against that workflow, so you don't have to take the files on trust. Each check
needs a tool you may not have: [`pypi-attestations`](https://pypi.org/project/pypi-attestations/), the
[GitHub CLI](https://cli.github.com/) and [cosign](https://docs.sigstore.dev/cosign/).

| What | Check |
|---|---|
| The wheel and sdist on PyPI came from this repository | `pypi-attestations verify pypi --repository https://github.com/lud3nb3rg/DataNyx pypi:datanyx-X.Y.Z-py3-none-any.whl` |
| A file you downloaded was built by the release workflow | `gh attestation verify datanyx-X.Y.Z-py3-none-any.whl --repo lud3nb3rg/DataNyx` |
| The container image was built and signed by it | `gh attestation verify oci://ghcr.io/lud3nb3rg/datanyx:X.Y.Z --repo lud3nb3rg/DataNyx` |
| The same, with cosign | `cosign verify ghcr.io/lud3nb3rg/datanyx:X.Y.Z --certificate-oidc-issuer https://token.actions.githubusercontent.com --certificate-identity-regexp '^https://github.com/lud3nb3rg/DataNyx/\.github/workflows/release\.yml@refs/tags/v'` |

Two lists of what is inside come with a release. The GitHub Release has `datanyx-X.Y.Z.cdx.json`, a
[CycloneDX](https://cyclonedx.org/) list of the package's locked dependencies. The image carries its own SBOM and its
build provenance in the registry: `docker buildx imagetools inspect ghcr.io/lud3nb3rg/datanyx:X.Y.Z --format '{{ json .SBOM }}'`.
Release 0.1.0 predates the GitHub attestations, the signature and the SBOM, so only its PyPI attestation can be checked. Later releases are built with all of them.

## As a pre-commit hook

A commit is blocked when a staged file holds personal data or secrets. The hook reports where (file,
kind, count and line) and never the value. Add this to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/lud3nb3rg/DataNyx   # this repository
    rev: v0.2.0
    hooks:
      - id: datanyx-scan
```

Settings come from `datanyx.toml`, so the hook uses the same exclusions and custom detectors as
`datanyx scan`. Pass options with `args: [--kinds, SECRET]`.

## In GitHub Actions

The repository is also a GitHub Action. It scans for personal data and secrets, or compares two
schemas, and fails the job without printing a value:

```yaml
jobs:
  datanyx:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v5
      - uses: lud3nb3rg/DataNyx@v0.2.0           # this repository
        with:
          command: scan
          path: .
          args: --exclude tests
```

For a schema check on pull requests, save the schema you expect and compare against it:

```yaml
      - uses: lud3nb3rg/DataNyx@v0.2.0
        with:
          command: diff
          path: schema.json data/export.csv     # the earlier schema, then the later one
```

`diff` fails only for changes that could break code written for the old schema. The inputs reach the
command through environment variables, not the script text, so a pull request can't inject commands
through them.

The Action ignores `datanyx.toml` unless you set `use-config: "true"`, because a pull request can change
that file: `exclude = ["*"]` would otherwise turn the scan off. Put exclusions in `args`, in the workflow.
That workflow is part of the repository too, so protect `.github/` with a code owner or a branch rule
if pull requests come from people you don't trust.

`scan` also fails when a file that may hold data couldn't be read, because it was too large or is binary
of an unknown kind. Exclude it, raise `--max-size`, or pass `--allow-unscanned`.

## A server a team shares

By default the [MCP server](agents.md#as-an-mcp-server) speaks over stdio to one local agent. To host
one for a team, serve it over HTTP:

```sh
export DATANYX_MCP_TOKEN="$(openssl rand -hex 24)"
datanyx mcp --http 127.0.0.1:8808 --root /srv/data --audit-log /var/log/datanyx-mcp.jsonl
```

The endpoint is `http://HOST:8808/mcp`, and every request must carry `Authorization: Bearer TOKEN`.
A request without it gets a bare 401, and the token is compared in constant time.

- The token comes from the environment (`--token-env NAME` picks another variable) and must be at
  least 16 characters. It is never a command-line option, so it stays out of process listings.
- The server offers the same tools as over stdio, so it still returns only schemas and anonymized text,
  and only for files under `--root`.
- **Put TLS in front of it** (a reverse proxy such as Caddy or nginx) before it leaves a trusted
  network: the bearer token is sent with every request.
- One token is shared by everyone who uses the server. The audit log records what was asked, never
  a value or who asked.

## Publishing a release

Maintainers: set the version in `pyproject.toml`, then tag it:

```sh
git tag v0.2.0 && git push --tags
```

The release workflow checks that the tag matches the version, builds the package, checks its
metadata, publishes to PyPI through trusted publishing (no API token is stored), and pushes the
container image. One-time setup: on pypi.org add a pending publisher for this repository with the workflow
`release.yml` and the environment `pypi`, and create that environment in the repository settings.
