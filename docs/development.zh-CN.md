# 开发与部署指南

[简体中文](development.zh-CN.md) · [English](development.md) · [返回中文首页](../README.md)

本文介绍 SemiBrain 的开发环境、服务启动、存储部署、文档解析、上下文压缩与恢复边界。产品能力和使用场景见 [项目首页](../README.md)。

## 仓库结构与服务职责

| 目录 | 职责 |
| --- | --- |
| `apps/user-web` | 基于 Vue 的用户工作台 |
| `apps/admin-web` | 基于 Vue 的管理控制台 |
| `services/conversation-service` | 身份认证、会话管理与流式展示 |
| `services/agent-service` | 意图路由、Agent 执行、证据组织与 Markdown 回答 |
| `services/business-service` | 受控业务查询、检索、文档入库、资产与工具 |
| `packages/contracts` | 带版本的 API、事件与工具协议 |
| `packages/common` | 共享的传输与配置工具 |
| `packages/ui` | 共享界面组件 |
| `infra` | 部署定义与固定版本的镜像配置 |

每个服务独立管理自己的数据库，通过经过认证的 API 访问其他服务。最终回答使用普通 Markdown；工具和事件的类型约束不会强制用户回答采用固定的报告结构。

### 模型与执行边界

对话理解、调查、审核和 RCA 默认共用可配置的 `deepseek-flash`，也可按角色覆盖模型。DeepSeek 使用非思考模式，因为其 Responses 接口不提供加密推理回放；原生工具调用和应用侧 Agent 循环仍然启用。系统不存储或展示明文推理。对于已验证支持加密回放的提供方，可以显式开启该能力。

模型迁移后，模型配置不匹配的运行中检查点会被拒绝恢复；历史最终回答仍可阅读，后续追问会创建新运行。视觉、嵌入、重排与公开网络搜索各自使用独立配置的提供方。

共享 Markdown 渲染器通过 KaTeX 支持括号形式和美元符号形式的数学公式。渲染时禁用需要信任权限的 TeX 命令，限制宏与表达式大小，并清理生成的 HTML/MathML。复制操作保留含公式在内的原始 Markdown。

运行具有模型调用、工具调用、任务委派次数限制，以及截止时间、取消与受版本和租约约束的检查点恢复机制。默认不设置累计 200,000 Token 的硬上限，实际 Token 用量与未知用量仍可查看。单次请求的上下文容量和输出预留，与整个任务的累计用量分别管理。

接近时间或调用次数上限时，系统可使用已有的授权证据进行不再调用工具的收尾；租约丢失或任务取消不会被包装成成功的兜底回答。运维时应查看任务受理时冻结的运行限制，不要假设所有执行模式使用相同边界。

## 本地开发

使用 Python 3.12.13、uv 0.12.0、Node 24.19.0 和 pnpm 11.19.0。依赖解析结果保存在 `uv.lock` 与 `pnpm-lock.yaml`。

在仓库根目录执行：

```shell
uv sync --frozen --all-packages
pnpm install --frozen-lockfile
uv run --all-packages pytest -q
uv run --all-packages ruff check packages services tests scripts
node --test packages/ui/tests/*.test.mjs
pnpm build
uv run --all-packages python scripts/verify/export_contracts.py
uv run --all-packages python scripts/verify/engineering.py
```

在不同终端中分别启动服务与前端：

```shell
uv run --all-packages uvicorn semibrain_conversation.main:app --port 8101
uv run --all-packages uvicorn semibrain_agent.main:app --port 8102
uv run --all-packages uvicorn semibrain_business.main:app --port 8103
pnpm --filter @semibrain/user-web dev
pnpm --filter @semibrain/admin-web dev --port 5174
```

管理控制台使用 `/admin/` 路径。完整业务流程还需要下文 B 阶段的存储、各服务环境配置、worker 与反向代理；仅启动上述 API 进程，只能提供进程健康检查与 API 定义。

每个服务提供 `/healthz` 和当前的 `/openapi.json`，生成的传输协议文件位于 `packages/contracts/schemas`。`Report` 封装 Markdown 正文及引用绑定，不规定业务报告字段或固定章节。

## 私有 Linux 基础环境

构建 `infra/images/backend.Dockerfile` 和 `web.Dockerfile` 时，使用 `infra/images/base-images.lock.json` 中对应的不可变镜像引用。该文件记录原始镜像仓库的 manifest 与已验证的导入镜像摘要；使用导入引用时，镜像必须已存在于本机。

新主机应按原始 manifest 摘要从 Docker Hub 拉取镜像，或导入已验证的镜像归档，再将对应引用传入构建参数。

