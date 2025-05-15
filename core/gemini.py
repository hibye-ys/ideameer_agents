import os
import base64
import mimetypes
from google import genai
from google.genai import types
from typing_extensions import Iterator, Union


def process_data(
    data: Union[str, bytes],
    history: list[dict] = None,
    system_prompt: list[str] | str = None,
    threshold_mb: int = 20,
    enable_function_calling: bool = False,
    function_declarations: list[dict] = None,
    function_map: dict[str, callable] = None,
    enable_structured_output: bool = False,
    response_schema: type = None,
    enable_thinking: bool = True,
    stream: bool = False,
) -> Union[str, Iterator[types.GenerateContentResponse]]:
    """
    data: 파일 경로 또는 순수 텍스트
    prompt: 멀티모달 입력 후 추가할 사용자 프롬프트
    history: 이전 대화 이력 (role: 'system'|'user'|'assistant', content: str)
    """

    # 1) 클라이언트 초기화
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    model = "gemini-2.5-flash-preview-04-17"
    # 2) 대화 이력(contents) 생성
    contents: list[types.Content] = []
    if history:
        for msg in history:
            role = msg["role"]
            if role == "assistant":
                role = "model"
            if role in ["user", "model"]:
                contents.append(
                    types.Content(
                        role=role, parts=[types.Part.from_text(text=msg["content"])]
                    )
                )

    # 3) 새 입력 분기 (파일 vs 텍스트)
    if os.path.isfile(data):
        mime_type, _ = mimetypes.guess_type(data)
        mime_type = mime_type or "application/octet-stream"
        size = os.path.getsize(data)
        if mime_type.startswith("text/"):
            with open(data, "r", encoding="utf-8") as f:
                contents.append(f.read())
        else:
            if size <= threshold_mb * 1024 * 1024:
                with open(data, "rb") as f:
                    b = f.read()
                part = types.Part.from_bytes(data=b, mime_type=mime_type)
                contents.append(part)
            else:
                file_ref = client.files.upload(file=data)
                contents.append(file_ref)
    else:
        if isinstance(data, str):
            contents.append(
                types.Content(role="user", parts=[types.Part.from_text(text=data)])
            )
        # TODO: data가 bytes이고 파일이 아닌 경우 처리 (현재 시나리오에서는 발생하지 않을 것으로 예상)

    # 4) GenerateContentConfig 설정
    config = types.GenerateContentConfig()

    # 4.1) config 생성 및 시스템 프롬프트 설정
    config = types.GenerateContentConfig()
    if system_prompt:
        # 문자열 하나면 리스트로, 리스트면 그대로 사용
        config.system_instruction = (
            [system_prompt] if isinstance(system_prompt, str) else system_prompt
        )

    if enable_function_calling and function_declarations:
        config.tools = [types.Tool(function_declarations=function_declarations)]
    if enable_structured_output and response_schema:
        config.response_mime_type = "application/json"
        config.response_schema = response_schema

    # 4.2) Thinking Config 설정
    if not enable_thinking:
        # thinking_budget=0으로 설정하면 생각 비활성화 :contentReference[oaicite:3]{index=3}
        config.thinking_config = types.ThinkingConfig(thinking_budget=0)

    if stream:
        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY")).aio
        # generate_content_stream을 호출하면 Iterator[Chunk]를 반환 :contentReference[oaicite:1]{index=1}
        return client.models.generate_content_stream(
            model=model, contents=contents, config=config
        )

    # 5) 모델 호출 (1차)
    response = client.models.generate_content(
        model=model, contents=contents, config=config
    )

    # 6) Function Calling 후처리 (필요 시)
    if enable_function_calling and getattr(response, "function_calls", None):
        call = response.function_calls[0]
        result = function_map[call.name](**call.arguments)
        response = client.models.generate_content(
            model=model,
            contents=[
                types.Content(role="model", parts=[types.Part(function_call=call)]),
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(
                            name=call.name, response=result
                        )
                    ],
                ),
            ],
            config=config,
        )

    return response.text


if __name__ == "__main__":
    from pydantic import BaseModel
    import os

    """gemini-2.5-flash-preview-04-17"""
    # 1) 대화 이력 포함한 멀티턴 텍스트 대화
    history = [
        {"role": "system", "content": "너는 요약봇이야."},
        {"role": "user", "content": "이번 회의록 요약해줄래?"},
        {"role": "assistant", "content": "물론이죠, 회의록을 보내주세요."},
    ]
    result = process_data(
        data="meeting.pdf",
        prompt="이 회의록을 간략히 요약해줘.",
        api_key="YOUR_API_KEY",
        history=history,
    )
    print(result)

    # 2) 이미지 + 함수 호출 + 대화 이력
    history = [
        {"role": "user", "content": "이 이미지를 분석하고 메타데이터를 저장해줘."}
    ]

    def save_metadata(description: str) -> dict:
        return {"meta_id": 42}

    set_meta_decl = {
        "name": "save_metadata",
        "description": "이미지 설명을 저장합니다.",
        "parameters": {
            "type": "object",
            "properties": {"description": {"type": "string"}},
            "required": ["description"],
        },
    }
    result = process_data(
        data="photo.png",
        prompt="사진 설명을 제공하고 저장해줘.",
        api_key="YOUR_API_KEY",
        history=history,
        enable_function_calling=True,
        function_declarations=[set_meta_decl],
        function_map={"save_metadata": save_metadata},
    )
    print(result)
