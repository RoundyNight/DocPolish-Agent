let currentDehydratedData = null;
let currentThreadId = null;
let currentSpecId = "general";
let pendingSpecPreview = null;
let currentDocumentFileName = "";
let currentTemplateFileName = "";
let activeTemplateSpecId = "";

const apiKeyInput = document.getElementById("apiKey");
const modelSelect = document.getElementById("model");
const testConnBtn = document.getElementById("testConnBtn");
const uploadInput = document.getElementById("uploadInput");
const uploadBtn = document.getElementById("uploadBtn");
const chatMessages = document.getElementById("chatMessages");
const userInput = document.getElementById("userInput");
const sendBtn = document.getElementById("sendBtn");
const statusBar = document.getElementById("statusBar");
const templateInput = document.getElementById("templateInput");
const specSelect = document.getElementById("specSelect");
const refreshSpecBtn = document.getElementById("refreshSpecBtn");
const specDropdown = document.getElementById("specDropdown");
const specDropdownTrigger = document.getElementById("specDropdownTrigger");
const specDropdownLabel = document.getElementById("specDropdownLabel");
const specDropdownMenu = document.getElementById("specDropdownMenu");
const sidebarResizer = document.getElementById("sidebarResizer");
const chatPanel = document.getElementById("chatPanel");
const attachmentShelf = document.getElementById("attachmentShelf");
const documentAttachment = document.getElementById("documentAttachment");
const templateAttachment = document.getElementById("templateAttachment");
const documentFileName = document.getElementById("documentFileName");
const templateFileName = document.getElementById("templateFileName");

// 本地存储恢复
const savedApiKey = localStorage.getItem("deepseek_api_key");
const savedModel = localStorage.getItem("deepseek_model");
if (savedApiKey) apiKeyInput.value = savedApiKey;
if (savedModel) modelSelect.value = savedModel;

apiKeyInput.addEventListener("input", () => {
    localStorage.setItem("deepseek_api_key", apiKeyInput.value.trim());
});
modelSelect.addEventListener("change", () => {
    localStorage.setItem("deepseek_model", modelSelect.value);
});

// 测试连接
testConnBtn.addEventListener("click", async () => {
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) { alert("请输入 API Key"); return; }
    testConnBtn.disabled = true;
    testConnBtn.textContent = "测试中...";
    try {
        const resp = await fetch("https://api.deepseek.com/v1/models", {
            headers: { Authorization: `Bearer ${apiKey}` }
        });
        if (resp.ok) {
            alert("连接成功!");
        } else {
            const err = await resp.json();
            alert("连接失败: " + (err.error?.message || resp.statusText));
        }
    } catch (e) {
        alert("网络错误: " + e.message);
    } finally {
        testConnBtn.disabled = false;
        testConnBtn.textContent = "测试连接";
    }
});

// 统一上传入口：“+”使用悬浮菜单中当前勾选的类型
uploadBtn.addEventListener("click", () => {
    const uploadType = document.querySelector('input[name="uploadType"]:checked')?.value || "document";
    (uploadType === "template" ? templateInput : uploadInput).click();
});

document.querySelectorAll('input[name="uploadType"]').forEach(input => {
    input.addEventListener("change", () => {
        uploadBtn.title = input.value === "template" ? "上传模板" : "上传需排版的文档";
    });
});

function showAttachment(type, fileName) {
    const isTemplate = type === "template";
    const card = isTemplate ? templateAttachment : documentAttachment;
    const name = isTemplate ? templateFileName : documentFileName;
    name.textContent = fileName;
    card.hidden = false;
    attachmentShelf.hidden = false;
}

function setUploadBusy(busy, label = "") {
    uploadBtn.disabled = busy;
    uploadBtn.textContent = busy ? "…" : "+";
    uploadBtn.setAttribute("aria-label", busy ? label : "选择并上传文档");
}

uploadInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    setUploadBusy(true, "正在解析待排版文档");
    try {
        const uploadResp = await fetch("http://127.0.0.1:8000/api/upload-doc", {
            method: "POST", body: formData
        });
        if (!uploadResp.ok) throw new Error("上传失败");
        const parseResp = await fetch("http://127.0.0.1:8000/api/parse-doc");
        const parseData = await parseResp.json();
        if (parseData.status !== "success") throw new Error("解析文档失败");
        currentDehydratedData = parseData.data;
        currentDocumentFileName = file.name;
        showAttachment("document", file.name);
        appendMessage("system", `已上传并解析文档: ${file.name}`);
        updateTemplateFormatAction();
        loadPreview();
        if (userInput) {
            userInput.placeholder = "输入排版需求，如\"帮我优化文档格式\"";
            userInput.removeAttribute("readonly");
        }
    } catch (err) {
        alert("上传或解析出错: " + err.message);
    } finally {
        setUploadBusy(false);
        uploadInput.value = "";
    }
});

// ---------- 模板上传与规范管理 ----------
templateInput.addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const apiKey = apiKeyInput.value.trim();
    if (!apiKey) { alert("上传模板需要 API Key 进行角色分类"); return; }
    setUploadBusy(true, "正在逆向模板规范");
    const formData = new FormData();
    formData.append("file", file);
    formData.append("api_key", apiKey);
    formData.append("model", modelSelect.value);
    formData.append("domain", file.name.replace(/\.docx$/i, ""));
    try {
        const resp = await fetch("http://127.0.0.1:8000/api/spec/upload-template", {
            method: "POST", body: formData
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || "模板逆向失败");
        }
        const data = await resp.json();
        pendingSpecPreview = data.preview;
        currentTemplateFileName = file.name;
        showAttachment("template", file.name);
        showSpecPreview(data.preview);
        refreshSpecList();
    } catch (err) {
        alert("模板逆向出错: " + err.message);
    } finally {
        setUploadBusy(false);
        templateInput.value = "";
    }
});

specSelect.addEventListener("change", () => {
    currentSpecId = specSelect.value;
    if (currentSpecId !== activeTemplateSpecId) activeTemplateSpecId = "";
    syncSpecDropdownSelection();
    updateTemplateFormatAction();
});

refreshSpecBtn.addEventListener("click", () => refreshSpecList());
specDropdownTrigger.addEventListener("click", () => {
    const open = specDropdownMenu.hidden;
    specDropdownMenu.hidden = !open;
    specDropdownTrigger.setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", event => {
    if (!specDropdown.contains(event.target)) closeSpecDropdown();
});
document.addEventListener("keydown", event => {
    if (event.key === "Escape") closeSpecDropdown();
});

function closeSpecDropdown() {
    specDropdownMenu.hidden = true;
    specDropdownTrigger.setAttribute("aria-expanded", "false");
}

function selectSpec(specId) {
    specSelect.value = specId;
    specSelect.dispatchEvent(new Event("change"));
    closeSpecDropdown();
}

function renderSpecDropdown() {
    specDropdownMenu.innerHTML = "";
    Array.from(specSelect.options).forEach(option => {
        const row = document.createElement("div");
        row.className = "spec-option-row" + (option.value === currentSpecId ? " selected" : "");
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(option.value === currentSpecId));

        const choose = document.createElement("button");
        choose.type = "button";
        choose.className = "spec-option-select";
        choose.textContent = option.textContent;
        choose.addEventListener("click", () => selectSpec(option.value));
        row.appendChild(choose);

        if (option.value.startsWith("template_")) {
            const remove = document.createElement("button");
            remove.type = "button";
            remove.className = "spec-option-delete";
            remove.textContent = "×";
            remove.title = `删除模板 ${option.textContent}`;
            remove.setAttribute("aria-label", `删除模板 ${option.textContent}`);
            remove.addEventListener("click", event => {
                event.stopPropagation();
                deleteTemplate(option.value, option.textContent, remove);
            });
            row.appendChild(remove);
        }
        specDropdownMenu.appendChild(row);
    });
    syncSpecDropdownSelection();
}

function syncSpecDropdownSelection() {
    const option = Array.from(specSelect.options).find(item => item.value === currentSpecId);
    specDropdownLabel.textContent = option?.textContent || "选择排版规范";
    specDropdownMenu.querySelectorAll(".spec-option-row").forEach((row, index) => {
        const selected = specSelect.options[index]?.value === currentSpecId;
        row.classList.toggle("selected", selected);
        row.setAttribute("aria-selected", String(selected));
    });
}

