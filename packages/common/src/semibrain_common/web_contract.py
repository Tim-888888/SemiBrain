"""Shared web tool contract; content expansion is owned by the Agent executor."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WebSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=3, max_length=240,
                       description="仅公开知识关键词，不携带内部编号、人员、凭据或资料原文。")
    provider: Literal["auto", "bocha", "zhipu"] = Field(default="auto",
        description="auto优先博查，失败或无有效网址时至多回退一次智谱search_pro；结果不相关时可明确选zhipu互补检索。")
    content: bool = Field(default=False,
        description="true时并行读取最多3个候选网页，返回独立正文证据与逐页状态；部分失败保留成功页。摘要不是正文。")
