ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE} AS build
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/runtime
WORKDIR /build
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple uv==0.12.0
COPY . .
RUN uv sync --frozen --package semibrain-sandbox-runtime --no-dev --no-editable
FROM ${PYTHON_IMAGE}
COPY --from=build /opt/runtime /opt/runtime
RUN groupadd -g 10001 sandbox && useradd -u 10001 -g 10001 -M sandbox
USER 10001:10001
WORKDIR /workspace
ENV PATH="/opt/runtime/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-I", "-c", "import time; time.sleep(3600)"]
