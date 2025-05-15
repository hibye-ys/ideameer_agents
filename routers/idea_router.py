from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict, Any, AsyncGenerator, Optional
from supabase import Client
from core.dependencies import get_supabase_client
from services.idea_service import IdeaService  # IdeaService 임포트


router = APIRouter()


class ChatRequest(BaseModel):
    user_id: str
    chat_id: str
    chat_history: List[Dict[str, str]]
    prompt: str
    referenced_ideas: List[str]


def get_idea_service(supabase: Client = Depends(get_supabase_client)) -> IdeaService:
    return IdeaService(supabase)


@router.post("_helper")
async def idea_helper(
    request: ChatRequest, service: IdeaService = Depends(get_idea_service)
) -> StreamingResponse:
    try:
        return StreamingResponse(
            service.generate_idea_stream(
                user_id=request.user_id,
                chat_id=request.chat_id,
                chat_history=request.chat_history,
                prompt_text=request.prompt,
                referenced_ideas=request.referenced_ideas,
            ),
            media_type="text/plain",
        )
    except Exception as e:

        print(f"idea_helper 스트림 설정 오류: {e}")

        async def error_stream():
            yield f"스트리밍 중 심각한 오류 발생: {str(e)}"

        return StreamingResponse(
            error_stream(), media_type="text/plain", status_code=500
        )


@router.post("_report")
async def idea_report(
    request: ChatRequest, service: IdeaService = Depends(get_idea_service)
) -> Dict[str, Any]:
    try:
        return await service.create_idea_report(
            user_id=request.user_id,
            chat_id=request.chat_id,
            chat_history=request.chat_history,
            prompt_text=request.prompt,
            referenced_ideas=request.referenced_ideas,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"idea_report 처리 중 예외: {e}")
        raise HTTPException(
            status_code=500,
            detail="리포트 생성 중 알 수 없는 서버 오류가 발생했습니다.",
        )
