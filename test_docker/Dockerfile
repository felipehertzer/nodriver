FROM fedora:41

# ── System dependencies ──────────────────────────────────────────────
RUN dnf install -y \
        chromium \
        python3 \
        python3-pip \
        python3-devel \
        gcc \
        gcc-c++ \
        libcurl-devel \
        openssl-devel \
        libxml2-devel \
        libxslt-devel \
        procps-ng \
        iproute \
        lsof \
        && dnf clean all

# Find and verify the chromium binary
RUN CHROMIUM_BIN=$(rpm -ql chromium | grep -E 'bin/chromium' | head -1) \
        && echo "Found chromium at: ${CHROMIUM_BIN}" \
        && test -x "${CHROMIUM_BIN}" \
        && "${CHROMIUM_BIN}" --version \
        && echo "${CHROMIUM_BIN}" > /tmp/chromium_path

# ── Set the browser path for nodriver ─────────────────────────────────
ENV CHROME_EXECUTABLE_PATH=/usr/lib64/chromium-browser/chromium-browser
ENV HELIUM_EXECUTABLE_PATH=/usr/lib64/chromium-browser/chromium-browser

# ── Copy nodriver source and install it ──────────────────────────────
COPY nodriver /opt/nodriver/nodriver
COPY pyproject.toml /opt/nodriver/pyproject.toml
COPY README.md /opt/nodriver/README.md
COPY LICENSE.txt /opt/nodriver/LICENSE.txt
WORKDIR /opt/nodriver
RUN pip3 install --break-system-packages -e .

# ── Install additional test dependencies ─────────────────────────────
RUN pip3 install --break-system-packages \
        trafilatura \
        curl_cffi \
        ua-generator \
        lxml \
        beautifulsoup4 \
        psutil

# ── Copy test files ──────────────────────────────────────────────────
COPY test_docker/ /opt/test/
WORKDIR /opt/test

# ── Default: run the leak test ───────────────────────────────────────
CMD ["python3", "leak_test.py"]

