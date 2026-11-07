# Isolated agent setup

A Docker Compose setup in which the agent has no access to your data at all. `datanyx protect` is a
guardrail for one agent's tools; this is the arrangement that holds even if the agent writes a script that
prints every row, because the script runs in a container where the rows aren't.

```
 your data ──(read-only)──> datanyx container ──MCP over HTTP──> agent container ──> model API
                            schemas, fake rows,                  no data mounted
                            anonymized text only                 writes code in ./workspace
```

Setup is in [`compose.yaml`](compose.yaml); the walkthrough and what it does and doesn't cover are in
[Hard isolation with containers](../../docs/agents.md#hard-isolation-with-containers).
