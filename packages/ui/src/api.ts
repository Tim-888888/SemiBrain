export type User = { id: string; username: string; role: 'admin' | 'user'; revision: number; enabled: boolean; demo: boolean }
export type Citation = { body_expired?: boolean; marker: string; title: string; evidence_id: string; asset_id?: string; job_id?: string; location?: Record<string, unknown> }
export type DocumentImage = { asset_id: string; url: string; display_url?: string; alt: string; media_type?: string }
export type Artifact = { name: string; asset_id: string; media_type?: string }
export type TaskNode = { task_id: string; role: string; key: string; goals: string[]; depends_on: string[]; plan_version: number; status: string; attempt: number; role_round?: number; error?: string; model_calls?: number; tool_calls?: number; settled_tokens?: number }
export type Run = { image_refs?: DocumentImage[]; artifacts?: Artifact[]; task_tree?: TaskNode[]; run_id: string; status: string; sequence: number; body_markdown: string; citations: Citation[]; progress?: string; error?: string; web_disabled?: boolean; web_activity?: { search: string; reason: string; results?: number; error?: string; pages: { status: string; error?: string }[] }; continuation?: { from_run_id: string; kind: string }; input_scope?: { investigation_strategy?: "single_agent" | "multi_agent"; mode: "quick_qa" | "investigation"; allow_web: boolean; resource_restrictions: string[]; attachment_refs: string[] }; strategy?: string; model_origin?: string; round?: number; active_tool?: string; scope_summary?: { goals: string[]; constraints: string[]; missing: string[]; allow_web: boolean }; budget?: { model_calls: number; tools: number; searches?: number; pages?: number; settled_tokens: number; unreconciled_calls: number } }
export type Message = Partial<Run> & { id: string; role: string; text?: string; run_id: string; input_revision: number }
let csrf = ''
export function setCsrf(value: string) { csrf = value }
const labels: Record<string, string> = {
  LOGIN_FAILED: '账号或密码不正确，或账号已被停用。', CAPTCHA_INVALID: '验证码不正确，请刷新后重试。',
  CAPTCHA_EXPIRED: '验证码已使用或已过期，请刷新后重试。', USERNAME_UNAVAILABLE: '这个用户名已被使用或属于保留账号。',
  RATE_LIMITED: '操作较频繁，请稍后重试。', LOGIN_REQUIRED: '请先登录。', SESSION_REVOKED: '登录已失效，请重新登录。',
  ADMIN_REQUIRED: '此账号没有管理权限。', CONVERSATION_REVISION_CONFLICT: '会话已在其他页面更新，请重新打开会话后提交。',
  UPSTREAM_DENIED: '资源已失效或暂时无法访问。', UPSTREAM_FAILED: '服务暂时不可用，请稍后重试。',
  REVISION_CONFLICT: '资源已更新，请刷新后重试。', CSRF_DENIED: '会话校验失效，请重新登录。',
  PASSWORD_LENGTH: '密码需要 8–128 位。', PASSWORD_MISMATCH: '原密码不正确。',
  MULTI_AGENT_UNAVAILABLE: '多 Agent 功能当前未开放，请关闭协作开关后提交。', CONVERSATION_RUN_ACTIVE: '当前会话仍在执行，请先停止本轮再切换。', IMAGE_SIZE_INVALID: '图片大小不符合要求，请重新选择（上限 3 MB）。', IMAGE_FORMAT_INVALID: '请选择 PNG、JPEG 或 WebP 图片（不超过 1600 万像素）。',
  WEB_SNAPSHOT_EXPIRED: '原网页快照已过期；可查看来源链接，当前网页内容可能已变化。',
  DOCUMENT_IMAGES_INVALID: '配图格式、大小或路径不正确，请检查后重新上传。',
  REPUBLISH_DATA_INCOMPLETE: '原发布版本的正文、配图或检索索引不完整，暂不能重新上架。请重新上传解析，或联系管理员修复。',
  REPUBLISH_CHECK_UNAVAILABLE: '暂时无法校验原版本，请稍后重试。文档仍保持下架。',
  REPUBLISH_NEW_VERSION_PENDING: '此文档有新版本正在处理或等待发布，请先处理新版本。',
  NO_PUBLISHED_VERSION: '此文档没有已发布过的版本，请先完成解析并确认发布。',
  DOCUMENT_ALREADY_PUBLISHED: '文档已经上架，请刷新查看。',
  IDEMPOTENCY_CONFLICT: '操作请求已发生变化，请刷新后重试。',
  INVALID_ARGUMENT: '填写内容不符合要求，请检查后重试。',
}
export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  const headers = new Headers(options.headers)
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', csrf)
  const response = await fetch(path, { ...options, headers, credentials: 'same-origin' })
  const data = await response.json().catch(() => ({}))
  if (!response.ok) {
    const code = data.detail?.code || data.error?.code || 'REQUEST_FAILED'
    if (code === 'INVALID_ARGUMENT' && path.startsWith('/v1/auth/')) {
      const hints: Record<string, string> = {
        'body.username': '用户名需为 4–64 位，可用英文字母、数字、下划线、点、@、加号或短横线，支持邮箱形式。',
        'body.password': path.endsWith('/register') ? '密码需要 8–128 位。' : '请填写密码，最多 128 位。',
        'body.new_password': '新密码需要 8–128 位。',
        'body.answer': '请填写图片中的 5 位验证码。',
        'body.challenge_id': '验证码已失效，请刷新后重试。',
      }
      const fields = Array.isArray(data.error?.fields) ? data.error.fields : []
      const messages = fields.map((field: string) => hints[field]).filter(Boolean)
      if (messages.length) throw new Error([...new Set(messages)].join(' '))
    }
    throw new Error(labels[code] || `操作未完成（${code}）`)
  }
  return data as T
}
export const post = <T = any>(path: string, value: object, requestId?: string) => api<T>(path, {
  method: 'POST', body: JSON.stringify(value), headers: requestId ? { 'Idempotency-Key': requestId } : {},
})
