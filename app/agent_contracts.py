"""Typed messages exchanged between independently scoped agents."""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class AnalysisPlan(Contract):
    dimension: Literal["channel", "product"]
    channel: str | None = Field(default=None, max_length=80)
    product: str | None = Field(default=None, max_length=80)
    metric: Literal["refund_rate"]


class Decision(Contract):
    action: Literal["tool", "final"]
    tool: str | None = None
    arguments: dict = Field(default_factory=dict)
    output: dict = Field(default_factory=dict)


class Delegation(Contract):
    supported: bool
    analysis_goal: str = Field(min_length=2, max_length=500)
    knowledge_goal: str = Field(min_length=2, max_length=500)
    report_goal: str = Field(min_length=2, max_length=500)
    knowledge_required: bool


class SelectedAnalysis(Contract):
    selected_call_id: str = Field(min_length=1, max_length=80)


class SearchRules(Contract):
    query: str = Field(min_length=2, max_length=300)
    limit: int = Field(default=5, ge=1, le=8)


class RuleCitation(Contract):
    id: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=2, max_length=1200)


class SelectedEvidence(Contract):
    citations: list[RuleCitation] = Field(default_factory=list, max_length=8)


class ReportOutline(Contract):
    focus_groups: list[str] = Field(default_factory=list, max_length=5)
    citations: list[RuleCitation] = Field(default_factory=list, max_length=8)
    recommendations: list[
        Literal[
            "verify_refund_lag",
            "inspect_channel_mix",
            "review_product_changes",
            "check_small_samples",
            "compare_support_cases",
        ]
    ] = Field(min_length=1, max_length=5)


RECOMMENDATIONS = {
    "verify_refund_lag": "核对退款入账滞后，确认两个时间窗口的观察周期一致。",
    "inspect_channel_mix": "检查渠道流量与客户构成变化，避免将结构变化误判为因果关系。",
    "review_product_changes": "核查产品、促销与履约策略变更，再判断是否需要进一步排查。",
    "check_small_samples": "检查分组样本量，避免对小样本波动做过度解释。",
    "compare_support_cases": "结合客服工单和已核验业务规则开展复核，不直接把相关性当作原因。",
}
