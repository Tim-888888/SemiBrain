export type ConnectionDraft = {
  url: string; transport: 'streamable_http' | 'sse'; auth_type: 'none' | 'bearer' | 'api_key' | 'headers';
  token: string; header_name: string; headers: {name: string; value: string}[];
  data_origin: 'public' | 'authorized_business' | 'synthetic';
}
export function emptyConnection(): ConnectionDraft {
  return {url:'', transport:'streamable_http', auth_type:'none', token:'', header_name:'X-API-Key', headers:[], data_origin:'public'}
}
export function parseMcpImport(text: string): {label:string; connection:ConnectionDraft}[] {
  let raw: any
  try {raw=JSON.parse(text)} catch {throw new Error('JSON 格式不正确，请粘贴完整配置。')}
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error('配置必须是 JSON 对象。')
  const entries = raw.mcpServers ? Object.entries(raw.mcpServers) : [['远程 MCP',raw]]
  if (!entries.length || entries.length > 30) throw new Error('请提供 1 至 30 个 MCP 服务配置。')
  return entries.map(([label,value])=>{
    const cfg=value as any
    if (!cfg || typeof cfg!=='object' || Array.isArray(cfg)) throw new Error('服务配置必须是对象。')
    if (cfg.command || cfg.args || cfg.env) throw new Error('本阶段支持远程 HTTP / SSE，不支持 command、args 或本地进程。')
    if (cfg.oauth || cfg.auth?.type==='oauth' || cfg.auth_config?.auth_type==='oauth2') throw new Error('OAuth 服务暂不支持，请选择 API Key 或 Token 接入。')
    let url:URL
    try {url=new URL(cfg.url)} catch {throw new Error('配置中缺少有效的服务 URL。')}
    if (url.protocol!=='https:' || url.username || url.password || url.search || url.hash) throw new Error('远程连接请使用无查询参数的 HTTPS URL，认证信息填写到请求头。内网服务请选择已登记连接。')
    const type=String(cfg.type??cfg.transport??'').toLowerCase()
    if (type && !['http','streamable-http','streamable_http','http-streamable','sse'].includes(type)) throw new Error('不支持该传输方式，请选择 HTTP 或 SSE。')
    const connection=emptyConnection()
    connection.url=cfg.url.trim();connection.transport=type==='sse' || (!type && /\/sse\/?$/i.test(url.pathname))?'sse':'streamable_http'
    if(cfg.headers){
      if(typeof cfg.headers!=='object'||Array.isArray(cfg.headers)) throw new Error('headers 必须是键值对象。')
      connection.headers=Object.entries(cfg.headers).map(([name,value])=>{
        if(typeof value!=='string') throw new Error('请求头值必须是字符串。')
        return {name,value}
      })
      if(connection.headers.length)connection.auth_type='headers'
    }
    return {label:String(label),connection}
  })
}
export function connectionHeaders(value:ConnectionDraft):Record<string,string>|null {
  if(value.auth_type==='none') return {}
  if(value.auth_type==='bearer')return value.token?{Authorization:`Bearer ${value.token}`} : null
  if(value.auth_type==='api_key')return value.token?{[value.header_name]:value.token}:null
  if(!value.headers.length)return null
  if(value.headers.some(row=>!row.name.trim() || !row.value))throw new Error('请完整填写请求头名称和值。')
  if(new Set(value.headers.map(row=>row.name.trim().toLowerCase())).size!==value.headers.length)throw new Error('请求头名称不能重复。')
  return Object.fromEntries(value.headers.map(row=>[row.name.trim(),row.value]))
}
export function mcpError(code:string):string {
  const messages:Record<string,string>={MCP_AUTH_FAILED:'认证失败，请检查 API Key 或 Token。',MCP_TIMEOUT:'连接超时，请检查服务状态或稍后重试。',MCP_CONNECTION_FAILED:'无法连接服务，请检查地址、协议和服务器网络。',MCP_URL_INVALID:'请使用无查询参数的 HTTPS 地址，认证信息填写到请求头。',WEB_ADDRESS_DENIED:'该地址指向内网或保留地址。内网服务请使用部署环境登记的连接。',WEB_DNS_FAILED:'域名解析失败，请检查服务地址。',MCP_HEADERS_INVALID:'请求头名称或值不合法，不能覆盖协议与连接控制头。',MCP_ENCRYPTION_NOT_CONFIGURED:'服务器尚未配置 MCP 凭据加密密钥。',MCP_CREDENTIAL_REQUIRED:'请填写认证凭据。',MCP_CREDENTIAL_REENTER_REQUIRED:'认证方式或服务域名已改变，请重新填写凭据。',MCP_CATALOG_REFRESH_REQUIRED:'连接或工具目录已改变，请重新连接并同步工具。',MCP_SCHEMA_CHANGED_REDISCOVER:'工具参数已变化，请同步目录后重试。',MCP_CREDENTIAL_UNAVAILABLE:'凭据无法解密，请联系管理员检查加密配置。',MCP_REDIRECT_DENIED:'服务返回了不允许的跳转地址，请使用最终 MCP 地址。'}
  return messages[code] || code
}
