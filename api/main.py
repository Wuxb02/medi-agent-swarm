"""FastAPI 应用入口"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import chat, knowledge, sessions, dashboard

app = FastAPI(
    title="MediZJ Agent Swarm API",
    description="多智能体医疗助手系统 API",
    version="0.1.0",
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat.router)
app.include_router(knowledge.router)
app.include_router(sessions.router)
app.include_router(dashboard.router)


@app.get("/")
async def root():
    return {"message": "MediZJ Agent Swarm API", "docs": "/docs"}
