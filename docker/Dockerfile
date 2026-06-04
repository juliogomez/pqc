# Base image pinned by digest for reproducible builds (ubuntu:24.04 as of this writing)
FROM ubuntu:24.04@sha256:786a8b558f7be160c6c8c4a54f9a57274f3b4fb1491cf65146521ae77ff1dc54

RUN apt-get update && apt-get install -y \
    build-essential git cmake ninja-build libssl-dev pkg-config \
    libsystemd-dev iproute2 iputils-ping kmod tcpdump vim \
    autoconf automake libtool gettext gperf bison flex \
    && rm -rf /var/lib/apt/lists/*

# Build strongSwan with the native ML-KEM plugin (--enable-ml)
# - Pinned to the 6.0.6 release tag for reproducibility; the self-contained `ml`
#   plugin (FIPS 203) ships in stable 6.x releases, so liboqs is not needed.
# - --enable-kernel-pfroute is BSD/macOS only; Linux uses kernel-netlink (default)
RUN git clone --branch 6.0.6 --depth 1 https://github.com/strongswan/strongswan.git /tmp/strongswan \
    && cd /tmp/strongswan \
    && ./autogen.sh \
    && ./configure \
        --enable-openssl \
        --enable-kernel-netlink \
        --enable-ml \
        --enable-swanctl \
        --enable-vici \
    && make -j$(nproc) \
    && make install \
    && ldconfig \
    && rm -rf /tmp/strongswan

# Disable modular plugin loading so all compiled-in plugins are loaded by default.
# (The generated per-plugin .conf files are empty, so load_modular=yes results in
# none of them being loaded, causing charon to fail on missing critical features.)
RUN sed -i 's/load_modular = yes/load_modular = no/' /usr/local/etc/strongswan.conf

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Healthy once charon is up and answering on the VICI socket
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=3 \
    CMD swanctl --stats >/dev/null 2>&1 || exit 1

ENTRYPOINT ["/entrypoint.sh"]
