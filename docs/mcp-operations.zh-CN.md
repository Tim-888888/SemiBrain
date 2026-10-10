# MCP 服务接入与调用

[简体中文](mcp-operations.zh-CN.md) · [English](mcp-operations.md) · [返回中文首页](../README.md)

business-service 管理 MCP 配置、版本历史、发现句柄与持久化工具任务。网关代理管理员操作，并提供当前身份和 Agent 角色声明。单 Agent 和多 Agent 共用 `mcp.discover` 与 `mcp.call`，按角色和本次任务范围筛选可用服务。

## 在管理控制台接入远程服务

可以填写 HTTPS URL，也可以导入 `mcpServers` JSON。

| 项目 | 支持范围 |
| --- | --- |
| 传输方式 | Streamable HTTP、SSE |
| 认证方式 | Bearer、API key、自定义请求头 |
| 暂不支持 | OAuth、stdio |
| 凭据位置 | 使用请求头，不接受 URL 查询参数中的凭据 |

由后端连接 MCP 服务，浏览器不直接连接。每次受管理的请求都会执行公网 DNS 检查，并将连接固定到已验证的 IP 地址。SSE 消息端点必须保持同源。已批准的内部服务继续通过下文的部署注册表配置。

### 部署加密密钥

在 business API 和所有 business worker 的环境中配置持久化的 `SEMIBRAIN_MCP_ENCRYPTION_KEY`，其值为生成的 Fernet 密钥，并与 MongoDB 分开备份。

`mcp_connections` 保存不可变的加密 URL 与凭据。管理接口只返回请求头名称，不返回请求头值。同源编辑时，凭据留空会保留原值；更换源地址或认证类型时必须重新填写。替换连接后需要重新同步工具目录并重新授权工具。已有的环境变量引用仍然有效。

### 启用流程

1. 添加服务并配置连接与认证信息。
2. 执行“保存并连接 / 同步工具”。
3. 选择允许调用的工具，并填写使用说明。
4. 启用服务，将其纳入管理员配置的可用范围。

刷新目录不会自动授权新发现的工具。使用说明属于不可信的目录元数据，不能覆盖系统规则或授权边界。

## 在智能调查中使用

输入区的 MCP 选择器控制本次任务的服务范围：

| `mcp_service_ids` | 含义 |
| --- | --- |
| `null` | 继承管理员策略 |
| `[]` | 本次任务禁用 MCP |
| 服务 ID 列表 | 将本次任务限制在指定服务范围内 |

选择结果保存在输入快照中，通过可信授权传递给单 Agent 和多 Agent。限定为所选文档的任务仍然禁用外部 MCP。运行详情展示当前用户拥有的调用记录；服务被撤销后，其保留的结果内容也会隐藏。

## 通过部署配置管理内部端点

在 **business 服务及 worker** 环境中配置 `SEMIBRAIN_MCP_ENDPOINTS_JSON`。它是以端点引用为键、由部署管理的 JSON 对象。每个端点包含 `url`、可选的 `secret_ref`，以及 `data_origin`（`public`、`authorized_business` 或 `synthetic`）。

凭据从指定的 `SEMIBRAIN_MCP_*_TOKEN` 环境变量读取，不得放入 URL、提交到仓库的配置或前端资产。经批准的内部端点如需使用明文 HTTP，必须显式设置 `allow_private: true`。

不支持任意模型提供的 URL、stdio、远程 shell、客户端 roots、sampling 或 elicitation。

可选的 `infra/compose/extensions.yaml` 会启动 `mcp-measurement`：一个无状态的长度单位换算示例，不挂载数据、密钥，也不发布宿主机端口。注册内部 URL `http://mcp-measurement:8000/mcp` 时，设置 `allow_private: true` 和 `data_origin: authorized_business`。它用于演示真实 MCP 传输，不是生产连接器，也不提供工艺规范。

管理控制台操作顺序为：添加引用该端点的服务 → 保存 → 刷新远程目录 → 选择精确工具名称及允许的账号/Agent 角色 → 启用并保存。

| 管理员范围策略 | 行为 |
| --- | --- |
| `none` | 默认策略，不开放服务 |
| `all` | 仅包含已启用且获得授权的服务 |
| `selected` | 在已有授权基础上，进一步与明确选中的服务取交集 |

仅允许所选文档的任务不会使用外部 MCP 数据。

## 渐进式发现与权限检查

列表阶段只暴露描述信息。`describe` 才返回完整参数结构，以及绑定调用主体、运行、角色、服务、配置和 schema 的不透明句柄。

调用路径先在本地验证参数，并在执行前再次检查实时远程 schema。不同服务中的同名工具保持独立。schema 变化后，需要刷新目录并重新发现工具。

服务禁用或角色撤权会阻止新调用及结果交付，也会影响保留结果的访问授权。新增权限只对后续运行生效，已受理运行的原始范围不能扩大。执行句柄七天后过期，经过验证的结果来源记录随持久化任务保留。

MCP 元数据与工具结果始终作为不可信内容处理，不能成为系统消息，也不能自行授权资产或构造可信来源链路。超大、不支持或格式错误的结果会明确报错，不会静默丢失文本；资源链接和图片不会自动下载。

## 执行、失败与回滚

每次调用使用已有的持久化任务和关联标识。整个远程交互受超时、响应体大小与取消检查约束。worker 丢失时，不会自动重试结果未知的外部调用。取消只停止本地请求，无法保证撤销远程服务器已经产生的副作用，因此管理员应在启用工具前检查其行为。

配置集合包括 `mcp_settings`、`mcp_config_history` 和 `mcp_catalogs`；临时 `mcp_tool_refs`、`mcp_run_policies` 设有 TTL 索引。不新增跨服务数据库访问或破坏性迁移。

回滚时禁用策略并恢复此前的应用镜像/配置，已经受理的调用保留审计记录。实现使用由 `uv.lock` 锁定的官方 Python MCP SDK，渐进式发现遵循 SemiBrain 的服务和授权边界。

---

[开发与部署](development.zh-CN.md) · [Agent 配置与评测](agent-configuration.zh-CN.md) · [Skills](skills-operations.zh-CN.md)
