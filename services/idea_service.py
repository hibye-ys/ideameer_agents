import json
import asyncio
from typing import List, Dict, Any, AsyncGenerator, Optional
from supabase import Client
from core import gemini
from prompts.idea import IDEA_HELPER_PROMPT, IDEA_REPORT_PROMPT
from fastapi import HTTPException
from core.logger import get_logger

logger = get_logger(__name__)


class IdeaService:
    def __init__(self, supabase: Client):
        self.supabase = supabase
        logger.info(f"IdeaService initialized with Supabase client.")

    async def generate_idea_stream(
        self,
        user_id: str,
        chat_id: str,
        chat_history: List[Dict[str, str]] = [],
        prompt_text: str = "",
        referenced_ideas: Optional[List[str]] = None,
    ) -> AsyncGenerator[str, None]:
        logger.info(
            f"Generating idea stream for user_id: {user_id}, chat_id: {chat_id}"
        )
        logger.debug(f"Prompt text: {prompt_text}")
        logger.debug(f"Referenced ideas: {referenced_ideas}")

        referenced_ideas_message = []
        if referenced_ideas:
            logger.info(
                f"Fetching {len(referenced_ideas)} referenced ideas from Supabase."
            )
            for idea_id in referenced_ideas:
                try:
                    response = (
                        self.supabase.table("idea_record")
                        .select("title, data_content")
                        .eq("id", str(idea_id))
                        .eq("user_id", user_id)
                        .execute()
                    )
                    logger.debug(f"Supabase response for idea_id {idea_id}: {response}")
                    if response.data:
                        record = response.data[0]
                        title = record.get("title", "제목 없음")
                        data_content = record.get("data_content", "내용 없음")
                        idea_text = (
                            f"참고 아이디어 제목: {title}\\n내용: {data_content}"
                        )
                        referenced_ideas_message.append(
                            {"role": "user", "content": idea_text}
                        )
                        logger.debug(f"Added referenced idea {idea_id} to messages.")
                    else:
                        logger.warning(
                            f"Idea with ID {idea_id} not found for user {user_id}."
                        )
                except Exception as e:
                    logger.error(
                        f"Error fetching idea {idea_id} from Supabase: {str(e)}",
                        exc_info=True,
                    )

        logger.debug(
            f"Referenced ideas messages to be used: {referenced_ideas_message}"
        )

        stream_response = None
        try:
            logger.info("Calling Gemini API for idea generation...")
            api_call_coroutine = gemini.process_data(
                data=prompt_text,
                history=chat_history + referenced_ideas_message,
                system_prompt=IDEA_HELPER_PROMPT,
                stream=True,
            )
            stream_response = await api_call_coroutine
            logger.info("Gemini API call successful, streaming response received.")
        except Exception as e:
            logger.error(
                f"Gemini API call error (generate_idea_stream): {str(e)}", exc_info=True
            )
            yield f"아이디어 생성 중 오류가 발생했습니다: {str(e)}"
            return

        full_response = ""
        try:
            if stream_response:
                async for chunk in stream_response:
                    if hasattr(chunk, "text") and chunk.text:
                        full_response += chunk.text
                        yield chunk.text
                    elif isinstance(chunk, str):
                        full_response += chunk
                        yield chunk
                logger.info("Finished streaming Gemini response.")
            else:
                logger.warning("Gemini stream_response was None, skipping streaming.")

            referenced_ideas_messages_str = ""
            for i in referenced_ideas_message:
                referenced_ideas_messages_str += str(i)

            message_pair = {
                "user": {
                    "role": "user",
                    "content": prompt_text + referenced_ideas_messages_str,
                },
                "assistant": {"role": "assistant", "content": full_response},
            }
            logger.debug(f"Message pair to save: {message_pair}")

            try:
                logger.info(f"Saving message pair to Supabase for chat_id: {chat_id}")
                self.supabase.rpc(
                    "append_message_pair",
                    {
                        "_id": chat_id,
                        "_uid": user_id,
                        "_pair": json.dumps(message_pair),
                    },
                ).execute()
                logger.info("Message pair saved successfully to Supabase.")
            except Exception as e:
                logger.error(
                    f"Supabase RPC call error (generate_idea_stream): {str(e)}",
                    exc_info=True,
                )

        except Exception as e:
            logger.error(
                f"Error during streaming or saving (generate_idea_stream): {str(e)}",
                exc_info=True,
            )
            yield f"스트리밍 처리 또는 저장 중 오류가 발생했습니다: {str(e)}"

    async def create_idea_report(
        self,
        user_id: str,
        chat_id: str,
        chat_history: List[Dict[str, str]],
        prompt_text: str,
        referenced_ideas: List[str],
    ) -> Dict[str, Any]:
        logger.info(f"Creating idea report for user_id: {user_id}, chat_id: {chat_id}")
        logger.debug(f"Prompt text for report: {prompt_text}")
        logger.debug(f"Referenced ideas for report: {referenced_ideas}")

        current_message_content = (
            prompt_text if prompt_text else "대화내용을 종합해서 리포트 작성해줘"
        )
        current_message = {"role": "user", "content": current_message_content}

        response_text = ""
        try:
            logger.info("Calling Gemini API for report generation...")
            response_text = gemini.process_data(
                data=current_message["content"],
                history=chat_history,
                system_prompt=IDEA_REPORT_PROMPT,
                stream=False,
            )
            logger.info("Gemini API call for report successful.")
            logger.debug(f"Generated report text (raw): {response_text[:200]}...")
        except Exception as e:
            logger.error(
                f"Gemini API call error (create_idea_report): {str(e)}", exc_info=True
            )
            raise HTTPException(
                status_code=500, detail=f"AI 리포트 생성 중 외부 API 오류: {str(e)}"
            )

        try:
            logger.info(f"Saving generated report to Supabase.")
            if referenced_ideas:
                logger.info(
                    f"Updating report for {len(referenced_ideas)} referenced ideas."
                )
                for idea_id in referenced_ideas:
                    logger.debug(f"Updating report for idea_id: {idea_id}")
                    self.supabase.table("idea_record").update(
                        {"ai_report": response_text}
                    ).eq("id", str(idea_id)).eq("user_id", user_id).execute()

            logger.info(f"Updating summary for chat_id: {chat_id}")
            self.supabase.table("ai_chats").update({"summary": response_text}).eq(
                "id", chat_id
            ).eq("user_id", user_id).execute()
            logger.info("Report saved successfully to Supabase.")

            return {
                "status": "success",
                "message": "AI 리포트가 생성되어 idea_record 및 ai_chats에 업데이트되었습니다.",
                "user_id": user_id,
                "chat_id": chat_id,
            }
        except Exception as e:
            logger.error(
                f"Supabase report saving error (create_idea_report): {str(e)}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail=f"데이터베이스 저장 중 오류: {str(e)}"
            )
