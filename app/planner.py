import json
import os
from typing import Literal
import httpx
from pydantic import BaseModel, ValidationError


class Plan(BaseModel):
    dimension: Literal["channel", "product"] = "channel"
    channel: Literal["自然流量", "广告投放", "合作渠道"] | None = None
    product: Literal["标准版", "专业版"] | None = None
    metric: Literal["refund_rate"] = "refund_rate"
    supported: bool = True
    clarification: str = ""


def plan_question(question, provider="rules"):
    if provider == "rules":
        if "退款" not in question:
            return Plan(supported=False, clarification="当前支持 2026-09-01 至 09-14 模拟数据的退款率分析，请明确按渠道或商品拆解。")
        dimension = "product" if any(word in question for word in ["商品", "产品", "版本"]) else "channel"
        channel = next((value for value in ["自然流量", "广告投放", "合作渠道"] if value in question), None)
        product = next((value for value in ["标准版", "专业版"] if value in question), None)
        return Plan(dimension=dimension, channel=channel, product=product)
    if provider != "openai":
        raise ValueError("未知规划器")
    prompt = "你是受约束的数据分析规划器。只支持 refund_rate 指标，固定对比 2026-09-01..07 与 09-08..14。不要生成 SQL。对其他指标或不支持的明确日期返回 supported=false 并填写 clarification。按 JSON schema 输出计划。"
    response = httpx.post(os.getenv("MODEL_BASE_URL", "http://127.0.0.1:11434/v1").rstrip("/") + "/chat/completions", headers={"Authorization": "Bearer " + os.getenv("MODEL_API_KEY", "")}, json={"model": os.getenv("MODEL_NAME", "qwen3:8b"), "temperature": 0, "messages": [{"role": "system", "content": prompt + json.dumps(Plan.model_json_schema(), ensure_ascii=False)}, {"role": "user", "content": question}], "response_format": {"type": "json_object"}}, timeout=45)
    response.raise_for_status()
    try:
        return Plan.model_validate_json(response.json()["choices"][0]["message"]["content"])
    except (ValidationError, KeyError, IndexError) as error:
        raise ValueError("模型计划不符合白名单结构，未执行查询") from error
