export interface ConversationStarter {
  id: string
  title: string
  description: string
  icon: string
  tags: string[]
  mode: 'quick_qa' | 'investigation'
  multiAgent: boolean
  allowWeb: boolean
  prompt: string
}

// Curated task inputs, not cached answers. All execution still uses ordinary
// authorization, retrieval, tool selection and artifact delivery.
export const conversationStarters: ConversationStarter[] = [
  {
    id: 'cp-ft', title: '看懂 CP 与 FT', icon: 'knowledge',
    description: '比较测试阶段与良率口径，逐条查看资料来源。',
    tags: ['快速问答', '知识库'], mode: 'quick_qa', multiAgent: false, allowWeb: false,
    prompt: '根据已发布的知识库和工程 Wiki，比较 CP 晶圆测试与 FT 成品测试的测试对象、设备接口、先后关系及良率统计口径。请用简明对比表说明，引用资料来源，不把 CP 结果当作 FT 结果。',
  },
  {
    id: 'web', title: '联网核对工艺知识', icon: 'search',
    description: '结合专业资料和公开网页，核对工艺概念。',
    tags: ['单 Agent', '联网搜索'], mode: 'investigation', multiAgent: false, allowWeb: true,
    prompt: '结合知识库并实际检索公开网络资料，简要介绍 ICP 与 CCP 刻蚀的区别。比较等离子体产生方式、控制特点和适用边界，优先查阅设备厂商或研究机构资料，标注可核验的来源链接。',
  },
  {
    id: 'graph', title: '沿图谱追溯关系', icon: 'graph',
    description: '从测试工序找到关联设备，追溯关系原文。',
    tags: ['单 Agent', '工程图谱'], mode: 'investigation', multiAgent: false, allowWeb: false,
    prompt: '请先查询工程图谱，说明 CP 晶圆测试与探针台、探针卡、ATE、FT 成品测试的关系，给出支持这些关系的原文和引用。区分先后关系、设备分工与一般相关性；若图谱缺少某条关系，请说明，不要编造。',
  },
  {
    id: 'yield', title: '调查良率异常', icon: 'operations',
    description: '协作核对测试、工艺和告警，提出候选根因。',
    tags: ['多 Agent', '合成演示数据'], mode: 'investigation', multiAgent: true, allowWeb: false,
    prompt: '请协作调查合成演示批次 DEMO-B-Lot-0001 的良率异常。范围：产品 DEMO-B-P-B，工艺设备 DEMO-B-EQ-B / C2，时间为北京时间 2026-09-01 00:00 至 2026-09-02 00:00（不含），数据截至 2026-09-02 00:00。\n1. 查询该批次的上下文、CP 阶段 CP-v2 程序下同一队列的首测与最终良率（列分子分母），以及上述设备腔体的相关工艺历史和 FDC 告警。\n2. 检索知识库，结合数据给出异常现象、候选根因、支持证据、缺失信息和下一步验证建议。\n请输出调查报告，正文时间统一转换为北京时间（UTC+8）并标明。只调查上述范围，不把 CP 与 FT 混为一谈；缺少对照数据时不能声称已证明良率下降，不把相关性当作已证实因果。明确标注合成演示数据。',
  },
  {
    id: 'sandbox', title: '用 Python 算一算', icon: 'evaluations',
    description: '在沙箱计算良率，下载图表和结果 CSV。',
    tags: ['多 Agent', '沙箱 · 图表'], mode: 'investigation', multiAgent: true, allowWeb: false,
    prompt: '请在 Python 沙箱中分析以下独立合成示例数据，无需查询业务表或联网。各行均为 FT 阶段，单位为器件，同批首测与最终判定使用同一队列、程序和条件；最终通过数已包含复测后通过的器件。计算各批首测良率、最终良率和改善百分点，绘制对比柱状图，提供 PNG 图表和 CSV 结果下载，并简要说明统计口径。图表可用英文标注，正文用中文，注明合成示例，不据此判断真实产线根因。\n\n批次,器件总数,首测通过数,最终通过数\nDEMO-A,100,90,96\nDEMO-B,120,102,114\nDEMO-C,80,72,76\nDEMO-D,150,135,144\nDEMO-E,100,92,97',
  },
  {
    id: 'illustrated-note', title: '生成图文工程笔记', icon: 'wiki',
    description: '引用知识库原图，交付带配图的 Markdown 笔记。',
    tags: ['单 Agent', '图文 · 文件下载'], mode: 'investigation', multiAgent: false, allowWeb: false,
    prompt: '请根据知识库整理一份半导体刻蚀图文笔记，介绍湿法与干法、各向同性与各向异性、掩膜与轮廓控制。请查阅带配图的原文，在相关段落之间插入至少一张知识库原图，附图注和来源；找不到合适配图时如实说明，不虚构图片。最后把这份图文笔记生成 Markdown 文件供下载，保留配图。',
  },
]