```shell
docker build -f infra/images/backend.Dockerfile --build-arg PYTHON_IMAGE=<verified-python-reference> -t semibrain/backend:a-stage .
docker build -f infra/images/web.Dockerfile --build-arg NODE_IMAGE=<verified-node-reference> --build-arg NGINX_IMAGE=<verified-nginx-reference> -t semibrain/web:a-stage .
docker compose --env-file /private/foundation-compose.env -f infra/compose/foundation.yaml up -d --wait
```

私有 Compose 环境需配置 `BACKEND_IMAGE`、`WEB_IMAGE`、`POSTGRES_IMAGE`、`FOUNDATION_DATA_DIR`、`FOUNDATION_PG_PASSWORD_FILE` 和 `FOUNDATION_BUSINESS_ENV`。应用镜像按摘要或本地不可变镜像 ID 固定。

密码文件仅包含 PostgreSQL 所有者密码。business 环境中使用**只读**的 `SEMIBRAIN_WAREHOUSE_READ_URL`，以及随机生成的 `SEMIBRAIN_FOUNDATION_TOKEN`。运行中的业务 API 不应复用数据库所有者凭据；需单独创建只读角色并仅授予 SELECT 权限。SQLAlchemy 数仓元数据负责创建初始表。

Web 网关仅绑定本机回环地址的 8080 端口，并屏蔽 `/internal/`。A 阶段健康页面不是经过用户认证的公网产品。基础环境的私有查询接口即使在 Docker 网络内，也要求携带 Bearer 凭据；进入 B 阶段后，由身份与资源授权适配层替代。

## 私有 B 阶段平台

组合使用 `infra/compose/storage.yaml` 和 `application.yaml`。先构建并固定后端与 Web 镜像，在 `SECRET_DIR` 下配置私有 `conversation.env`、`agent.env`、`business.env`，以及离线初始化专用的 `bootstrap.env`。在线服务不应获得初始化凭据。配置名称见 `.env.example`，存储镜像锁定文件为 `infra/images/platform-images.lock.json`。

先初始化 Mongo 副本集与认证 keyfile，再通过 `ops` profile 运行 `scripts/bootstrap/platform.py`。它负责创建各服务独立的 Mongo 用户、合成 PostgreSQL 数仓及只读角色，以及限定权限的存储用户。

Redis 启动时应配置服务独立的 ACL：

- 各服务分别拥有 `celery_<service>:*`。
- 仅 conversation 拥有 `auth:*` 与 `delegation:*`。
- conversation 写入 `stream:runs`、读取 `stream:agent`；agent 的权限方向相反。
- business 拥有 `stream:business`。
- 禁用 Redis 默认用户和 Celery 远程控制。

```shell
docker compose --env-file /private/compose.env -f infra/compose/storage.yaml -f infra/compose/application.yaml up -d
```

Web 入口仅监听 `127.0.0.1:8081`。私有开发时，将其转发到 `SEMIBRAIN_PUBLIC_ORIGIN` 中精确配置的本机回环源地址。API 不暴露宿主机端口。各服务使用独立 Mongo 凭据，只有 business 能访问隔离的 PostgreSQL 网络。

worker 使用持久化 Mongo 记录、租约、Outbox/Inbox 投递与终态快照，Redis 仅作为传输通道。

### 演示账号与 HTTPS

仅在 `SEMIBRAIN_DEMO_MODE=true` 时，才能显式调用 `semibrain_conversation.auth.seed_demo` 初始化演示账号。首次运行会创建 `admin/admin` 和 `user/user`，已有账号记录保持不变。注册密码长度为 8–128 个字符，使用 Argon2id 哈希；验证码绑定浏览器与用途，且只能尝试一次。演示密码仅适用于隔离的演示部署。

`infra/compose/domain.yaml` 可在本机回环地址的 8443 端口增加 TLS 验证入口。`TLS_DIR` 中应包含 `fullchain.pem` 和 `semibrain.key`，证书账户密钥保留在 Web 容器之外。

公网启用前，应满足主机所在地的域名备案要求，将浏览器源地址精确设置为正式 HTTPS 域名，检查 Secure Cookie 和代理的客户端 IP 处理，并建立证书续期机制。仓库不会自动创建续期账户或 DNS 凭据。

### Redis 丢失后的恢复

先停止消费者，重放各生产者的持久化 Outbox，通过 `reset_consumer` 重置对应的持久化消费游标，再重启 worker。Inbox 去重记录必须保留。

协议升级后重放事件前，应先检查隔离记录；不得静默删除失败事件或将失败任务标记为成功。

## 数据与解析验证

`scripts/seed_mock/generate.py --seed <integer> --prefix <namespace> --output <private-path>` 用于生成可复现的合成数据。配合私有 `SEMIBRAIN_WAREHOUSE_ADMIN_URL` 使用 `--load`，可导入可丢弃的基础测试数据库。同一数据集重复导入时会校验哈希并保持幂等；不同随机种子应使用不同命名空间。

