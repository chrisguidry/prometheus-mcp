FROM python:3.14-slim

# git is a build dependency: hatch-vcs reads the version from the repository
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get install -y --no-install-recommends tini git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY . .
RUN uv sync --locked --no-dev

# The final image never installs packages. pip vendors old copies of urllib3,
# msgpack, and setuptools that scanners report.
RUN pip uninstall -y pip

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 9000

ENTRYPOINT ["tini", "--"]
CMD ["python", "-c", "from prometheus_mcp.server import mcp; mcp.run(transport='http', host='0.0.0.0', port=9000)"]
