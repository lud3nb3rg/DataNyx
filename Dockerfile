# DataNyx in a container: the CLI, and the MCP server over HTTP.
#
#   docker build -t datanyx .
#   docker run --rm -v "$PWD:/data:ro" datanyx scan /data
#   docker run --rm -v "$PWD:/data:ro" datanyx schema /data/customers.csv --format text
#
# A shared MCP server (the token is read from the environment, never from the command line):
#   docker run -d -p 8808:8808 -e DATANYX_MCP_TOKEN -v "$PWD/data:/data:ro" datanyx \
#       mcp --http 0.0.0.0:8808 --root /data
#
# The text models are large, so they are left out; build with --build-arg MODEL=en_core_web_sm to add one.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /wheels

FROM python:3.12-slim
ARG MODEL=""
RUN useradd --create-home --uid 10001 datanyx
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir "$(ls /wheels/*.whl)[mcp,yaml,excel,parquet,orc,avro,duckdb,stats,mongodb,streaming,check,pdf,text]" \
    && if [ -n "$MODEL" ]; then python -m spacy download "$MODEL"; fi \
    && rm -rf /wheels
USER datanyx
WORKDIR /data
ENTRYPOINT ["datanyx"]
CMD ["--help"]
