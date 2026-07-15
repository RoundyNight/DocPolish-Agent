import json
import os
import uuid
import subprocess
from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse, FileResponse
from dehydrator import dehydrate_document, calculate_dynamic_baseline
from doc_tools import all_tools  # LangChain @tool 列表
from retriever import match_template
import httpx

from agent import agent_graph
from langgraph.types import Command
from langchain_core.messages import AIMessage, ToolMessage
from template_ingest import ingest_template, reassemble_with_corrections
from spec_store import (
    set_active_spec, get_spec, list_all_specs, register_dynamic_spec,
    delete_dynamic_spec,
)

app = FastAPI(title="AI Document Agent Backend")

def _parse_raw_doc(doc_path: str):
    """仅用于内部分析，返回原始 JSON 或 None"""
    try:
        cmd = ["officecli", "get", doc_path, "/body", "--depth", "6", "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return json.loads(result.stdout)
    except Exception:
        return None

# 加载系统提示词
PROMPT_DIR = os.path.join(os.path.dirname(__file__), "prompts")
SYSTEM_PROMPT_PATH = os.path.join(PROMPT_DIR, "system_prompt.txt")
def load_system_prompt() -> str:
    with open(SYSTEM_PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read()

SYSTEM_PROMPT = load_system_prompt()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 路径配置
TEST_DOC_PATH = os.path.abspath("../test-docs/test.docx")
WORKSPACE_DIR = os.path.join(os.path.dirname(__file__), "workspace")
WORKSPACE_DOC = os.path.join(WORKSPACE_DIR, "current.docx")
os.makedirs(WORKSPACE_DIR, exist_ok=True)

def get_current_doc_path():
    """返回当前活动文档的绝对路径：优先工作区文件，否则测试文档。"""
    if os.path.exists(WORKSPACE_DOC):
        return WORKSPACE_DOC
    return TEST_DOC_PATH

def parse_doc_with_officecli(doc_path):
    """调用 OfficeCLI 获取文档原始 JSON"""
    try:
        cmd = ["officecli", "get", doc_path, "/body", "--depth", "6", "--json"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        raw_json = json.loads(result.stdout)
        return raw_json
    except subprocess.CalledProcessError as e:
        raise HTTPException(status_code=500, detail=f"OfficeCLI 失败: {e.stderr}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="OfficeCLI 返回的数据无法解析为 JSON")

# ---------------- 已有路由 ----------------
@app.get("/api/raw-doc")
def get_raw_document():
    doc_path = get_current_doc_path()
    if not os.path.exists(doc_path):
        raise HTTPException(status_code=404, detail=f"找不到文档: {doc_path}")
    try:
        raw_json = parse_doc_with_officecli(doc_path)
        return {
            "status": "success",
            "file_name": os.path.basename(doc_path),
            "data": raw_json,
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/parse-doc")
def parse_document():
    doc_path = get_current_doc_path()
    if not os.path.exists(doc_path):
        raise HTTPException(status_code=404, detail=f"找不到文档: {doc_path}")
    try:
        raw_json = parse_doc_with_officecli(doc_path)
        dehydrated_data = dehydrate_document(raw_json)
        return {
            "status": "success",
            "file_name": os.path.basename(doc_path),
            "data": dehydrated_data,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}\n\n{tb}")

@app.post("/api/upload-doc")
async def upload_doc(file: UploadFile = File(...)):
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 文件")
    try:
        with open(WORKSPACE_DOC, "wb") as f:
            content = await file.read()
            f.write(content)
        return {
            "status": "success",
            "file_name": file.filename,
            "message": "文档已上传并设为当前工作文档"
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"文件保存失败: {str(e)}")

# ---------------- 请求模型 ----------------
class ChatRequest(BaseModel):
    api_key: str
    model: str
    dehydrated_data: list
    message: str

class ToolCall(BaseModel):
    tool: str
    arguments: dict

class ExecuteRequest(BaseModel):
    tool_calls: list[ToolCall]

# ---------- Agent 请求模型 ----------
class AgentStartRequest(BaseModel):
    api_key: str
    model: str
    dehydrated_data: dict
    message: str
    spec_id: str = "general"

class AgentContinueRequest(BaseModel):
    thread_id: str = Field(..., description="Agent 会话线程ID")
    decision: str = Field(..., description="approve 或 revise")
    feedback: str = Field(default="", description="修订意见（decision=revise 时必填）")

class SpecConfirmRequest(BaseModel):
    spec_id: str
    corrections: dict[int, str] = Field(default_factory=dict, description="index→新角色 修正")
    activate: bool = True
    save: bool = False
    name: str = Field(default="", max_length=80)

# ---------- Agent SSE 辅助 ----------
STEP_LABELS = {
    "analyze_done": "分析文档中",
    "plan_done": "规划建议中",
    "error": "出错了",
    "execute_done": "执行完成",
}

def format_sse(event: str, data: dict) -> str:
    """格式化 SSE 事件"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

def _extract_plan_from_messages(messages: list) -> dict:
    """从 AIMessage.tool_calls 提取前端可读的 plan 格式"""
    tool_calls_info = []
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.tool_calls:
            for tc in m.tool_calls:
                tool_calls_info.append({"tool": tc["name"], "arguments": tc["args"]})
            break
    return {"tool_calls": tool_calls_info}

def _extract_results_from_messages(messages: list) -> list:
    """从 ToolMessage 提取执行结果"""
    results = []
    for m in messages:
        if isinstance(m, ToolMessage):
            status = "error" if m.status == "error" else "success"
            results.append({"tool": m.name, "status": status, "result": m.content})
    return results

# ---------------- AI 对话路由（原有，保留） ----------------
@app.post("/api/chat")
async def chat_with_ai(req: ChatRequest):
    baseline_text = ""
    try:
        doc_path = get_current_doc_path()
        if os.path.exists(doc_path):
            raw_json = _parse_raw_doc(doc_path)
            if raw_json:
                results = raw_json.get("data", {}).get("results", [])
                raw_nodes = results[0].get("children", []) if results else []
                if raw_nodes:
                    baseline = calculate_dynamic_baseline(raw_nodes)
                    label_map = {
                        "font_east_asia": "中文字体",
                        "font_ascii": "西文字体",
                        "font_size": "字号",
                        "bold": "加粗",
                        "color": "颜色",
                    }
                    parts = []
                    for key, label in label_map.items():
                        val = baseline.get(key)
                        if val is not None:
                            if isinstance(val, bool):
                                val = "是" if val else "否"
                            parts.append(f"{label}：{val}")
                    if parts:
                        baseline_text = f"文档默认格式（已省略的属性均为此值，无需重复设置）：{'；'.join(parts)}。\n"
    except Exception:
        pass

    matched_content = match_template(req.message)
    template_prompt = ""
    if matched_content:
        template_prompt = (
            "用户指定了如下排版规范（若用户未明确指定，则为系统默认的通用规范），请严格遵循所有细节，逐条转换为工具调用。\n"
            "请根据文档的语义（如\"摘要\"二字、标题层级编号）来识别段落角色并执行对应设置。\n"
            f"【排版规范】：\n{matched_content}\n\n"
        )
    else:
        template_prompt = "用户未指定特定排版规范，请根据通用美观原则排版。\n"

    user_message = (
        f"{baseline_text}"
        f"{template_prompt}"
        f"以下是用户的文档结构（仅保留有意义的格式差异）：\n"
        f"```json\n{json.dumps(req.dehydrated_data, ensure_ascii=False, indent=2)}\n```\n\n"
        f"以下是用户需求：{req.message}\n请生成操作指令。"
    )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_message},
    ]

    base_url = "https://api.deepseek.com"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {req.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": req.model,
                    "messages": messages,
                    "temperature": 0.1,
                    "max_tokens": 32768,
                    "response_format": {"type": "json_object"}
                },
                timeout=90.0
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            try:
                operations = json.loads(content)
                return {"status": "success", "response": operations}
            except json.JSONDecodeError:
                return {"status": "success", "raw_response": content}
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"请求大模型失败: {str(e)}")

# ---------------- 执行修改路由（原有，保留） ----------------
@app.post("/api/execute")
async def execute_ai_operations(req: ExecuteRequest):
    doc_path = get_current_doc_path()
    if not os.path.exists(doc_path):
        raise HTTPException(status_code=404, detail="当前工作区没有文档，请先上传")
    results = []
    for call in req.tool_calls:
        tool_name = call.tool
        args = call.arguments
        # 用 @tool 函数替代 TOOL_MAP
        tool_func = None
        for t in all_tools:
            if t.name == tool_name:
                tool_func = t
                break
        if tool_func is None:
            results.append({"tool": tool_name, "status": "failed", "reason": "未知工具"})
            continue
        try:
            res = await tool_func.ainvoke(args) if hasattr(tool_func, "ainvoke") else tool_func.invoke(args)
            results.append({"tool": tool_name, "status": "success", "result": str(res)})
        except Exception as e:
            results.append({"tool": tool_name, "status": "error", "reason": str(e)})
    success_count = sum(1 for r in results if r["status"] == "success")
    return {
        "status": "success" if success_count == len(req.tool_calls) else "partial_success",
        "details": results
    }

# ---------------- 文档下载路由 ----------------
@app.get("/api/download-doc")
def download_doc():
    doc_path = get_current_doc_path()
    if not os.path.exists(doc_path):
        raise HTTPException(status_code=404, detail="当前没有可下载的文档")
    return FileResponse(
        doc_path,
        filename=os.path.basename(doc_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.get("/")
def read_root():
    return {"status": "success", "message": "Doc-Agent 后端引擎与过滤层已启动"}

# ---------- Agent SSE 路由 ----------
@app.post("/api/agent/start")
async def agent_start(req: AgentStartRequest):
    """启动 Agent 流程，通过 SSE 流式推送状态更新"""
    doc_path = get_current_doc_path()
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "user_message": req.message,
        "dehydrated_data": req.dehydrated_data,
        "api_key": req.api_key,
        "model": req.model,
        "doc_path": doc_path,
        "baseline_text": "",
        "template_prompt": "",
        "role_catalog": "",
        "human_decision": "",
        "human_feedback": "",
        "current_step": "",
        "error": "",
        "spec_id": req.spec_id,
        "messages": [],  # add_messages reducer 起始为空列表
    }

    async def stream():
        try:
            result = await agent_graph.ainvoke(initial_state, config=config)

            for step_name, step_label in STEP_LABELS.items():
                step_value = result.get("current_step", "")
                if step_value == step_name:
                    yield format_sse("state", {
                        "step": step_name,
                        "label": step_label,
                        "thread_id": thread_id,
                    })

            state_snapshot = agent_graph.get_state(config=config)
            next_nodes = state_snapshot.next

            if next_nodes:
                # interrupt: 从 AIMessage 提取 plan
                plan = _extract_plan_from_messages(state_snapshot.values.get("messages", []))
                yield format_sse("interrupt", {
                    "plan": plan,
                    "thread_id": thread_id,
                    "label": "等待审核",
                })
            else:
                if result.get("error"):
                    yield format_sse("error", {
                        "message": result["error"],
                        "thread_id": thread_id,
                    })
                else:
                    results = _extract_results_from_messages(state_snapshot.values.get("messages", []))
                    yield format_sse("result", {
                        "execution_results": results,
                        "thread_id": thread_id,
                        "label": "执行完成",
                    })
        except Exception as e:
            yield format_sse("error", {"message": str(e), "thread_id": thread_id})

    return StreamingResponse(stream(), media_type="text/event-stream")

@app.post("/api/agent/continue")
async def agent_continue(req: AgentContinueRequest):
    """用户审核后继续 Agent 流程"""
    config = {"configurable": {"thread_id": req.thread_id}}
    resume_value = {"decision": req.decision, "feedback": req.feedback}

    async def stream():
        try:
            result = await agent_graph.ainvoke(
                Command(resume=resume_value), config=config
            )

            for step_name, step_label in STEP_LABELS.items():
                step_value = result.get("current_step", "")
                if step_value == step_name:
                    yield format_sse("state", {
                        "step": step_name,
                        "label": step_label,
                        "thread_id": req.thread_id,
                    })

            state_snapshot = agent_graph.get_state(config=config)
            next_nodes = state_snapshot.next

            if next_nodes:
                plan = _extract_plan_from_messages(state_snapshot.values.get("messages", []))
                yield format_sse("interrupt", {
                    "plan": plan,
                    "thread_id": req.thread_id,
                    "label": "等待审核（修订版）",
                })
            else:
                if result.get("error"):
                    yield format_sse("error", {
                        "message": result["error"],
                        "thread_id": req.thread_id,
                    })
                else:
                    results = _extract_results_from_messages(state_snapshot.values.get("messages", []))
                    yield format_sse("result", {
                        "execution_results": results,
                        "thread_id": req.thread_id,
                        "label": "执行完成",
                    })
        except Exception as e:
            yield format_sse("error", {"message": str(e), "thread_id": req.thread_id})

    return StreamingResponse(stream(), media_type="text/event-stream")

# ---------- 规范管理路由 (S2 模板逆向) ----------
@app.post("/api/spec/upload-template")
async def upload_template_spec(
    file: UploadFile = File(...),
    api_key: str = Form(""),
    model: str = Form("deepseek-chat"),
    domain: str = Form("上传模板"),
):
    """上传模板文档, 逆向出 StyleSpec, 返回审核预览(不自动激活)。"""
    if not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="只支持 .docx 模板文件")
    if not api_key:
        raise HTTPException(status_code=400, detail="需要 api_key 调用 LLM 进行角色分类")
    try:
        # 保存模板到临时路径
        tmp_path = os.path.join(WORKSPACE_DIR, f"_template_{uuid.uuid4().hex[:8]}.docx")
        content = await file.read()
        with open(tmp_path, "wb") as f:
            f.write(content)
        # officecli 解析
        raw_json = parse_doc_with_officecli(tmp_path)
        os.remove(tmp_path)  # 解析完即删, 不占空间
        # 模板逆向管线
        spec, preview = ingest_template(raw_json, api_key, model, domain)
        return {"status": "success", "preview": preview}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"模板逆向失败: {str(e)}")

@app.post("/api/spec/confirm")
async def confirm_spec(req: SpecConfirmRequest):
    """确认激活规范；模板仅在 save=true 时命名并持久化。"""
    try:
        if req.corrections:
            spec, preview = reassemble_with_corrections(req.spec_id, req.corrections)
        else:
            spec = get_spec(req.spec_id)
            if spec is None:
                raise HTTPException(status_code=404, detail=f"找不到规范: {req.spec_id}; 可用: {list_all_specs()}")
            preview = None
        if req.save:
            name = req.name.strip()
            if not name:
                raise HTTPException(status_code=400, detail="保存模板时必须填写模板名称")
            spec.domain = name
            register_dynamic_spec(spec, persist=True)
        if req.activate:
            set_active_spec(req.spec_id)
        return {"status": "success", "spec_id": req.spec_id, "domain": spec.domain,
                "activated": req.activate, "saved": req.save, "preview": preview}
    except HTTPException:
        raise
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"确认规范失败: {str(e)}")

@app.get("/api/spec/list")
def list_specs():
    """列出预置及用户明确保存的动态规范。"""
    spec_ids = list_all_specs()
    details = {spec_id: get_spec(spec_id).domain for spec_id in spec_ids if get_spec(spec_id)}
    return {"status": "success", "specs": spec_ids, "spec_details": details}


@app.delete("/api/spec/{spec_id}")
def delete_spec(spec_id: str):
    """删除用户模板；系统预置规范受保护。"""
    try:
        if not delete_dynamic_spec(spec_id):
            raise HTTPException(status_code=404, detail=f"找不到模板: {spec_id}")
        return {"status": "success", "spec_id": spec_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