async function deleteTemplate(specId, rawName, deleteButton) {
    const templateName = rawName.replace("（仅本次）", "");
    if (!window.confirm(`确定删除模板“${templateName}”吗？此操作无法撤销。`)) return;

    deleteButton.disabled = true;
    try {
        const resp = await fetch(`http://127.0.0.1:8000/api/spec/${encodeURIComponent(specId)}`, {
            method: "DELETE",
        });
        if (!resp.ok) {
            const error = await resp.json();
            throw new Error(error.detail || "删除失败");
        }
        const deletedCurrent = currentSpecId === specId;
        if (deletedCurrent) {
            currentSpecId = "general";
            activeTemplateSpecId = "";
            pendingSpecPreview = null;
        }
        await refreshSpecList(deletedCurrent ? "general" : currentSpecId);
        updateTemplateFormatAction();
        appendMessage("system", `模板已删除: ${templateName} (${specId})`);
    } catch (err) {
        alert("删除模板出错: " + err.message);
        deleteButton.disabled = false;
    }
}

async function refreshSpecList(preferredSpecId = "") {
    try {
        const resp = await fetch("http://127.0.0.1:8000/api/spec/list");
        const data = await resp.json();
        if (data.status === "success") {
            const prev = preferredSpecId || specSelect.value;
            specSelect.innerHTML = "";
            const labels = { "general": "通用规范", "academic_gb": "学术论文" };
            const specDetails = data.spec_details || {};
            data.specs.forEach(id => {
                const opt = document.createElement("option");
                opt.value = id;
                opt.textContent = labels[id] || specDetails[id] || id;
                specSelect.appendChild(opt);
            });
            if (data.specs.includes(prev)) specSelect.value = prev;
            currentSpecId = specSelect.value;
            renderSpecDropdown();
            updateTemplateFormatAction();
        }
    } catch (e) { /* 静默失败 */ }
}

function showSpecPreview(preview) {
    const roles = preview.roles || {};
    const lowConf = preview.low_confidence_indices || [];
    let html = `<b>模板规范逆向完成: ${preview.domain}</b><br>`;
    html += `<span style="color:#666">规范ID: ${preview.spec_id}</span><br><br>`;
    html += `<b>识别到 ${Object.keys(roles).length} 个角色:</b><br>`;
    Object.entries(roles).forEach(([role, info]) => {
        const styleStr = Object.entries(info.style || {})
            .map(([k, v]) => `${k}=${v}`).join(", ");
        html += `<div style="margin:4px 0; padding:5px; background:#f0f7ff; border-radius:4px; border-left:3px solid #2B579A;">`;
        html += `<b>${role}</b> (${info.count}段) — ${styleStr}`;
        if (info.composite) html += ` <span style="color:#E65100">[复合]</span>`;
        if (info.anomalies && info.anomalies.length) html += ` <span style="color:#C62828">⚠${info.anomalies.length}异类</span>`;
        if (info.samples && info.samples.length) html += `<br><span style="color:#888;font-size:12px">例: ${info.samples[0]}</span>`;
        html += `</div>`;
    });
    if (lowConf.length) {
        html += `<br><span style="color:#E65100">⚠ ${lowConf.length} 段置信度低(可修正): index ${lowConf.join(", ")}</span><br>`;
    }
    html += `<br><div class="review-actions"><button class="approve-btn" onclick="confirmSpec('${preview.spec_id}', true)">保存模板并使用</button>`;
    html += `<button class="revise-btn" onclick="confirmSpec('${preview.spec_id}', false)">仅本次使用</button></div>`;
    appendMessage("ai", html);
}

window.confirmSpec = async function(specId, saveTemplate) {
    let templateName = "";
    if (saveTemplate) {
        const suggestedName = pendingSpecPreview?.domain || currentTemplateFileName.replace(/\.docx$/i, "");
        const inputName = window.prompt("请输入保存后的模板名称", suggestedName);
        if (inputName === null) return;
        templateName = inputName.trim();
        if (!templateName) {
            alert("模板名称不能为空");
            return;
        }
    }
    try {
        const resp = await fetch("http://127.0.0.1:8000/api/spec/confirm", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                spec_id: specId,
                corrections: {},
                activate: true,
                save: saveTemplate,
                name: templateName,
            })
        });
        if (!resp.ok) {
            const error = await resp.json();
            throw new Error(error.detail || "激活失败");
        }
        const data = await resp.json();
        appendMessage("system", `${data.saved ? "模板已保存并激活" : "临时模板已激活"}: ${data.domain} (${specId})`);
        if (data.saved) {
            await refreshSpecList(specId);
        } else {
            ensureTemporarySpecOption(specId, data.domain);
        }
        currentSpecId = specId;
        activeTemplateSpecId = specId.startsWith("template_") ? specId : "";
        updateTemplateFormatAction();
    } catch (err) {
        alert("激活规范出错: " + err.message);
    }
};

