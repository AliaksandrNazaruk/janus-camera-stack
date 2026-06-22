# syntax=docker/dockerfile:1
#
# Janus Gateway — built from source (multi-arch: works on amd64 AND arm64).
# There is no official multi-arch Janus image, and the community ones are
# amd64-only, so we build it. Used by docker-compose.demo.yml (and usable by
# docker-compose.prod.yml via JANUS_IMAGE).
#
#   docker build -t janus-from-source:latest deploy/janus
#
# Includes the WebSockets transport (browser signalling) + usrsctp (DataChannel,
# needed for the textroom back-channel) + the streaming & textroom plugins.

ARG JANUS_VERSION=v1.2.4

# ── builder ────────────────────────────────────────────────────────────
FROM debian:bookworm-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates build-essential automake libtool pkg-config gtk-doc-tools \
    cmake gengetopt m4 \
    libmicrohttpd-dev libjansson-dev libssl-dev libsofia-sip-ua-dev libglib2.0-dev \
    libopus-dev libogg-dev libcurl4-openssl-dev liblua5.3-dev libconfig-dev \
    libnice-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

# libsrtp 2.x (Debian ships 2.x dev too, but build from source pinned for parity)
RUN git clone --depth 1 -b v2.5.0 https://github.com/cisco/libsrtp.git && \
    cd libsrtp && ./configure --prefix=/usr --enable-openssl && \
    make shared_library -j"$(nproc)" && make install

# usrsctp (DataChannels / textroom)
RUN git clone --depth 1 https://github.com/sctplab/usrsctp.git && \
    cd usrsctp && ./bootstrap && ./configure --prefix=/usr --disable-programs --disable-inet --disable-inet6 && \
    make -j"$(nproc)" && make install

# libwebsockets (WS transport)
RUN git clone --depth 1 -b v4.3-stable https://github.com/warmcat/libwebsockets.git && \
    cd libwebsockets && mkdir build && cd build && \
    cmake -DLWS_MAX_SMP=1 -DLWS_WITHOUT_EXTENSIONS=0 -DCMAKE_INSTALL_PREFIX:PATH=/usr -DCMAKE_C_FLAGS="-fpic" .. && \
    make -j"$(nproc)" && make install

# Janus itself
ARG JANUS_VERSION
RUN git clone --depth 1 -b ${JANUS_VERSION} https://github.com/meetecho/janus-gateway.git && \
    cd janus-gateway && sh autogen.sh && \
    ./configure --prefix=/usr/local \
        --disable-rabbitmq --disable-mqtt --disable-unix-sockets \
        --disable-all-handlers && \
    make -j"$(nproc)" && make install && make configs

# ── runtime ────────────────────────────────────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
    libmicrohttpd12 libjansson4 libssl3 libsofia-sip-ua0 libglib2.0-0 \
    libopus0 libogg0 libcurl4 liblua5.3-0 libconfig9 libnice10 \
    && rm -rf /var/lib/apt/lists/*

# Janus + the libs we built into /usr
COPY --from=builder /usr/local /usr/local
COPY --from=builder /usr/lib/libsrtp2* /usr/lib/
COPY --from=builder /usr/lib/libusrsctp* /usr/lib/
COPY --from=builder /usr/lib/libwebsockets* /usr/lib/
RUN ldconfig

EXPOSE 8088 8188 7088
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD curl -fsS http://127.0.0.1:8088/janus/info || exit 1

CMD ["/usr/local/bin/janus"]
