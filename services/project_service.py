from supabase import Client
from core import gemini
from prompts.plan import PLAN_RECOMMENDATION_PROMPT, PLAN_ORGANIZATION_PROMPT
from fastapi import HTTPException
from typing import Dict, List, Optional
import json
from agents.search_agent import SearchAgent
from langchain_core.messages import HumanMessage
from uuid import uuid4
from datetime import datetime
from core.logger import get_logger

logger = get_logger(__name__)


class ProjectService:
    def __init__(self, supabase: Client, search_agent: SearchAgent):
        self.supabase = supabase
        self.search_agent = search_agent
        logger.info("ProjectService initialized.")

    async def recommend_project_plan(
        self, user_id: str, project_id: str
    ) -> Dict[str, any]:
        logger.info(
            f"Recommending project plan for user_id: {user_id}, project_id: {project_id}"
        )
        system_prompt = PLAN_RECOMMENDATION_PROMPT
        try:
            logger.info("Fetching ideas from Supabase...")
            ideas_rows = (
                self.supabase.table("ideas")
                .select("content")
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .execute()
            )
            logger.debug(f"Supabase ideas_rows response: {ideas_rows}")

            idea_contents: List[str] = [
                row["content"] for row in (ideas_rows.data if ideas_rows.data else [])
            ]
            if not idea_contents:
                logger.warning(
                    f"No ideas found for user {user_id}, project {project_id}. Proceeding with empty combined_text."
                )

            combined_text = "\n".join(idea_contents)
            logger.debug(f"Combined idea text for Gemini: {combined_text[:200]}...")

            logger.info("Calling Gemini API for plan recommendation...")
            response_text = gemini.process_data(
                data=combined_text,
                history=[],
                system_prompt=system_prompt,
                stream=False,
            )
            logger.info("Gemini API call for plan recommendation successful.")
            logger.debug(f"Gemini response_text (raw): {response_text}")
            if not response_text or not response_text.strip():
                logger.error(
                    "Gemini response_text is empty or contains only whitespace."
                )
                raise HTTPException(
                    status_code=500,
                    detail="Gemini로부터 빈 응답을 받았습니다. 계획을 생성할 수 없습니다.",
                )

        except Exception as e:
            logger.error(
                f"Error in Gemini API or Supabase query (recommend_project_plan): {str(e)}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"계획 추천 생성 중 외부 API/DB 오류: {str(e)}"
            )

        try:
            plan_data = None
            if isinstance(response_text, dict):
                plan_data = response_text
                logger.info("Gemini response is already a dict.")
            elif isinstance(response_text, str):
                logger.info("Attempting to parse JSON from Gemini string response...")
                json_block_start = response_text.find("```json")
                if json_block_start != -1:
                    json_block_end = response_text.find(
                        "```", json_block_start + len("```json")
                    )
                    if json_block_end != -1:
                        json_str_to_parse = response_text[
                            json_block_start + len("```json") : json_block_end
                        ].strip()
                        try:
                            plan_data = json.loads(json_str_to_parse)
                            logger.info("Parsed JSON block from Gemini response.")
                        except json.JSONDecodeError as e_block:
                            logger.warning(
                                f"JSON block parsing error: {e_block}. Falling back to full response parsing."
                            )
                            pass

                if plan_data is None:
                    try:
                        plan_data = json.loads(response_text)
                        logger.info("Parsed full JSON response from Gemini.")
                    except json.JSONDecodeError as e_full:
                        logger.error(
                            f"Full response JSON parsing error: {e_full}", exc_info=True
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=f"Gemini 응답에서 JSON 데이터를 파싱할 수 없습니다. 오류: {e_full}",
                        )
            else:
                logger.error(
                    f"Unexpected response format from Gemini: {type(response_text)}"
                )
                raise HTTPException(
                    status_code=500,
                    detail="Gemini로부터 예상치 못한 형식의 응답을 받았습니다.",
                )

            if plan_data is None:
                logger.error("plan_data is None after parsing attempts.")
                raise HTTPException(
                    status_code=500,
                    detail="JSON 데이터 파싱 후에도 plan_data가 None입니다.",
                )

            logger.debug(f"Parsed plan_data: {plan_data}")

            insert_data = {
                "user_id": user_id,
                "project_id": project_id,
                "title": plan_data.get("title", "제목 없는 계획"),
                "contents": plan_data.get("content"),
                "description": plan_data.get("description"),
                "is_ai": True,
            }
            logger.info(f"Inserting new plan into Supabase: {insert_data.get('title')}")
            insert_response = self.supabase.table("plans").insert(insert_data).execute()
            logger.debug(f"Supabase insert response: {insert_response}")
            if insert_response.data:
                logger.info("New project plan created successfully in Supabase.")
            else:
                logger.error(
                    f"Failed to insert plan into Supabase. Response: {insert_response}"
                )

            return {
                "status": "success",
                "message": "새로운 프로젝트 계획이 생성되었습니다.",
                "user_id": user_id,
                "project_id": project_id,
            }
        except json.JSONDecodeError as e:
            logger.error(
                f"JSON parsing error (recommend_project_plan): {str(e)}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail=f"계획 데이터 형식 오류: Gemini 응답이 유효한 JSON이 아닙니다. {str(e)}",
            )
        except Exception as e:
            logger.error(
                f"Error saving plan to Supabase (recommend_project_plan): {str(e)}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"데이터베이스에 계획 생성 중 오류: {str(e)}"
            )

    async def organize_project_plan(
        self,
        user_id: str,
        project_id: str,
        plan_id: str,
    ) -> Dict[str, any]:
        logger.info(
            f"Organizing project plan for user_id: {user_id}, project_id: {project_id}, plan_id: {plan_id}"
        )
        system_prompt = PLAN_ORGANIZATION_PROMPT

        try:
            logger.info(f"Fetching plan contents from Supabase for plan_id: {plan_id}")
            plan_response = (
                self.supabase.table("plans")
                .select("contents")
                .eq("id", plan_id)
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .execute()
            )
            logger.debug(f"Supabase plan_response: {plan_response}")

            if not plan_response.data or not plan_response.data[0].get("contents"):
                logger.error(
                    f"Plan not found or contents are empty for plan_id: {plan_id}"
                )
                raise HTTPException(
                    status_code=404,
                    detail=f"ID {plan_id}에 해당하는 계획을 찾을 수 없거나 내용이 비어 있습니다.",
                )

            data_to_organize = plan_response.data[0]["contents"]
            logger.debug(f"Data to organize: {data_to_organize[:200]}...")

            logger.info("Calling Gemini API for plan organization...")
            response_text = gemini.process_data(
                data=data_to_organize,
                system_prompt=system_prompt,
                stream=False,
            )
            logger.info("Gemini API call for plan organization successful.")
            logger.debug(f"Organized plan text (raw): {response_text[:200]}...")

        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error in Gemini API or Supabase query (organize_project_plan): {str(e)}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"계획 구성 중 외부 API/DB 오류: {str(e)}"
            )

        try:
            logger.info(f"Updating organized plan in Supabase for plan_id: {plan_id}")
            update_response = (
                self.supabase.table("plans")
                .update(
                    {
                        "contents": response_text,
                        "updated_at": datetime.now().isoformat(),
                    }
                )
                .eq("id", plan_id)
                .eq("user_id", user_id)
                .eq("project_id", project_id)
                .execute()
            )
            logger.debug(f"Supabase update response: {update_response}")
            if update_response.data:
                logger.info("Project plan updated successfully in Supabase.")
            else:
                logger.error(
                    f"Failed to update plan in Supabase for plan_id: {plan_id}. Response: {update_response}"
                )
                raise HTTPException(
                    status_code=500,
                    detail="데이터베이스 업데이트에 실패했습니다. 변경된 행이 없습니다.",
                )

            return {
                "status": "success",
                "message": "프로젝트 계획이 업데이트되었습니다.",
                "user_id": user_id,
                "project_id": project_id,
                "plan_id": plan_id,
            }
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error saving organized plan to Supabase (organize_project_plan): {str(e)}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"데이터베이스 업데이트 중 오류: {str(e)}"
            )

    async def search_ideas(
        self,
        user_id: str,
        project_id: str,
        prompt: str,
        ai_result_id: Optional[str] = None,
    ) -> Dict[str, any]:
        logger.info(
            f"Searching ideas for user_id: {user_id}, project_id: {project_id}, prompt: {prompt}, ai_result_id: {ai_result_id}"
        )

        initial_state = {
            "initial_request": prompt,
            "messages": [HumanMessage(content=prompt)],
        }
        config = {"configurable": {"thread_id": str(uuid4())}}

        logger.info("Running SearchAgent...")
        search_agent_final_state_values = await self.search_agent.run_async(
            initial_state, config
        )
        logger.info("SearchAgent run completed.")
        logger.debug(
            f"SearchAgent final state values: {search_agent_final_state_values}"
        )

        structured_summary_data = search_agent_final_state_values.get("final_summary")

        text_summary = ""
        references_list = []

        if isinstance(structured_summary_data, dict):
            text_summary = structured_summary_data.get("text_summary", "")
            references_list = structured_summary_data.get("references", [])
            if not text_summary and not references_list:
                logger.warning(
                    f"SearchAgent 반환값의 final_summary가 비어있거나 예상치 못한 구조입니다: {structured_summary_data}"
                )
                if search_agent_final_state_values.get("messages"):
                    last_message_content = search_agent_final_state_values["messages"][
                        -1
                    ].content
                    if isinstance(last_message_content, str):
                        text_summary = last_message_content
                        logger.info(
                            "final_summary가 비어있어 messages의 마지막 content를 text_summary로 사용합니다."
                        )
        elif isinstance(
            search_agent_final_state_values.get("messages", [])[-1].content, str
        ):
            logger.warning(
                "SearchAgent가 구조화된 요약 대신 단순 문자열을 반환했습니다. 이전 방식으로 처리합니다."
            )
            text_summary = search_agent_final_state_values["messages"][-1].content
        else:
            logger.error(
                f"SearchAgent로부터 유효한 요약 데이터를 받지 못했습니다: {structured_summary_data}"
            )
            text_summary = "아이디어 검색 결과를 처리하는 중 오류가 발생했습니다."

        logger.info(f"Processed text_summary from SearchAgent: {text_summary[:200]}...")
        logger.info(
            f"Processed references from SearchAgent: {len(references_list)} items."
        )
        logger.debug(f"References content: {references_list}")

        processed_result_for_db = {
            "text_summary": text_summary,
            "references": references_list,
        }

        try:
            new_messages = (
                {"role": "user", "content": prompt},
                {
                    "role": "assistant",
                    "content": json.dumps(processed_result_for_db, ensure_ascii=False),
                },
            )

            if ai_result_id is None:
                logger.info("Creating new ai_results entry in Supabase...")
                insert_result = (
                    self.supabase.table("ai_results")
                    .insert(
                        {
                            "user_id": user_id,
                            "project_id": project_id,
                            "messages": list(new_messages),
                            "title": prompt if prompt else "검색 결과",
                            "type": "search",
                        }
                    )
                    .execute()
                )
                logger.debug(f"Supabase insert ai_results response: {insert_result}")
                created_id = insert_result.data[0]["id"] if insert_result.data else None
                if created_id:
                    logger.info(f"New ai_results entry created with id: {created_id}")
                else:
                    logger.error(
                        f"Failed to create new ai_results entry. Response: {insert_result}"
                    )
                    raise HTTPException(status_code=500, detail="AI 결과 저장 실패")
                return {
                    "status": "success",
                    "ai_result_id": created_id,
                    "result": processed_result_for_db,
                }
            else:
                logger.info(
                    f"Appending messages to existing ai_results_id: {ai_result_id}"
                )
                existing_data_response = (
                    self.supabase.table("ai_results")
                    .select("messages")
                    .eq("id", ai_result_id)
                    .eq("user_id", user_id)
                    .execute()
                )
                logger.debug(
                    f"Supabase select existing messages response: {existing_data_response}"
                )
                if not existing_data_response.data:
                    logger.error(
                        f"ai_result_id {ai_result_id} not found for user {user_id}."
                    )
                    raise HTTPException(
                        status_code=404, detail="기존 AI 결과를 찾을 수 없습니다."
                    )

                current_messages_raw = existing_data_response.data[0].get("messages")
                current_messages = []
                if isinstance(current_messages_raw, str):
                    try:
                        current_messages = json.loads(current_messages_raw)
                    except json.JSONDecodeError:
                        logger.warning(
                            f"Failed to parse existing messages string for {ai_result_id}. Treating as new."
                        )
                elif isinstance(current_messages_raw, list):
                    current_messages = current_messages_raw
                else:
                    logger.warning(
                        f"Existing messages for {ai_result_id} is not a list or string: {current_messages_raw}. Initializing as list."
                    )

                updated_messages = current_messages + list(new_messages)

                update_response = (
                    self.supabase.table("ai_results")
                    .update(
                        {
                            "messages": updated_messages,
                            "updated_at": datetime.now().isoformat(),
                        }
                    )
                    .eq("id", ai_result_id)
                    .eq("user_id", user_id)
                    .execute()
                )
                logger.debug(f"Supabase update ai_results response: {update_response}")
                if update_response.data:
                    logger.info(
                        f"Successfully appended messages to ai_results_id: {ai_result_id}"
                    )
                else:
                    logger.error(
                        f"Failed to update ai_results_id: {ai_result_id}. Response: {update_response}"
                    )
                    raise HTTPException(status_code=500, detail="AI 결과 업데이트 실패")

                return {
                    "status": "success",
                    "ai_result_id": ai_result_id,
                    "result": processed_result_for_db,
                }

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in search_ideas DB operation: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"아이디어 검색 결과 처리 중 오류: {str(e)}"
            )

    async def create_new_project(
        self, user_id: str, title: str, description: Optional[str] = None
    ) -> Dict[str, any]:
        logger.info(f"Creating new project for user_id: {user_id} with title: {title}")
        try:
            insert_data = {
                "user_id": user_id,
                "title": title,
                "description": description,
                "last_accessed_at": datetime.now().isoformat(),
            }
            response = self.supabase.table("projects").insert(insert_data).execute()
            logger.debug(f"Supabase create project response: {response}")
            if response.data:
                project_id = response.data[0]["id"]
                logger.info(f"New project created successfully with id: {project_id}")
                return {
                    "status": "success",
                    "project_id": project_id,
                    "title": title,
                    "description": description,
                    "message": "새로운 프로젝트가 생성되었습니다.",
                }
            else:
                logger.error(f"Failed to create new project. Response: {response}")
                raise HTTPException(
                    status_code=500, detail="프로젝트 생성에 실패했습니다."
                )
        except Exception as e:
            logger.error(f"Error creating new project: {str(e)}", exc_info=True)
            raise HTTPException(
                status_code=500, detail=f"프로젝트 생성 중 오류 발생: {str(e)}"
            )

    async def update_project_last_accessed(self, user_id: str, project_id: str) -> None:
        logger.info(
            f"Updating last_accessed_at for project_id: {project_id}, user_id: {user_id}"
        )
        try:
            response = (
                self.supabase.table("projects")
                .update({"last_accessed_at": datetime.now().isoformat()})
                .eq("id", project_id)
                .eq("user_id", user_id)
                .execute()
            )
            logger.debug(f"Supabase update_project_last_accessed response: {response}")
            if not response.data:
                logger.warning(
                    f"Update last_accessed_at for project {project_id} might not have affected any rows or returned no data."
                )
            else:
                logger.info(
                    f"Successfully updated last_accessed_at for project_id: {project_id}"
                )
        except Exception as e:
            logger.error(
                f"Error updating project last_accessed_at for project {project_id}: {str(e)}",
                exc_info=True,
            )


async def create_project_service(supabase: Client) -> ProjectService:
    search_agent = SearchAgent()
    await search_agent.setup_graph()
    logger.info("SearchAgent instance created and graph set up for ProjectService.")
    return ProjectService(supabase=supabase, search_agent=search_agent)
