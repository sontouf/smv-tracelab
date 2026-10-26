# SMV TraceLab — NuSMV + web UI in one image (linux/amd64).
#   docker run --rm -p 8780:8780 -v "$PWD:/models" ghcr.io/sontouf/smv-tracelab
FROM python:3.12-slim

ARG NUSMV_VERSION=2.7.1
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates xz-utils libxml2 libedit2 \
 && curl -fsSL "https://nusmv.fbk.eu/distrib/${NUSMV_VERSION}/NuSMV-${NUSMV_VERSION}-linux64.tar.xz" \
    | tar -xJ -C /opt "NuSMV-${NUSMV_VERSION}-linux64/bin" \
 && ln -s "/opt/NuSMV-${NUSMV_VERSION}-linux64/bin/NuSMV" /usr/local/bin/NuSMV \
 && ln -s "$(ls /usr/lib/*-linux-gnu/libedit.so.2)" "$(dirname "$(ls /usr/lib/*-linux-gnu/libedit.so.2)")/libedit.so.0" \
 && NuSMV -h 2>&1 | head -1 \
 && apt-get purge -y curl xz-utils && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY . /src
RUN pip install --no-cache-dir /src && mkdir -p /models && cp -r /src/examples /models/examples && rm -rf /src

WORKDIR /models
EXPOSE 8780
ENTRYPOINT ["smv-tracelab", "--host", "0.0.0.0", "--no-browser", "--no-download", "--root", "/models"]
