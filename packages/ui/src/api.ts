export type User = { id: string; username: string; role: 'admin' | 'user'; revision: number; enabled: boolean; demo: boolean }
export type Citation = { marker: string; title: string; evidence_id: string; asset_id?: string; job_id?: string; location?: Record<string, unknown> }
export type Run = { run_id: string; status: string; sequence: number; body_markdown: string; citations: Citation[]; progress?: string; error?: string }
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