function ensureTemporarySpecOption(specId, domain) {
    let option = Array.from(specSelect.options).find(item => item.value === specId);
    if (!option) {
        option = document.createElement("option");
        option.value = specId;
        specSelect.appendChild(option);
    }
    option.textContent = `${domain}（仅本次）`;
    specSelect.value = specId;
    currentSpecId = specId;
    renderSpecDropdown();
}

// ---------- 可调宽度侧边栏 ----------
const SIDEBAR_MIN = 280;
const SIDEBAR_MAX = 720;

function clampSidebarWidth(width) {
    return Math.max(SIDEBAR_MIN, Math.min(SIDEBAR_MAX, window.innerWidth * 0.6, width));
}

function applySidebarWidth(width, persist = false) {
    const nextWidth = Math.round(clampSidebarWidth(width));
    document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
    sidebarResizer.setAttribute("aria-valuenow", String(nextWidth));
    if (persist) localStorage.setItem("doc_agent_sidebar_width", String(nextWidth));
}

function resizeFromPointer(event) {
    applySidebarWidth(window.innerWidth - event.clientX);
}

sidebarResizer.addEventListener("pointerdown", event => {
    sidebarResizer.setPointerCapture(event.pointerId);
    document.body.classList.add("resizing-sidebar");
    resizeFromPointer(event);
});
sidebarResizer.addEventListener("pointermove", event => {
    if (sidebarResizer.hasPointerCapture(event.pointerId)) resizeFromPointer(event);
});
sidebarResizer.addEventListener("pointerup", event => {
    if (sidebarResizer.hasPointerCapture(event.pointerId)) sidebarResizer.releasePointerCapture(event.pointerId);
    document.body.classList.remove("resizing-sidebar");
    const width = chatPanel.getBoundingClientRect().width;
    applySidebarWidth(width, true);
});
sidebarResizer.addEventListener("dblclick", () => applySidebarWidth(350, true));
sidebarResizer.addEventListener("keydown", event => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const direction = event.key === 'ArrowLeft' ? 1 : -1;
    applySidebarWidth(chatPanel.getBoundingClientRect().width + direction * 20, true);
});
window.addEventListener("resize", () => applySidebarWidth(chatPanel.getBoundingClientRect().width));

const savedSidebarWidth = Number(localStorage.getItem("doc_agent_sidebar_width"));
applySidebarWidth(Number.isFinite(savedSidebarWidth) && savedSidebarWidth > 0 ? savedSidebarWidth : 350);

function updateTemplateFormatAction() {
    const existing = document.getElementById("templateFormatAction");
    const selectedTemplate = currentSpecId.startsWith("template_");
    const ready = Boolean(currentDehydratedData && currentDocumentFileName && selectedTemplate);
    if (!ready) {
        if (existing) existing.remove();
        return;
    }
    if (existing) return;

    const action = document.createElement("div");
    action.id = "templateFormatAction";
    action.className = "template-format-action";
    action.innerHTML = `
        <span>模板和待排版文档已准备好</span>
        <button type="button" id="templateFormatBtn">按模板规范排版</button>
    `;
    chatMessages.appendChild(action);
    document.getElementById("templateFormatBtn").addEventListener("click", () => {
        startAgent("按当前已激活的模板规范对全文进行排版");
    });
    chatMessages.scrollTop = chatMessages.scrollHeight;
}

// ---------- 状态栏管理 ----------
function setStatus(label, variant = "active") {
    statusBar.textContent = label;
    statusBar.className = "status-bar " + variant;
    statusBar.style.display = "block";
}
function clearStatus() {
    statusBar.textContent = "";
    statusBar.className = "status-bar idle";
    statusBar.style.display = "none";
}

