ARG BASE_IMAGE
FROM ${BASE_IMAGE}

COPY wheelhouse/python_dotenv-1.2.1-py3-none-any.whl /wheelhouse/
RUN python3 -m venv /opt/config-venv \
 && /opt/config-venv/bin/pip install --no-index --no-deps /wheelhouse/python_dotenv-1.2.1-py3-none-any.whl \
 && rm -rf /wheelhouse

COPY --chown=65532:65532 snapshot/ /workspace/
RUN test ! -e /workspace/.env

ENV PATH=/opt/config-venv/bin:/usr/local/bin:/usr/bin:/bin
ENV PYTHONPATH=/workspace
WORKDIR /workspace
USER 65532:65532
