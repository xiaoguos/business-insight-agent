"""Explicit test-only model double. Never imported by the product."""

import copy
import json
import threading
from app.agents import AgentError, ModelReply


class ScriptedModel:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()
        self.fail_roles = set()
        self.override_tools = {}
        self.knowledge_required = False
        self.barrier = None
        self.bad_group = False
        self.repeat_tool = False

    def __call__(self, role, messages):
        with self.lock:
            self.calls.append((role, copy.deepcopy(messages)))
        if role in self.fail_roles:
            raise AgentError("injected model failure")
        if self.barrier and role in {"analysis", "knowledge"} and len(messages) == 2:
            self.barrier.wait(timeout=5)

        def final(output):
            return ModelReply({"action": "final", "output": output}, total_tokens=17)

        def tool(name, args):
            return ModelReply(
                {"action": "tool", "tool": name, "arguments": args}, total_tokens=17
            )

        context = json.loads(messages[1]["content"])["context"]
        if role in self.override_tools:
            return tool(self.override_tools[role], {})
        if role == "conductor":
            return final(
                {
                    "supported": True,
                    "analysis_goal": "按渠道计算退款指标",
                    "knowledge_goal": "检索退款业务规则",
                    "report_goal": "综合指标与证据，生成待审批报告",
                    "knowledge_required": self.knowledge_required,
                }
            )
        if role == "analysis":
            if len(messages) == 2 or self.repeat_tool:
                return tool(
                    "query_metrics", {"dimension": "channel", "metric": "refund_rate"}
                )
            return final({"selected_call_id": "analysis-1"})
        if role == "knowledge":
            if len(messages) == 2:
                return tool("search_rules", {"query": "退款业务规则", "limit": 5})
            observation = json.loads(messages[-1]["content"])["tool_observation"]
            evidence = observation.get("result", [])
            return final(
                {
                    "citations": [
                        {"id": evidence[0]["id"], "quote": evidence[0]["text"][:100]}
                    ]
                    if evidence
                    else []
                }
            )
        if role == "report":
            evidence = context["knowledge"]["evidence"]
            return final(
                {
                    "focus_groups": ["non-existent"]
                    if self.bad_group
                    else [context["analysis"]["analysis"]["segments"][0]["name"]],
                    "citations": [
                        {"id": e["id"], "quote": e["quote"]} for e in evidence
                    ],
                    "recommendations": ["check_small_samples", "verify_refund_lag"],
                }
            )
        raise AssertionError("Unexpected agent role")