查询 API 以明确的 CP/FT、程序版本、样本群体时间窗口与入库截止时间，计算首测或终测良率。样本群体为空时，比例返回 null。

`tests/integration/test_postgres.py` 需要私有的所有者/只读连接 URL、`SEMIBRAIN_FOUNDATION_BASE_URL`、`SEMIBRAIN_FOUNDATION_TOKEN`，以及离线 `SEMIBRAIN_ORACLE_MODULE` 的路径。该测试会修改并恢复一条合成记录，以验证 HTTP 接口确实读取已提交的 SQL 数据。不得将其指向生产数据。

### 文档入库

支持 PDF、Word、图片、Excel、XMind、PowerPoint、EPUB、Markdown、CSV、JSON、HTML 和 MHTML。固定版本的解析适配器处理 Python 兼容性、来源位置、图片与格式相关的回退逻辑。

MinerU 和视觉/OCR 需要明确授权向外部发送原文。旧版 Office 转换与 PDF 报告渲染使用固定版本的 Docker 运行环境，不使用宿主机 shell。

系统保留原始资产和不可变的解析版本。需要人工检查的警告（包括旧版 XLS 仅保留单元格值的转换）、不支持/加密/损坏的变体以及取消状态都会明确呈现。上传成功不等于资料已经发布并参与检索。

### 检查点与离线评测

`scripts/verify/checkpoint_probe.py` 使用私有 `SEMIBRAIN_CHECKPOINT_PROBE_URI` 验证受支持的 MongoDBSaver API。即使存在更新的孤立检查点，也必须明确选择一个已经提交的检查点。该脚本不实现持久化租约或恢复流程。

封存的评测生成器、标准答案判定逻辑（oracle）与评测器均离线运行。留出集应生成在仓库外；验收时仅向运行服务提供批准的输入与合成源表。不得把 oracle 文件、预期答案或评测结论挂载到运行中的服务。评测封装可以包含测量元数据，回答正文始终保持自由 Markdown 格式。

## 历史上下文压缩

新调查运行会冻结 `SEMIBRAIN_CONTEXT_COMPACTION_ENABLED`（默认 true）以及可选的 `SEMIBRAIN_COMPACTION_POLICY_JSON` 运维配置。不含此快照的旧运行继续使用原有上下文路径。

接近路由模型的上下文窗口阈值时，系统会总结较早且已闭合的工具交互，同时保留当前目标与近期原始消息。该机制不会重新引入多 Agent 累计 Token 上限，也不会重置模型、工具或时间限制。摘要请求记为 `context.compact`，且不能占用专门预留的最终调用额度。

| 配置项 | 默认值 | 含义 |
| --- | --- | --- |
| `threshold_ratio` | `0.8` | 上下文压缩触发阈值比例 |
| `retain_ratio` | `0.16` | 近期原始历史的保留比例 |
| `headroom_tokens` | `65536` | 上下文容量预留 |
| `summary_tokens` | `8192` | 摘要 Token 预算 |

较小上下文窗口的路由需显式配置：使用 `default` 默认设置，并在 `models` 下按精确模型名称设置覆盖项。无效的容量配置会被拒绝。

来源正文仍保存在已有证据/快照存储中，可通过经授权的 `evidence.read` 和 `web.read` 句柄回读。摘要属于历史数据，不能独立作为事实引用。原生历史日志与 `context_compactions` 纳入现有终态运行副本的清理机制；已发布报告和正式来源的保留策略不变。

Mongo 迁移只创建索引。已提交的摘要指针按 run/task/role 受并发版本约束；失败或结果未知的请求不会覆盖历史，也不会自动重复提交同一来源片段。可按需运行 `scripts/verify/context_compaction.py`，在隔离的真实 Mongo 环境中用合成数据验证，不调用模型提供方。

## 管理与运维文档

- [Agent 配置与评测](agent-configuration.zh-CN.md)
- [MCP 服务](mcp-operations.zh-CN.md)
- [Skills 审核与执行](skills-operations.zh-CN.md)
- [长期记忆](memory-operations.zh-CN.md)
- [Markdown、Word 与 PDF 报告导出](report-export.zh-CN.md)
- [手动隔离恢复演练](recovery-rehearsal.zh-CN.md)

MongoDB 是权威数据源，Milvus 和 Neo4j 是可重建的投影，MinIO 保存原始资产与生成资产。E 阶段迁移新增索引与集合，不破坏性转换已有文档。

配置经审核后需显式发布；新运行固定配置版本，历史回答保留原有来源链路。应用回滚恢复先前固定的镜像和配置，不应删除新数据，也不应使用演练数据覆盖在线存储。冷备份工具不提供定时每日备份、异机保护或自动灾难切换。
