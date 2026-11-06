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
# The base image and every dependency are pinned: dependencies by the hashes in uv.lock.
FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS build
WORKDIR /src
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir uv==0.10.7 \
    && uv build --wheel --out-dir /wheels \
    && uv export --frozen --no-default-groups --no-emit-project --extra mcp --extra yaml --extra excel --extra parquet --extra orc --extra avro --extra duckdb --extra stats --extra mongodb --extra streaming --extra check --extra pdf --extra text --extra zstd --extra remote --extra s3 -o /wheels/requirements.txt

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
ARG MODEL=""
RUN useradd --create-home --uid 10001 datanyx
COPY --from=build /wheels /wheels
RUN pip install --no-cache-dir --require-hashes -r /wheels/requirements.txt \
    && pip install --no-cache-dir --no-deps /wheels/*.whl \
    && if [ -n "$MODEL" ]; then python -m spacy download "$MODEL"; fi \
    && rm -rf /wheels
USER datanyx
WORKDIR /data
ENTRYPOINT ["datanyx"]
CMD ["--help"]
