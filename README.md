# Ideameer Agents

이 프로젝트는 FastAPI를 기반으로 하는 **Ideameer**의 에이전트 서버입니다. Ideameer는 아이디어 구상부터 관리, 창작 워크플로우까지 지원하는 웹 애플리케이션입니다.
이 에이전트 서버는 Ideameer의 핵심 기능을 제공하고 확장하는 역할을 담당합니다.

## Features

- **아이디어 서치 및 기록:** 사용자가 아이디어를 검색하고 기록할 수 있는 기능을 제공합니다.
- **기획 추천 및 아이디어 정리:** 수집된 아이디어를 바탕으로 기획을 추천하고, 아이디어를 체계적으로 정리하는 기능을 지원합니다.
- FastAPI를 사용한 웹 애플리케이션 프레임워크
- 아이디어 (Ideas) 관련 API 엔드포인트 제공
- 프로젝트 (Projects) 관련 API 엔드포인트 제공

## Install

1.  **저장소 복제:**

    ```bash
    git clone git remote add origin https://github.com/hibye-ys/ideameer_agents.git
    cd ideameer-agents # 또는 실제 프로젝트 폴더명
    ```

2.  **가상 환경 생성 및 활성화 (uv 사용 권장):**

    ```bash
    python -m venv .venv
    source .venv/bin/activate  # macOS/Linux
    # .venv\Scripts\activate  # Windows
    ```

    또는 `uv`를 직접 사용하여 가상환경을 만들 수 있습니다:

    ```bash
    uv venv
    source .venv/bin/activate # macOS/Linux
    # .venv\Scripts\activate  # Windows
    ```

3.  **의존성 설치 (uv 사용):**

    ```bash
    uv pip install -r requirements.txt
    ```

    만약 `pyproject.toml`에 의존성이 명시되어 있다면:

    ```bash
    uv pip install .
    ```

4.  **애플리케이션 실행:**

    ```bash
    python app.py
    ```

    또는 Uvicorn 직접 실행:

    ```bash
    uvicorn app:app --reload --host 0.0.0.0 --port 8000
    ```

    애플리케이션은 `http://localhost:8000` 에서 실행됩니다.

## Todo

- [ ] 이미지 생성 에이전트 추가
- [ ] 비디오 생성 에이전트 추가
- [ ] 음악 생성 에이전트 추가
- [ ] 데이터 분석 에이전트 추가
