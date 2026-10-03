export const adminGroups = [
  { label: '知识管理', items: [['knowledge', '知识库'], ['wiki', '工程 Wiki'], ['graph', '工程图谱']] },
  { label: 'Agent 能力', items: [['agent-config', 'Agent 配置'], ['skills', 'Skills 技能'], ['mcp', 'MCP 服务']] },
  { label: '运行与管理', items: [['evaluations', '评测与用量'], ['comparison', '运行对照'], ['operations', '运行治理'], ['users', '用户管理']] },
  { label: '个人', items: [['memory', '我的记忆']] },
]
export const adminPages = adminGroups.flatMap(group => group.items.map(([id, title]) => ({ id, title, group: group.label })))
