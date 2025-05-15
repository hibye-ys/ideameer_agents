from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import os

from routers import idea_router, project_router

app = FastAPI()

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 라우터 포함
app.include_router(idea_router.router, prefix="/ideas", tags=["Ideas"])
app.include_router(project_router.router, prefix="/projects", tags=["Projects"])


@app.get("/")
async def root():
    return {"message": "Welcome to FastAPI application"}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