// ---------- SSE 解析 ----------
async function fetchSSE(url, body, onEvent) {
    const resp = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const errText = await resp.text();
        onEvent("error", { message: errText });
        return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop(); // last incomplete line
        let currentEvent = "";
        let currentData = "";
        for (const line of lines) {
            if (line.startsWith("event: ")) {
                currentEvent = line.slice(7).trim();
            } else if (line.startsWith("data: ")) {
                currentData = line.slice(6).trim();
            } else if (line === "" && currentEvent && currentData) {
                try {
                    onEvent(currentEvent, JSON.parse(currentData));
                } catch (e) {
                    onEvent("error", { message: "SSE parse error" });
                }
                currentEvent = "";
                currentData = "";
            }
        }
    }
}

// ---------- Agent 交互 ----------
sendBtn.addEventListener("click", startAgent);
userInput.addEventListener("keypress", (e) => {
    if (e.key === "Enter") startAgent();
});

async function startAgent(forcedMessage = "") {
    const apiKey = apiKeyInput.value.trim();
    const model = modelSelect.value;
    const message = typeof forcedMessage === "string" && forcedMessage
        ? forcedMessage
        : userInput.value.trim();
    if (!apiKey) { alert("请输入 API Key"); return; }
    if (!currentDehydratedData) { alert("请先上传文档"); return; }
    if (!message) return;

    appendMessage("user", message);
    userInput.value = "";
    const templateFormatBtn = document.getElementById("templateFormatBtn");
    if (templateFormatBtn) templateFormatBtn.disabled = true;
    setStatus("分析文档中...", "active");

    await fetchSSE("http://127.0.0.1:8000/api/agent/start", {
        api_key: apiKey,
        model: model,
        dehydrated_data: currentDehydratedData,
        message: message,
        spec_id: currentSpecId,
    }, (event, data) => {
        if (event === "state") {
            setStatus(data.label, "active");
        } else if (event === "interrupt") {
            currentThreadId = data.thread_id;
            setStatus(data.label, "review");
            showPlanForReview(data.plan);
        } else if (event === "result") {
            currentThreadId = null;
            setStatus(data.label, "success");
            showExecutionResult(data.execution_results);
            loadPreview();
        } else if (event === "error") {
            setStatus("出错了", "error");
            appendMessage("ai", "错误: " + data.message);
        }
    });
}

function showPlanForReview(plan) {
    const toolCalls = plan.tool_calls || [];
    if (!toolCalls.length) {
        appendMessage("ai", "AI 未返回任何排版规划。");
        return;
    }
    let html = `<b>AI 排版规划 (${toolCalls.length} 条操作):</b><br>`;
    toolCalls.forEach((call, idx) => {
        html += `<div style="margin:6px 0; padding:5px; background:#f0f0f0; border-radius:4px;">
            <b>${idx + 1}:</b> ${call.tool}<br>
            <b>参数:</b> ${JSON.stringify(call.arguments)}
        </div>`;
    });
    const msgId = appendMessage("ai", html);
    addReviewButtons(msgId);
}

function addReviewButtons(msgId) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    const actionsDiv = document.createElement("div");
    actionsDiv.className = "review-actions";

    const approveBtn = document.createElement("button");
    approveBtn.className = "approve-btn";
    approveBtn.textContent = "批准执行";
    approveBtn.addEventListener("click", () => approvePlan(msgId));

    const reviseBtn = document.createElement("button");
    reviseBtn.className = "revise-btn";
    reviseBtn.textContent = "提出修改";
    reviseBtn.addEventListener("click", () => showReviseInput(msgId));

    actionsDiv.appendChild(approveBtn);
    actionsDiv.appendChild(reviseBtn);
    msgDiv.appendChild(actionsDiv);
}

async function approvePlan(msgId) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    // 移除审核按钮
    const actions = msgDiv.querySelector(".review-actions");
    if (actions) actions.remove();
    const reviseArea = msgDiv.querySelector(".revise-input-area");
    if (reviseArea) reviseArea.remove();

    setStatus("执行排版中...", "active");

    await fetchSSE("http://127.0.0.1:8000/api/agent/continue", {
        thread_id: currentThreadId,
        decision: "approve",
        feedback: "",
    }, (event, data) => {
        if (event === "state") {
            setStatus(data.label, "active");
        } else if (event === "result") {
            currentThreadId = null;
            setStatus(data.label, "success");
            showExecutionResult(data.execution_results);
            loadPreview();
        } else if (event === "interrupt") {
            // 执行后又产生中断（不太可能，但安全处理）
            currentThreadId = data.thread_id;
            setStatus(data.label, "review");
            showPlanForReview(data.plan);
        } else if (event === "error") {
            setStatus("出错了", "error");
            appendMessage("ai", "错误: " + data.message);
        }
    });
}

