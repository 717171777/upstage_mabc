FROM node:22-bookworm-slim AS build
WORKDIR /build
COPY package*.json next.config.ts tsconfig.json next-env.d.ts postcss.config.mjs ./
RUN npm ci
COPY src ./src
COPY public ./public
RUN npm run build

FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv git ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -u 10001 -m app
ENV HOME=/home/app

WORKDIR /app

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY deploy/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

RUN git init /opt/hermes-agent \
    && git -C /opt/hermes-agent remote add origin https://github.com/NousResearch/hermes-agent.git \
    && git -C /opt/hermes-agent fetch --depth 1 origin 337ef8f8ce4106231b72f6ee7a83d5094dab0059 \
    && git -C /opt/hermes-agent checkout --detach FETCH_HEAD \
    && pip install --no-cache-dir -e /opt/hermes-agent

ENV HERMES_SOURCE=/opt/hermes-agent
ENV HERMES_PYTHON=/opt/venv/bin/python
ENV GARIMI_DATA_DIR=/data/jobs
ENV GARIMI_BACKEND_URL=http://127.0.0.1:8000
ENV GARIMI_REQUIRE_UPSTAGE=1
ENV GARIMI_ALLOW_USER_DOCUMENTS=1
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

COPY --from=build /build/.next /app/.next
COPY --from=build /build/node_modules /app/node_modules
COPY --from=build /build/package*.json /app/
COPY --from=build /build/public /app/public
COPY --from=build /build/next.config.ts /app/

COPY backend ./backend
COPY reference/schemas ./reference/schemas
COPY fixtures/eval_v0 ./fixtures/eval_v0
COPY deploy/start.py /app/start.py

RUN mkdir -p /data/jobs && chown -R app:app /data /app && chmod -R o+r /opt/hermes-agent

EXPOSE 3000

ENV PORT=3000

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=3 \
  CMD python3 -c "import urllib.request,sys;d=urllib.request.urlopen('http://127.0.0.1:'+sys.argv[1]+'/api/service/health',timeout=5);sys.exit(0 if d.status==200 else 1)" "$PORT"

USER app
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python3", "/app/start.py"]
