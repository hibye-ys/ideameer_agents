import asyncio
import json
import os
from typing import List, Dict, Optional, TypedDict, Annotated, Sequence, Union
import re
from uuid import uuid4
import aiosqlite
from copy import deepcopy

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages  # 메시지 기록 관리
from langgraph.prebuilt import create_react_agent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from prompts.idea_search import PLAN_GENERATION_PROMPT, EXECUTION_PROMPT, SUMMARY_PROMPT
from langchain_core.prompts import PromptTemplate
from core.logger import get_logger  # 로거 임포트

logger = get_logger(__name__)  # 모듈 레벨 로거 초기화


class PlanStepState(TypedDict):
    """계획의 각 단계를 나타내는 상태"""

    plan_sequence: int
    task: str
    status: str
    action: str
    steps: Annotated[Sequence[BaseMessage], add_messages]
    result: str


class PlanningGraphState(TypedDict):
    initial_request: str
    # plan_title: Optional[str]
    plan_steps: Optional[List[PlanStepState]]
    current_step_index: Optional[int]
    step_result: Optional[List[str]]
    final_summary: Optional[str]
    messages: Annotated[Sequence[BaseMessage], add_messages]


class SearchAgent:
    @staticmethod
    def _to_json_serializable(obj):
        """
        Fallback serializer for json.dump so that LangChain message objects
        (HumanMessage, AIMessage, etc.) become JSON‑friendly.
        """
        from langchain_core.messages import BaseMessage

        if isinstance(obj, BaseMessage):
            return {
                "type": obj.__class__.__name__,
                "content": getattr(obj, "content", ""),
                "additional_kwargs": getattr(obj, "additional_kwargs", {}),
            }
        raise TypeError(
            f"Object of type {obj.__class__.__name__} is not JSON serializable"
        )

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            # __init__에서는 logger 대신 ValueError를 직접 발생시키는 것이 일반적입니다.
            # 또는 여기서도 로깅을 원한다면 self.logger = get_logger(self.__class__.__name__) 등으로 인스턴스 로거를 만들 수 있습니다.
            raise ValueError(
                "GEMINI_API_KEY must be provided or set as an environment variable."
            )
        self.llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash-preview-04-17",
            api_key=self.api_key,
        )
        self.app = None

    def create_plan_node(self, state: PlanningGraphState) -> Dict:
        logger.info("--- 노드: 계획 생성 ---")
        request = state["initial_request"]

        logger.info("LLM 호출: 계획 생성 요청...")
        plan_agent = create_react_agent(
            prompt=PLAN_GENERATION_PROMPT, model=self.llm, tools=[]
        )
        plan_response = plan_agent.invoke({"messages": request})
        plan_json_str = plan_response["messages"][-1].content
        logger.debug(f"LLM으로부터 받은 계획 (raw): {plan_json_str}")

        processed_result = None

        json_block_start = plan_json_str.find("```json")
        if json_block_start != -1:
            json_block_end = plan_json_str.find(
                "```", json_block_start + len("```json")
            )
            if json_block_end != -1:
                json_str_to_parse = plan_json_str[
                    json_block_start + len("```json") : json_block_end
                ].strip()
                try:
                    if json_str_to_parse and json_str_to_parse.strip():
                        processed_result = json.loads(json_str_to_parse)
                        logger.info("JSON 블록 파싱 완료")
                except json.JSONDecodeError as e_block:
                    logger.warning(
                        f"JSON 블록 파싱 오류: {e_block}. 전체 응답 파싱 시도 중..."
                    )
                    pass

        if processed_result is None or processed_result == plan_json_str:
            try:
                if plan_json_str and plan_json_str.strip():
                    result_to_parse = plan_json_str.strip()
                    if result_to_parse.startswith("```") and result_to_parse.endswith(
                        "```"
                    ):
                        result_to_parse = result_to_parse[3:-3].strip()
                    processed_result = json.loads(result_to_parse)
                    logger.info("전체 응답 JSON 파싱 완료")
            except json.JSONDecodeError as e_full:
                logger.error(
                    f"전체 응답 JSON 파싱 오류: {e_full}. 원본 결과 사용 시도."
                )
                try:
                    # 파싱에 완전히 실패하면, 원본 텍스트를 그대로 사용하거나, 에러를 명시하는 구조를 만듭니다.
                    # 여기서는 이전 로직을 따라 'text' 필드에 넣습니다.
                    processed_result = {"text": plan_json_str, "is_raw_text": True}
                    logger.info("원본 텍스트를 결과로 사용합니다.")
                except Exception as e_fallback:
                    logger.error(
                        f"최후의 결과 처리 중 오류: {e_fallback}", exc_info=True
                    )
                    # 이 경우, processed_result가 여전히 None일 수 있습니다. 호출하는 쪽에서 처리해야 합니다.
                    pass

        if processed_result is None:
            logger.error("계획 결과 파싱에 실패하여 빈 계획으로 처리합니다.")
            # 빈 계획이나 오류 메시지를 반환할 수 있습니다.
            return {
                "messages": [
                    AIMessage(content="Error: Failed to parse plan from LLM response.")
                ]
            }

        try:
            plan_data = processed_result
            logger.debug(f"파싱된 plan_data: {plan_data}")

            plan_steps: List[PlanStepState] = []
            # plan_data가 리스트가 아닐 경우 (예: {"text": "...", "is_raw_text": True} 형태)
            if not isinstance(plan_data, list):
                # plan_data가 딕셔너리이고 'text' 키를 가지고 있다면, 이를 task로 하는 단일 스텝 생성 시도
                if isinstance(plan_data, dict) and "text" in plan_data:
                    logger.warning(
                        f"계획 데이터가 리스트가 아닙니다. 'text'를 단일 작업으로 처리 시도: {plan_data.get('text')}"
                    )
                    plan_steps.append(
                        {
                            "plan_sequence": 1,
                            "task": plan_data.get(
                                "text", "LLM으로부터 계획을 파싱하는데 실패했습니다."
                            ),
                            "action": "수동 검토 필요",
                            "status": "not_started",
                            "steps": [],
                            "result": "",
                        }
                    )
                else:
                    logger.error(
                        f"처리할 수 없는 plan_data 형식: {type(plan_data)}. 빈 계획으로 진행합니다."
                    )

            else:
                for item in plan_data:
                    sequence = item.get("plan_sequence")
                    task_desc = item.get("task", "")
                    actions = item.get("action", [])
                    action_str = (
                        "\\n".join(actions)
                        if isinstance(actions, list)
                        else str(actions)
                    )
                    plan_steps.append(
                        {
                            "plan_sequence": sequence,
                            "task": task_desc,
                            "action": action_str,
                            "status": "not_started",
                            "steps": [],
                            "result": "",
                        }
                    )

            if not plan_steps:  # 여전히 plan_steps가 비어있다면 (파싱 실패 등)
                logger.warning(
                    "생성된 계획 단계가 없습니다. 사용자의 초기 요청을 단일 작업으로 처리합니다."
                )
                plan_steps.append(
                    {
                        "plan_sequence": 1,
                        "task": request,  # 사용자의 초기 요청을 작업으로 사용
                        "action": "자동 생성된 계획이 없으므로, 초기 요청을 직접 수행합니다.",
                        "status": "not_started",
                        "steps": [],
                        "result": "",
                    }
                )

            logger.info(f"생성된 계획 단계 수: {len(plan_steps)}")
            plan_steps.sort(key=lambda s: s["plan_sequence"])
            for step in plan_steps:
                logger.debug(f"- 계획 {step['plan_sequence']}: {step['task']}")
            return {
                "plan_steps": plan_steps,
                "messages": plan_response["messages"],  # LLM의 원본 메시지 포함
            }
        except Exception as e:
            logger.error(
                f"계획 생성 노드에서 최종 처리 중 오류 발생: {e}", exc_info=True
            )
            return {
                "messages": [
                    AIMessage(
                        content=f"Error during plan creation post-processing: {e}"
                    )
                ]
            }

    def identify_step_node(self, state: PlanningGraphState) -> Dict:
        logger.info("--- 노드: 다음 단계 식별 ---")
        steps = state.get("plan_steps")
        if not steps:
            logger.error("오류: 상태에 계획 단계가 없습니다.")
            return {
                "current_step_index": None,
                "messages": [AIMessage(content="Error: No plan steps found in state.")],
            }

        next_step_index = None
        for i, step in enumerate(steps):
            if step.get("status", "not_started") in ["not_started", "in_progress"]:
                next_step_index = i
                break

        if next_step_index is not None:
            logger.info(
                f"다음 단계 식별: 인덱스 {next_step_index} - 작업: {steps[next_step_index]['task']}"
            )
            updated_steps = deepcopy(
                steps
            )  # 이전에는 steps.copy() 였으나, 중첩된 딕셔너리 수정을 위해 deepcopy 고려
            updated_steps[next_step_index]["status"] = "in_progress"
            return {
                "current_step_index": next_step_index,
                "plan_steps": updated_steps,
                "messages": [
                    AIMessage(
                        content=f"Executing step {next_step_index}: {updated_steps[next_step_index]['task']}"
                    )
                ],
            }
        else:
            logger.info("모든 계획 단계 완료됨.")
            return {
                "current_step_index": None,
                "messages": [AIMessage(content="All plan steps are completed.")],
            }

    def format_plan_status(self, state: PlanningGraphState) -> str:
        """Helper function to format plan status from state"""
        # 이 함수는 직접 로깅보다는 문자열을 반환하므로, 내부 로깅은 최소화하거나 호출부에서 로깅합니다.
        steps = state.get("plan_steps", [])
        status_text = ""
        status_marks = {
            "completed": "[✓]",
            "in_progress": "[→]",
            "blocked": "[!]",
            "not_started": "[ ]",
        }
        for i, step in enumerate(steps):
            mark = status_marks.get(step.get("status", "not_started"), "[ ]")
            status_text += f"{i}. {mark} {step.get('task', '')}\\n"
            if step.get("action"):
                status_text += f"   Notes: {step['action']}\\n"
        return status_text

    async def execute_step_node(self, state: PlanningGraphState) -> Dict:
        logger.info("--- 노드: 단계 실행 ---")
        steps = state.get("plan_steps")
        current_step_index = state.get("current_step_index")

        if (
            steps is None
            or current_step_index is None
            or current_step_index >= len(steps)
        ):
            logger.error("오류: 실행할 현재 단계 정보를 찾을 수 없습니다.")
            return {
                "step_result": "Error: Could not find current step info.",  # 이 부분도 상태 업데이트에 포함
                "messages": [
                    AIMessage(content="Internal Error: Missing current step info.")
                ],
            }

        current_step_info = steps[current_step_index]
        logger.info(f"단계 {current_step_index} 실행 시작: {current_step_info['task']}")

        prompt = PromptTemplate.from_template(EXECUTION_PROMPT)
        formatted_prompt = prompt.format(
            current_step_index=current_step_index,
            current_step_info_task=current_step_info["task"],
            current_step_info_action=current_step_info["action"],
        )

        final_answer = ""
        ai_message = None
        updated_steps = deepcopy(steps)

        try:
            logger.info("MCP 클라이언트 및 React 에이전트 호출 중...")
            async with MultiServerMCPClient(
                {
                    "firecrawl-mcp": {
                        "command": "npx",
                        "args": ["-y", "firecrawl-mcp"],
                        "env": {"FIRECRAWL_API_KEY": os.getenv("FIRECRAWL_API_KEY")},
                    },
                    "tavily-mcp": {
                        "command": "npx",
                        "args": ["-y", "tavily-mcp@0.1.2"],
                        "env": {"TAVILY_API_KEY": os.getenv("TAVILY_API_KEY")},
                    },
                }
            ) as client:
                agent = create_react_agent(model=self.llm, tools=client.get_tools())
                response = await agent.ainvoke({"messages": formatted_prompt})
                final_answer = response["messages"][-1].content
                ai_message = AIMessage(
                    content=final_answer
                )  # AIMessage(content=response["messages"][-1].content) 와 동일

                # updated_steps = deepcopy(steps) # 성공 시에만 복사하는 것보다 위에서 미리 복사
                updated_steps[current_step_index]["steps"] = (
                    updated_steps[current_step_index].get("steps", [])
                    + response["messages"]
                )
                updated_steps[current_step_index]["result"] = (
                    updated_steps[current_step_index].get("result", "") + final_answer
                )
                updated_steps[current_step_index]["status"] = "completed"
                logger.info(f"단계 {current_step_index} 실행 완료.")

        except Exception as e:
            logger.error(
                f"단계 {current_step_index} 실행 중 LLM 또는 도구 호출 실패: {e}",
                exc_info=True,
            )
            final_answer = f"Error: Could not get execution description from LLM - {e}"
            ai_message = AIMessage(content=final_answer)

            updated_steps[current_step_index]["steps"] = updated_steps[
                current_step_index
            ].get("steps", []) + [ai_message]
            updated_steps[current_step_index]["result"] = (
                updated_steps[current_step_index].get("result", "") + final_answer
            )
            updated_steps[current_step_index]["status"] = "blocked"

        logger.debug(
            f"단계 {current_step_index} 실행 결과 (final_answer): {final_answer}"
        )

        existing_step_results = state.get("step_result", [])
        if isinstance(
            existing_step_results, str
        ):  # 이전 결과가 문자열일 경우 리스트로 변환 (방어 코드)
            logger.warning(
                f"step_result가 문자열이었습니다. 리스트로 변환합니다: {existing_step_results}"
            )
            existing_step_results = [existing_step_results]

        step_result_with_index = (
            f"Step {current_step_index} ({current_step_info['task']}): {final_answer}"
        )
        updated_step_results = existing_step_results + [step_result_with_index]

        return {
            "plan_steps": updated_steps,
            "step_result": updated_step_results,
            "messages": ([ai_message] if ai_message else []),
        }

    def finalize_node(self, state: PlanningGraphState) -> Dict:
        logger.info("--- 노드: 계획 종료 및 요약 ---")

        step_results_list = state.get("step_result", [])
        logger.debug(f"요약할 step_result: {step_results_list}")

        if isinstance(step_results_list, list):
            step_result_str = "\\n\\n".join(step_results_list)
        else:
            logger.warning(
                f"step_result가 리스트가 아닙니다: {type(step_results_list)}. 문자열로 변환합니다."
            )
            step_result_str = str(step_results_list)

        prompt_template = PromptTemplate.from_template(SUMMARY_PROMPT)

        formatted_prompt_str = ""  # 초기화
        try:
            formatted_prompt_str = prompt_template.format(
                initial_request=state["initial_request"],
                step_result=step_result_str,
            )
        except KeyError as e:
            logger.error(
                f"요약 프롬프트 포매팅 중 KeyError: {e}. 프롬프트 변수를 확인하세요.",
                exc_info=True,
            )
            # 포매팅 실패 시, 오류 메시지를 포함하는 AIMessage 반환
            error_summary = f"Error: Could not format summary prompt - {e}"
            return {
                "final_summary": {"text_summary": error_summary, "references": []},
                "messages": [AIMessage(content=error_summary)],
            }

        logger.info("LLM 호출: 최종 요약 요청...")
        logger.debug(f"요약 프롬프트: {formatted_prompt_str[:500]}...")

        llm_response_content = ""
        try:
            agent = create_react_agent(model=self.llm, tools=[])
            response = agent.invoke({"messages": formatted_prompt_str})
            llm_response_content = response["messages"][-1].content
            logger.info("최종 요약 생성 LLM 호출 완료.")
        except Exception as e:
            logger.error(f"최종 요약 생성 중 LLM 호출 실패: {e}", exc_info=True)
            error_summary = f"Error: Could not get final summary from LLM - {e}"
            return {
                "final_summary": {"text_summary": error_summary, "references": []},
                "messages": [AIMessage(content=error_summary)],
            }

        logger.debug(f"LLM으로부터 받은 전체 응답: {llm_response_content}")

        # LLM 응답에서 텍스트 요약과 참조 목록(JSON) 분리 시도
        text_summary_part = llm_response_content
        references_part_json = []

        # "References" 또는 번역된 유사 키워드를 찾아 분리 (SUMMARY_PROMPT의 지침에 따라)
        # 여기서는 "References"를 기준으로 하지만, 실제 LLM 출력에 따라 조정 필요
        references_keywords = [
            "References",
            "참고 자료",
            "Sources",
            "출처",
        ]  # 다양한 언어/표현 고려
        references_section_content = ""

        for keyword in references_keywords:
            references_idx = llm_response_content.rfind(
                f"\\n{keyword}\\n"
            )  # 줄바꿈으로 구분된 섹션 제목 가정
            if references_idx == -1:  # 단순 키워드로도 찾아보기
                references_idx = llm_response_content.rfind(keyword)

            if references_idx != -1:
                # 키워드 이후의 내용을 참조 섹션으로 간주
                potential_references_block = llm_response_content[
                    references_idx + len(keyword) :
                ].strip()
                # ```json ... ``` 블록 찾기
                json_block_start = potential_references_block.find("```json")
                if json_block_start != -1:
                    json_block_end = potential_references_block.find(
                        "```", json_block_start + len("```json")
                    )
                    if json_block_end != -1:
                        references_section_content = potential_references_block[
                            json_block_start + len("```json") : json_block_end
                        ].strip()
                        text_summary_part = llm_response_content[
                            :references_idx
                        ].strip()  # 참조 섹션 이전까지를 요약으로 간주
                        logger.info(
                            f"'{keyword}' 키워드로 참조 목록 섹션 후보를 찾았습니다."
                        )
                        break
                elif potential_references_block.startswith(
                    "["
                ) and potential_references_block.endswith(
                    "]"
                ):  # ```json 없이 바로 배열 시작/종료
                    references_section_content = potential_references_block
                    text_summary_part = llm_response_content[:references_idx].strip()
                    logger.info(
                        f"'{keyword}' 키워드로 JSON 배열 형식의 참조 목록 섹션 후보를 찾았습니다."
                    )
                    break

        if references_section_content:
            try:
                references_part_json = json.loads(references_section_content)
                if isinstance(references_part_json, list):
                    logger.info(
                        f"참조 목록 JSON 파싱 성공: {len(references_part_json)}개 항목."
                    )
                else:
                    logger.warning(
                        f"참조 목록이 JSON 배열이 아닙니다: {type(references_part_json)}. 빈 리스트로 처리합니다."
                    )
                    references_part_json = []
            except json.JSONDecodeError as e:
                logger.warning(
                    f"참조 목록 JSON 파싱 실패: {e}. 원본 참조 섹션 내용을 텍스트 요약에 포함합니다."
                )
                # 파싱 실패 시, 해당 블록을 요약의 일부로 되돌리거나, 별도 텍스트 필드에 저장할 수 있음
                # 여기서는 text_summary_part가 이미 references_idx 이전으로 설정되었으므로, 파싱 실패한 내용은 버려지거나,
                # text_summary_part = llm_response_content # 전체를 다시 요약으로 할당하는 등의 처리가 필요.
                # 현재는 파싱 실패시 references_part_json는 빈 리스트로 유지.
        else:
            logger.info(
                "LLM 응답에서 참조 목록 섹션을 찾지 못했거나 내용이 없습니다. 전체 응답을 텍스트 요약으로 간주합니다."
            )
            text_summary_part = llm_response_content  # 참조 섹션 못 찾으면 전체가 요약

        final_structured_summary = {
            "text_summary": text_summary_part,
            "references": references_part_json,
        }

        logger.debug(f"구조화된 최종 요약: {final_structured_summary}")
        # AIMessage의 content는 문자열이어야 하므로, 최종 사용자에게 보여줄 형태나 주요 정보를 문자열로 만듭니다.
        # 여기서는 text_summary_part를 대표로 사용하거나, 혹은 전체 구조를 JSON 문자열로 변환하여 전달할 수 있습니다.
        # ProjectService에서 구조화된 데이터를 직접 사용하므로, AIMessage는 간략화 하거나 text_summary만 담도록 합니다.
        return {
            "final_summary": final_structured_summary,
            "messages": [AIMessage(content=text_summary_part)],
        }

    async def setup_graph(self, db_path: str = "./memory/contents_research.db"):
        logger.info(f"데이터베이스 경로 {db_path}로 그래프 설정 시작...")
        conn = await aiosqlite.connect(db_path)
        checkpointer = AsyncSqliteSaver(conn)
        logger.info("AsyncSqliteSaver 초기화 완료.")

        workflow = StateGraph(PlanningGraphState)

        workflow.add_node("create_plan", self.create_plan_node)
        workflow.add_node("identify_step", self.identify_step_node)
        workflow.add_node("execute_step", self.execute_step_node)
        workflow.add_node("finalize", self.finalize_node)
        logger.info("워크플로우에 노드 추가 완료.")

        workflow.set_entry_point("create_plan")
        workflow.add_edge("create_plan", "identify_step")
        logger.info("워크플로우 진입점 및 초기 엣지 설정 완료.")

        def should_continue(state: PlanningGraphState) -> str:
            if state.get("current_step_index") is None:
                logger.info("엣지 결정: 모든 단계 완료, 'finalize'로 이동.")
                return "finalize"
            else:
                logger.info(
                    f"엣지 결정: 다음 단계 {state.get('current_step_index')} 실행, 'execute_step'으로 이동."
                )
                return "execute_step"

        workflow.add_conditional_edges(
            "identify_step",
            should_continue,
            {"execute_step": "execute_step", "finalize": "finalize"},
        )

        workflow.add_edge("execute_step", "identify_step")
        workflow.add_edge("finalize", END)
        logger.info("워크플로우 엣지 설정 완료.")

        self.app = workflow.compile(checkpointer=checkpointer)
        logger.info("워크플로우 컴파일 완료.")

    async def run_async(
        self, initial_state: Dict, config: Dict, output_json_path: str = "test.json"
    ):
        if not self.app:
            logger.error(
                "그래프가 설정되지 않았습니다. setup_graph()를 먼저 호출해야 합니다."
            )
            raise RuntimeError("Graph not set up. Call setup_graph() first.")

        logger.info(f"--- 그래프 비동기 스트림 실행 시작 ---")
        logger.debug(f"초기 상태: {initial_state}")
        logger.debug(f"설정: {config}")
        logger.debug(f"출력 JSON 경로: {output_json_path}")

        async for output in self.app.astream(initial_state, config):
            node_name = list(output.keys())[0]
            node_output = output[node_name]
            logger.info(f"노드 '{node_name}'로부터 출력:")

            if "messages" in node_output and node_output["messages"]:
                last_msg = node_output["messages"][-1]
                log_content = str(getattr(last_msg, "content", ""))[:300]
                logger.debug(
                    f"  >> 마지막 메시지 ({type(last_msg).__name__}): {log_content}"
                )
            if (
                "step_result" in node_output and node_output["step_result"]
            ):  # step_result가 비어있지 않은 경우만 로깅
                log_step_result = str(node_output["step_result"])[:300]
                logger.debug(f"  >> 단계 결과: {log_step_result}")
            if "plan_steps" in node_output and node_output["plan_steps"]:
                logger.debug(
                    f"  >> 계획 단계 업데이트됨: {len(node_output['plan_steps'])} 단계"
                )
            if "final_summary" in node_output and node_output["final_summary"]:
                logger.debug(
                    f"  >> 최종 요약: {str(node_output['final_summary'])[:300]}"
                )

        logger.info("그래프 실행 완료. 최종 상태 스냅샷 저장 중...")
        snapshot = await self.app.aget_state(config)
        data = snapshot.values if hasattr(snapshot, "values") else snapshot

        try:
            with open(output_json_path, "w", encoding="utf-8") as fp:
                json.dump(
                    data,
                    fp,
                    ensure_ascii=False,
                    indent=2,
                    default=self._to_json_serializable,
                )
            logger.info(f"최종 상태 스냅샷을 '{output_json_path}'에 저장했습니다.")
        except IOError as e:
            logger.error(
                f"'{output_json_path}'에 최종 상태 스냅샷 저장 실패: {e}", exc_info=True
            )
        except TypeError as e:
            logger.error(
                f"JSON 직렬화 중 오류 발생 (최종 상태 저장 실패): {e}", exc_info=True
            )

        return data


async def main():

    search_agent_instance = SearchAgent()
    await search_agent_instance.setup_graph()

    request = "프랭크 오션의 'Self Control'과 비슷한 분위기의 노래와 이미지를 찾아줘."
    initial_state = {
        "initial_request": request,
        "messages": [HumanMessage(content=request)],
    }
    config = {"configurable": {"thread_id": str(uuid4())}}

    # run_async 내부에서 로깅이 수행됩니다.
    await search_agent_instance.run_async(
        initial_state, config, "search_output.json"
    )  # 출력 파일명 변경


if __name__ == "__main__":
    asyncio.run(main())
