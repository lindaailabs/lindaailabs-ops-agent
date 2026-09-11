"""全局状态定义（LangGraph TypedDict）。

注意 total=False：节点只返回自己关心的字段，避免覆盖其它字段。
"""
from typing import Any, Dict, List, Optional, TypedDict


class OpsAgentState(TypedDict, total=False):
    user_input: str                                   # 用户原始请求
    messages: List[Dict[str, Any]]                    # 最近 16 条对话消息
    observations: List[Dict[str, Any]]                # 最近 3 次检查摘要，含采集时间
    clarification_messages: List[Dict[str, Any]]
    selected_skill: Optional[str]                     # 规划器选中的 Skill 名
    skill_args: Dict[str, Any]                        # 传给 Skill 的参数
    risk_level: str                                   # "low" | "high"，来自 Skill 静态声明
    mode: str                                          # "interactive" | "automated"，触发来源（人在场=interactive，无人值守=automated）
    command: Optional[Dict[str, Any]]                 # 交给执行层的指令
    execution_result: Optional[Dict[str, Any]]        # 执行结果
    approved: Optional[bool]                          # 审批结果（None=未触发/待审）
    approval_comment: Optional[str]                   # 审批意见
    final_answer: Optional[str]                       # 最终返回给用户的话术
    error: Optional[str]                              # 异常信息
