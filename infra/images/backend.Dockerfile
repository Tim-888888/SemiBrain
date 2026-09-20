ARG PYTHON_IMAGE
FROM ${PYTHON_IMAGE}
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
RUN pip install --no-cache-dir --index-url https://pypi.tuna.tsinghua.edu.cn/simple uv==0.12.0
COPY pyproject.toml uv.lock ./
COPY packages/common/pyproject.toml packages/common/pyproject.toml
COPY packages/contracts/pyproject.toml packages/contracts/pyproject.toml
COPY services/conversation-service/pyproject.toml services/conversation-service/pyproject.toml
COPY services/agent-service/pyproject.toml services/agent-service/pyproject.toml
COPY services/business-service/pyproject.toml services/business-service/pyproject.toml
COPY vendor/weknora-docreader/pyproject.toml vendor/weknora-docreader/pyproject.toml
RUN uv sync --frozen --all-packages --no-install-workspace
COPY . /app
RUN uv sync --frozen --all-packages --no-editable
RUN groupadd -g 10001 semibrain && useradd -u 10001 -g 10001 -d /app -M semibrain
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
CMD ["uvicorn", "semibrain_conversation.main:app", "--host", "0.0.0.0", "--port", "8000"]
