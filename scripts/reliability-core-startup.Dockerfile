ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN groupadd --gid 65532 matrx && useradd --uid 65532 --gid 65532 --home-dir /run/matrx-test/home --no-create-home --shell /usr/sbin/nologin matrx
COPY wheelhouse/uv-0.10.8-py3-none-manylinux_2_28_aarch64.whl /wheelhouse/
RUN python3 -m pip install --no-index --no-deps /wheelhouse/uv-0.10.8-py3-none-manylinux_2_28_aarch64.whl && rm -rf /wheelhouse
WORKDIR /workspace
COPY --chown=65532:65532 snapshot/ /workspace/
RUN test ! -e /workspace/.env
RUN UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never uv sync --frozen --no-dev --no-install-project --python /usr/local/bin/python3 \
 && target="$(readlink -f /opt/venv/bin/python)" \
 && test -x "$target" \
 && case "$target" in /root/*) exit 1;; esac \
 && runuser -u matrx -- /opt/venv/bin/python -I -c "import sys; print(sys.version)" \
 && rm -rf /root/.cache/uv
ENV PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin
ENV PYTHONPATH=/workspace
WORKDIR /workspace
USER matrx:matrx
