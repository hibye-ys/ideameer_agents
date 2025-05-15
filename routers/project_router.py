from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from supabase import Client


from core.dependencies import get_supabase_client
from services.project_service import ProjectService, create_project_service
from typing import Dict, Any
import asyncio

router = APIRouter()


class ProjectPlanGetRequest(BaseModel):
    user_id: str
    project_id: str


class ProjectPlanFinalGetRequest(BaseModel):
    user_id: str
    project_id: str
    plan_id: str


class ProjectSearchIdeaRequest(BaseModel):
    user_id: str
    project_id: str
    prompt: str
    ai_result_id: str = None


_project_service_instance = None
_initialization_lock = asyncio.Lock()


async def get_project_service(
    supabase: Client = Depends(get_supabase_client),
) -> ProjectService:
    global _project_service_instance
    if _project_service_instance is None:
        async with _initialization_lock:
            if _project_service_instance is None:
                _project_service_instance = await create_project_service(supabase)
    return _project_service_instance


@router.post("_plan")
async def plan_recommendation(
    request: ProjectPlanGetRequest,
    service: ProjectService = Depends(get_project_service),
) -> Dict[str, Any]:
    try:
        return await service.recommend_project_plan(
            user_id=request.user_id, project_id=request.project_id
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"plan_recommendation 처리 중 예외: {e}")
        raise HTTPException(
            status_code=500,
            detail="프로젝트 계획 추천 중 알 수 없는 서버 오류가 발생했습니다.",
        )


@router.post("_final")
async def plan_organization(
    request: ProjectPlanFinalGetRequest,
    service: ProjectService = Depends(get_project_service),
) -> Dict[str, Any]:
    try:
        return await service.organize_project_plan(
            user_id=request.user_id,
            project_id=request.project_id,
            plan_id=request.plan_id,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"plan_organization 처리 중 예외: {e}")
        raise HTTPException(
            status_code=500,
            detail="프로젝트 계획 구성 중 알 수 없는 서버 오류가 발생했습니다.",
        )


@router.post("_search_idea")
async def search_idea(
    request: ProjectSearchIdeaRequest,
    service: ProjectService = Depends(get_project_service),
) -> Dict[str, Any]:
    try:
        return await service.search_ideas(
            user_id=request.user_id,
            project_id=request.project_id,
            prompt=request.prompt,
            ai_result_id=request.ai_result_id,
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        print(f"search_idea 처리 중 예외: {e}")
        raise HTTPException(
            status_code=500,
            detail="프로젝트 계획 구성 중 알 수 없는 서버 오류가 발생했습니다.",
        )