function showReviseInput(msgId) {
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    // 隐藏审核按钮
    const actions = msgDiv.querySelector(".review-actions");
    if (actions) actions.style.display = "none";

    const areaDiv = document.createElement("div");
    areaDiv.className = "revise-input-area";

    const textarea = document.createElement("textarea");
    textarea.className = "revise-input";
    textarea.placeholder = "请输入修改意见...";
    textarea.rows = 3;

    const submitBtn = document.createElement("button");
    submitBtn.className = "revise-submit-btn";
    submitBtn.textContent = "提交修改意见";
    submitBtn.addEventListener("click", () => revisePlan(msgId, textarea.value));

    areaDiv.appendChild(textarea);
    areaDiv.appendChild(submitBtn);
    msgDiv.appendChild(areaDiv);
}

async function revisePlan(msgId, feedback) {
    if (!feedback.trim()) { alert("请输入修改意见"); return; }
    const msgDiv = document.getElementById(msgId);
    if (!msgDiv) return;
    const reviseArea = msgDiv.querySelector(".revise-input-area");
    if (reviseArea) reviseArea.remove();
    const actions = msgDiv.querySelector(".review-actions");
    if (actions) actions.remove();

    appendMessage("user", "修改意见: " + feedback);
    setStatus("重新规划中...", "active");

    await fetchSSE("http://127.0.0.1:8000/api/agent/continue", {
        thread_id: currentThreadId,
        decision: "revise",
        feedback: feedback,
    }, (event, data) => {
        if (event === "state") {
            setStatus(data.label, "active");
        } else if (event === "interrupt") {
            currentThreadId = data.thread_id;
            setStatus(data.label, "review");
            showPlanForReview(data.plan);
        } else if (event === "result") {
            currentThreadId = null;
            setStatus(data.label, "success");
            showExecutionResult(data.execution_results);
            loadPreview();
        } else if (event === "error") {
            setStatus("出错了", "error");
            appendMessage("ai", "错误: " + data.message);
        }
    });
}

function showExecutionResult(results) {
    if (!results || !results.length) {
        appendMessage("ai", "排版执行完成（无具体操作结果）");
        return;
    }
    const success = results.filter(r => r.status === "success").length;
    const total = results.length;
    let html = `<b>执行结果: ${success}/${total} 成功</b><br>`;
    results.forEach((r, idx) => {
        const icon = r.status === "success" ? "✓" : "✗";
        html += `<div style="margin:4px 0; padding:4px; background:#f8f8f8; border-radius:4px;">
            ${icon} <b>${r.tool}</b>: ${r.status}${r.reason ? " - " + r.reason : ""}
        </div>`;
    });
    appendMessage("ai", html);
}

// ---------- 消息辅助 ----------
function appendMessage(role, text) {
    const div = document.createElement("div");
    div.className = `message ${role}`;
    const id = "msg-" + Date.now() + "-" + Math.random().toString(36).substr(2, 5);
    div.id = id;
    div.innerHTML = text.replace(/\n/g, "<br>");
    chatMessages.appendChild(div);
    chatMessages.scrollTop = chatMessages.scrollHeight;
    return id;
}

function removeMessage(id) {
    const el = document.getElementById(id);
    if (el) el.remove();
}

// ---------- 下载 ----------
document.getElementById("downloadBtn").addEventListener("click", () => {
    const link = document.createElement("a");
    link.href = "http://127.0.0.1:8000/api/download-doc";
    link.download = "";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
});

// ---------- 预览 ----------
async function loadPreview() {
    const previewDiv = document.getElementById("docPreview");
    previewDiv.innerHTML = "加载预览中...";
    try {
        const resp = await fetch("http://127.0.0.1:8000/api/download-doc");
        const blob = await resp.blob();
        if (typeof docx !== "undefined" && docx.renderAsync) {
            await docx.renderAsync(blob, previewDiv);
        } else {
            previewDiv.innerHTML = `<p>预览功能需要 docx-preview 库支持</p>`;
        }
    } catch (error) {
        previewDiv.innerHTML = `<p style="color:red">预览失败: ${error.message}</p>`;
    }
}

// 页面打开时自动加载已保存模板，无需手动点击刷新。
refreshSpecList();
