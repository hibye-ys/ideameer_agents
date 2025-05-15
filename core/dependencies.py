from supabase import create_client, Client
import os

# from core.config import settings  # 방금 만든 config에서 settings를 가져옵니다.


def get_supabase_client() -> Client:
    return create_client(
        os.getenv("NEXT_PUBLIC_SUPABASE_URL"),
        os.getenv("NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY"),
    )
