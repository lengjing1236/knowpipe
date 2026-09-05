// 原生 JS：调用 /api/* 接口，驱动登录页与单页 Web 的最小交互（无前端框架）。

async function apiFetch(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    ...options,
  });
  const data = await resp.json().catch(() => ({}));
  return { ok: resp.ok, status: resp.status, data };
}

function setupLoginForm() {
  const form = document.getElementById("login-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const action = event.submitter ? event.submitter.dataset.action : "login";
    const username = document.getElementById("username").value;
    const password = document.getElementById("password").value;
    const path = action === "register" ? "/api/auth/register" : "/api/auth/login";
    const { ok, data } = await apiFetch(path, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    const message = document.getElementById("message");
    if (ok) {
      message.textContent = action === "register" ? "注册成功，请登录" : "登录成功";
      if (action === "login") window.location.href = "/";
    } else {
      message.textContent = "失败：" + (data.error || "未知错误");
    }
  });
}

async function loadTopics() {
  const list = document.getElementById("topics-list");
  if (!list) return;
  const { data } = await apiFetch("/api/topics");
  list.innerHTML = "";
  (data.topics || []).forEach((topic) => {
    const li = document.createElement("li");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.value = topic.topic_cluster_id;
    li.appendChild(checkbox);
    li.appendChild(document.createTextNode(
      ` #${topic.topic_cluster_id} (${topic.document_count} 篇): ${topic.top_keywords.join(", ")}`
    ));
    list.appendChild(li);
  });
}

function setupSaveTopics(userId) {
  const button = document.getElementById("save-topics");
  if (!button) return;
  button.addEventListener("click", async () => {
    const checked = Array.from(document.querySelectorAll("#topics-list input:checked"))
      .map((el) => el.value);
    await apiFetch("/api/profile/topics", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, known_topics: checked }),
    });
    loadRecommendations(userId);
  });
}

async function loadRecommendations(userId) {
  const list = document.getElementById("recommendations-list");
  if (!list) return;
  const source = document.getElementById("source-filter").value;
  const params = new URLSearchParams({ user_id: userId, limit: "20" });
  if (source) params.set("source", source);
  const { data } = await apiFetch(`/api/recommendations?${params.toString()}`);
  list.innerHTML = "";
  (data.items || []).forEach((item) => {
    const li = document.createElement("li");
    const doc = item.document || {};
    li.textContent = `[${item.status}] ${doc.title || doc.doc_id} — 命中关键词: ${
      (item.matched_keywords || []).join(", ") || "无"
    }`;
    list.appendChild(li);
  });
}

setupLoginForm();
if (document.getElementById("recommendations-section")) {
  // 演示环境的最小实现：从 URL 查询串取 user_id（真实登录态由服务端 session 维护）。
  const userId = new URLSearchParams(window.location.search).get("user_id");
  if (userId) {
    loadTopics().then(() => setupSaveTopics(userId));
    loadRecommendations(userId);
    document.getElementById("source-filter").addEventListener("change", () => loadRecommendations(userId));
  }
}
