"use strict";
const BASE_PATH = new URL(document.currentScript.src).pathname.replace(/\/static\/app\.js$/, "");
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const labels = {
  draft: "Rascunho",
  in_review: "Em aprovação",
  rejected: "Devolvido",
  approved: "Aprovado",
  sending: "Enviando",
  signature_pending: "Em assinatura",
  signed: "Assinado",
  signature_declined: "Assinatura recusada",
  archived: "Arquivado",
};
const roles = {
  admin: "Administrador",
  editor: "Editor",
  reviewer: "Revisor",
  viewer: "Leitor",
};
const actionLabels = {
  "contract.created": "Contrato criado",
  "contract.updated": "Documento atualizado",
  "contract.submit": "Enviado para aprovação",
  "contract.approve": "Etapa aprovada",
  "contract.reject": "Devolvido para ajustes",
  "contract.archive": "Contrato arquivado",
  "signature.sent": "Enviado ao DocuSign",
  "signature.completed": "Assinatura concluída",
  "signature.transfer_authorized": "Envio ao DocuSign autorizado",
  "signature.declined": "Assinatura recusada",
  "signature.voided": "Envelope cancelado",
  "signature.reconciled": "Envelope conciliado",
  "ai.requested": "Análise com IA local",
};
let state = {
  user: null,
  view: "contracts",
  templates: [],
  workflows: [],
  users: [],
  capabilities: {},
  docusign: {},
  offset: 0,
  query: "",
  status: "",
  contract: null,
};
let toastTimer;
const modal = $("#modal");
const fmtDate = (date) =>
  new Intl.DateTimeFormat("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(date + (date.endsWith("Z") ? "" : "Z")));
const badge = (status) =>
  `<span class="badge ${escape(status)}">${escape(labels[status] || status)}</span>`;
const canEdit = () => ["admin", "editor"].includes(state.user?.role);
function errorText(data) {
  const d = data.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d))
    return d.map((v) => `${v.loc.slice(1).join(" · ")}: ${v.msg}`).join("; ");
  return d?.fields
    ? Object.entries(d.fields)
        .map(([k, v]) => `${k}: ${v}`)
        .join("; ")
    : "Não foi possível concluir a operação.";
}
async function api(path, options = {}) {
  const response = await fetch(`${BASE_PATH}/api/v1${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-OpenCLM": "1",
      ...options.headers,
    },
    credentials: "same-origin",
  });
  if (response.status === 204) return null;
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && state.user && path !== "/auth/login") {
      state.user = null;
      modal.close();
      loginScreen();
    }
    throw new Error(errorText(data));
  }
  return data;
}
const post = (path, data) =>
  api(path, { method: "POST", body: JSON.stringify(data) });
function toast(message) {
  clearTimeout(toastTimer);
  $("#toast").textContent = message;
  $("#toast").hidden = false;
  toastTimer = setTimeout(() => ($("#toast").hidden = true), 5000);
}
function openModal(title, subtitle, content, detail = false) {
  modal.className = detail ? "detail" : "";
  modal.innerHTML = `<div class="modal-header"><div><h2 id="modal-title">${escape(title)}</h2><p>${escape(subtitle)}</p></div><button class="icon-button" data-close aria-label="Fechar">✕</button></div><div class="modal-body">${content}</div>`;
  $("[data-close]", modal).onclick = () => modal.close();
  if (!modal.open) modal.showModal();
}
function bindForm(selector, callback) {
  const form = $(selector);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("button[type=submit]", form);
    const error = $(".error", form);
    if (error) error.textContent = "";
    button.disabled = true;
    try {
      await callback(new FormData(form), form);
    } catch (e) {
      if (error) error.textContent = e.message;
      else toast(e.message);
    } finally {
      button.disabled = false;
    }
  });
}
function loginScreen() {
  $("#app").innerHTML =
    `<div class="login"><section class="login-story"><div class="brand"><img src="${BASE_PATH}/static/icon.svg" alt="">open<em>clm</em></div><div><div class="eyebrow">Livre por princípio. Seu por completo.</div><h1>Seus contratos.<br>Sua infraestrutura.<br>Seu controle.</h1><p>Do primeiro formulário à última assinatura, um espaço para cuidar de todo o ciclo dos seus contratos.</p></div><div class="small">Código aberto · Hospedagem própria · IA local</div></section><section class="login-form"><div class="login-box"><div class="eyebrow">Bem-vindo ao OpenCLM</div><h2>Acesse seu espaço</h2><p>Use a conta criada pelo administrador da sua instalação.</p><form id="login"><label>E-mail<input name="email" type="email" autocomplete="username" required placeholder="voce@suaempresa.com"></label><label>Senha<input name="password" type="password" autocomplete="current-password" required></label><div class="error" role="alert"></div><button class="button primary" type="submit">Entrar no OpenCLM <span>→</span></button></form><div class="notice">Primeiro acesso? Crie uma conta no servidor seguindo o <a href="${BASE_PATH}/docs">guia da API</a> e o README do projeto.</div></div></section></div>`;
  bindForm("#login", async (data) => {
    state.user = await post("/auth/login", Object.fromEntries(data));
    await loadWorkspace();
  });
}
async function loadWorkspace() {
  [
    state.templates,
    state.workflows,
    state.users,
    state.capabilities,
    state.docusign,
  ] = await Promise.all([
    api("/templates"),
    api("/workflows"),
    api("/users"),
    api("/settings/capabilities"),
    api("/integrations/docusign/status"),
  ]);
  const params = new URLSearchParams(location.search);
  const outcome = params.get("docusign") || params.get("salesforce");
  if (outcome) {
    state.view = "settings";
    history.replaceState(null, "", "/");
  }
  shell();
  await renderView();
  if (outcome) {
    const messages = {
      connected: "Conta conectada. Sua integração está pronta.",
      cancelled: "Conexão cancelada. Você pode tentar novamente.",
      failed:
        "Não foi possível conectar. Tente novamente ou consulte o administrador.",
      account_mismatch:
        "Há envelopes pendentes. Reconecte a conta DocuSign original.",
    };
    toast(messages[outcome] || "Conexão atualizada.");
  }
}
function shell() {
  const menu = [
    ["contracts", "▤", "Contratos"],
    ["templates", "▧", "Modelos e formulários"],
    ["workflows", "⇄", "Workflows"],
    ["settings", "⚙", "Configurações"],
  ];
  $("#app").innerHTML =
    `<div class="shell"><aside class="sidebar" id="openclm-sidebar"><a class="brand" href="${BASE_PATH}/" aria-label="OpenCLM início"><img src="${BASE_PATH}/static/icon.svg" alt="">open<em>clm</em></a><div class="side-label">ESPAÇO DE TRABALHO</div><nav class="nav" aria-label="Navegação principal">${menu.map(([key, icon, name]) => `<button data-view="${key}" class="${state.view === key ? "active" : ""}"><span class="nav-icon">${icon}</span>${name}</button>`).join("")}</nav><div class="side-bottom"><div class="privacy-note"><strong>◈ Seus dados ficam com você.</strong>Hospedado na sua infraestrutura.<br>Sem telemetria de uso.</div><div class="profile"><span class="avatar">${escape(state.user.name.slice(0, 2).toUpperCase())}</span><div class="profile-info"><strong>${escape(state.user.name)}</strong><span class="muted small">${roles[state.user.role]}</span></div><button class="icon-button" id="logout" aria-label="Sair da conta" title="Sair">↪</button></div></div></aside><main class="main"><header class="topbar"><div class="topbar-start"><button class="icon-button sidebar-toggle" id="sidebar-toggle" type="button" aria-controls="openclm-sidebar" aria-expanded="true" aria-label="Recolher menu lateral" title="Recolher menu lateral">☰</button><div class="breadcrumb">Workspace <span>/ &nbsp; <b id="crumb">Contratos</b></span></div></div><div class="top-right"><span class="local-badge"><i class="dot"></i> Instância própria</span><a href="${BASE_PATH}/docs" target="_blank" rel="noopener">API ↗</a><button class="icon-button mobile-logout" id="top-logout" aria-label="Encerrar sessão">Sair</button></div></header><div class="content" id="content"></div></main></div>`;
  const sidebarToggle = $("#sidebar-toggle");
  function setSidebar(collapsed) {
    $(".shell").classList.toggle("sidebar-collapsed", collapsed);
    sidebarToggle.setAttribute("aria-expanded", String(!collapsed));
    sidebarToggle.setAttribute("aria-label", collapsed ? "Expandir menu lateral" : "Recolher menu lateral");
    sidebarToggle.title = sidebarToggle.getAttribute("aria-label");
    try { localStorage.setItem("openclm-sidebar-collapsed", collapsed ? "1" : "0"); } catch {}
  }
  try { setSidebar(localStorage.getItem("openclm-sidebar-collapsed") === "1"); } catch {}
  sidebarToggle.onclick = () => setSidebar(!$(".shell").classList.contains("sidebar-collapsed"));
  $$("[data-view]").forEach(
    (button) =>
      (button.onclick = async () => {
        state.view = button.dataset.view;
        state.offset = 0;
        $$("[data-view]").forEach((b) =>
          b.classList.toggle("active", b === button),
        );
        await safeRender();
      }),
  );
  const logout = async () => {
    try {
      await post("/auth/logout", {});
      state.user = null;
      loginScreen();
    } catch (e) {
      toast(e.message);
    }
  };
  $("#logout").onclick = logout;
  $("#top-logout").onclick = logout;
}
async function safeRender() {
  try {
    await renderView();
  } catch (e) {
    toast(e.message);
  }
}
async function renderView() {
  const titles = {
    contracts: "Contratos",
    templates: "Modelos e formulários",
    workflows: "Workflows",
    settings: "Configurações",
  };
  $("#crumb").textContent = titles[state.view];
  if (state.view === "contracts") await contractsPage();
  if (state.view === "templates") templatesPage();
  if (state.view === "workflows") workflowsPage();
  if (state.view === "settings") await settingsPage();
}
const footer = () =>
  `<footer class="footer"><span>openclm · Código aberto, controle de ponta a ponta.</span><span>v0.1.0 · Base inicial</span></footer>`;
async function contractsPage() {
  const [page, stats] = await Promise.all([
    api(
      `/contracts?${new URLSearchParams({ q: state.query, limit: 10, offset: state.offset, ...(state.status ? { status: state.status } : {}) })}`,
    ),
    api("/dashboard"),
  ]);
  const counts = stats.by_status;
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">Visão geral</div><h1>Contratos sob controle.</h1><p>Acompanhe cada etapa, do rascunho à assinatura.</p></div>${canEdit() ? '<button class="button primary" id="new-contract">＋ Novo contrato</button>' : ""}</div><section class="banner"><div><div class="eyebrow">Sua operação, em um só lugar</div><h2>Menos tarefas repetidas. Mais clareza.</h2><p>Transforme respostas em documentos e acompanhe as aprovações.<br>Você decide quando conectar serviços externos.</p></div><div class="banner-art" aria-hidden="true">▤</div></section><section class="metrics" aria-label="Indicadores">${[
      ["Todos os contratos", stats.total, "Na sua base de contratos", "▤"],
      [
        "Em aprovação",
        counts.in_review || 0,
        "Aguardando revisão interna",
        "◷",
      ],
      [
        "Em assinatura",
        (counts.signature_pending || 0) + (counts.sending || 0),
        "Acompanhamento de assinaturas",
        "⌁",
      ],
      ["Assinados", counts.signed || 0, "Ciclo de assinatura concluído", "✓"],
    ]
      .map(
        ([label, value, caption, icon]) =>
          `<div class="metric"><div class="metric-label">${label}<span class="metric-icon">${icon}</span></div><div class="metric-number">${value}</div><div class="metric-caption">${caption}</div></div>`,
      )
      .join(
        "",
      )}</section><section class="panel"><div class="panel-header"><h2>Seus contratos <span class="count">${page.total}</span></h2><form class="toolbar" id="search"><input name="q" aria-label="Buscar contratos" placeholder="Buscar contrato ou contraparte…" value="${escape(state.query)}"><select name="status" aria-label="Filtrar por status"><option value="">Todos os status</option>${Object.entries(
      labels,
    )
      .map(
        ([key, label]) =>
          `<option value="${key}" ${state.status === key ? "selected" : ""}>${label}</option>`,
      )
      .join(
        "",
      )}</select><button class="button small" type="submit">Filtrar</button></form></div>${page.items.length ? `<div class="scroll-table"><table><thead><tr><th>Contrato</th><th>Contraparte</th><th>Status</th><th>Atualizado</th><th></th></tr></thead><tbody>${page.items.map((c) => `<tr><td><div class="contract-name"><span class="doc-icon">▤</span><button class="text-link" data-contract="${c.id}">${escape(c.title)}<span class="subline">${escape(state.templates.find((t) => t.id === c.template_id)?.name || "Modelo")} · v${c.version}</span></button></div></td><td class="muted">${escape(c.counterparty)}</td><td>${badge(c.status)}</td><td class="muted small">${fmtDate(c.updated_at)}</td><td><button class="icon-button" data-contract="${c.id}" aria-label="Abrir ${escape(c.title)}">↗</button></td></tr>`).join("")}</tbody></table></div>` : `<div class="empty"><span class="empty-mark">▤</span><h3>${state.query || state.status ? "Nenhum contrato encontrado" : "Seu primeiro contrato começa aqui"}</h3><p>${state.query || state.status ? "Experimente outro termo ou filtro." : "Escolha um modelo, responda ao formulário e gere o documento."}</p>${canEdit() && !state.query && !state.status ? '<button class="button" id="empty-new">Criar primeiro contrato →</button>' : ""}</div>`}<div class="panel-footer"><span>${page.total ? `${state.offset + 1}–${state.offset + page.items.length} de ${page.total} contratos` : "Nenhum contrato na seleção"}</span><div class="inline-actions"><button class="button small" id="previous" ${state.offset === 0 ? "disabled" : ""}>← Anterior</button><button class="button small" id="next" ${state.offset + page.items.length >= page.total ? "disabled" : ""}>Próxima →</button></div></div></section>${footer()}`;
  if ($("#new-contract")) $("#new-contract").onclick = () => contractForm();
  if ($("#empty-new")) $("#empty-new").onclick = () => contractForm();
  $$("[data-contract]").forEach(
    (b) =>
      (b.onclick = () =>
        showContract(b.dataset.contract).catch((e) => toast(e.message))),
  );
  $("#search").onsubmit = (event) => {
    event.preventDefault();
    const data = new FormData(event.target);
    state.query = data.get("q");
    state.status = data.get("status");
    state.offset = 0;
    safeRender();
  };
  $("#previous").onclick = () => {
    state.offset = Math.max(0, state.offset - 10);
    safeRender();
  };
  $("#next").onclick = () => {
    state.offset += 10;
    safeRender();
  };
}
function questionInput(q, value) {
  const required = q.required ? "required" : "";
  const attrs = `name="answer:${escape(q.key)}" ${required}`;
  let input;
  if (q.type === "textarea")
    input = `<textarea ${attrs}>${escape(value)}</textarea>`;
  else if (q.type === "select" || q.type === "boolean") {
    const options =
      q.type === "boolean"
        ? [
            ["true", "Sim"],
            ["false", "Não"],
          ]
        : q.options.map((o) => [o, o]);
    input = `<select ${attrs}><option value="">Selecione…</option>${options.map(([v, l]) => `<option value="${escape(v)}" ${String(value) === v ? "selected" : ""}>${escape(l)}</option>`).join("")}</select>`;
  } else
    input = `<input ${attrs} type="${q.type === "number" ? "number" : q.type === "date" ? "date" : q.type === "email" ? "email" : "text"}" ${q.type === "number" ? 'step="any"' : ""} value="${escape(value)}">`;
  return `<label class="${q.type === "textarea" ? "wide" : ""}">${escape(q.label)}${q.required ? " *" : ""}${input}${q.help_text ? `<span class="field-help">${escape(q.help_text)}</span>` : ""}</label>`;
}
function contractForm(templateId = null, existing = null) {
  if (!state.templates.length) {
    toast("Crie um modelo em Modelos e formulários para começar.");
    return;
  }
  templateId = existing?.template_id || templateId || state.templates[0].id;
  openModal(
    existing ? "Editar contrato" : "Novo contrato",
    existing
      ? "Ao salvar, uma nova versão será registrada."
      : "Preencha as informações. O documento será gerado no seu servidor.",
    `<form id="contract-form"><div class="form-grid"><label class="wide">Modelo<select id="choose-template" name="template_id" ${existing ? "disabled" : ""}>${state.templates.map((t) => `<option value="${t.id}" ${t.id === templateId ? "selected" : ""}>${escape(t.name)}</option>`).join("")}</select></label><label>Título do contrato<input name="title" required maxlength="200" placeholder="Ex.: Serviços de design · Acme" value="${escape(existing?.title)}"></label><label>Contraparte<input name="counterparty" required maxlength="200" placeholder="Pessoa ou empresa" value="${escape(existing?.counterparty)}"></label></div><div class="section-label">Formulário do documento</div><div class="form-grid" id="questions"></div><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">${existing ? "Salvar nova versão" : "Gerar contrato"} →</button></div></form>`,
  );
  const fill = () => {
    $("#questions").innerHTML = state.templates
      .find((t) => t.id === $("#choose-template").value)
      .questions.map((q) => questionInput(q, existing?.answers[q.key]))
      .join("");
  };
  fill();
  $("#choose-template").onchange = fill;
  bindForm("#contract-form", async (data) => {
    const tid = $("#choose-template").value;
    const answers = {};
    for (const q of state.templates.find((t) => t.id === tid).questions) {
      const v = data.get(`answer:${q.key}`);
      answers[q.key] =
        v === ""
          ? null
          : q.type === "number"
            ? Number(v)
            : q.type === "boolean"
              ? v === "true"
              : v;
    }
    const payload = {
      title: data.get("title"),
      counterparty: data.get("counterparty"),
      answers,
    };
    const result = existing
      ? await api(`/contracts/${existing.id}`, {
          method: "PATCH",
          body: JSON.stringify({ ...payload, revision: existing.revision }),
        })
      : await post("/contracts", { ...payload, template_id: tid });
    toast(
      existing ? "Nova versão registrada." : "Contrato gerado com sucesso.",
    );
    await showContract(result.id);
    await safeRender();
  });
}
async function showContract(id) {
  const [c, events, versions, salesforce] = await Promise.all([
    api(`/contracts/${id}`),
    api(`/audit?contract_id=${id}`),
    api(`/contracts/${id}/versions`),
    api(`/integrations/salesforce/contracts/${id}`),
  ]);
  state.contract = c;
  const owner = state.users.find((u) => u.id === c.owner_id);
  const writable =
    canEdit() && (state.user.role === "admin" || c.owner_id === state.user.id);
  const step = c.workflow_snapshot[c.current_step];
  const reviewer =
    ["admin", "reviewer"].includes(state.user.role) &&
    state.user.id !== c.owner_id &&
    (!step?.approver_id || step.approver_id === state.user.id);
  openModal(
    c.title,
    `${c.counterparty} · Versão ${c.version} · ${owner?.name || "Responsável"}`,
    `<div class="detail-tools">${badge(c.status)}<a class="button small" href="${BASE_PATH}/api/v1/contracts/${c.id}/document">↓ Baixar DOCX</a><a class="button small" href="${BASE_PATH}/api/v1/contracts/${c.id}/document?format=txt">↓ Texto</a>${writable && ["draft", "rejected"].includes(c.status) ? '<button class="button small" id="edit-contract">Editar formulário</button>' : ""}${writable && c.status === "draft" ? '<button class="button primary small" data-transition="submit">Enviar para aprovação →</button>' : ""}${reviewer && c.status === "in_review" ? '<button class="button primary small" data-transition="approve">Aprovar etapa ✓</button><button class="button danger small" id="reject-contract">Devolver</button>' : ""}${writable && c.status === "approved" ? `<button class="button primary small" id="sign-contract" ${state.capabilities.docusign_enabled && state.docusign.connected ? "" : "disabled"}>Enviar ao DocuSign ↗</button>` : ""}${writable && ["draft", "rejected", "approved", "signed", "signature_declined"].includes(c.status) ? '<button class="button small" data-transition="archive">Arquivar</button>' : ""}</div><div class="detail-grid"><div><div class="document-preview">${escape(c.content)}</div><div class="section-label">Versões do documento</div><div class="inline-actions">${versions.map((v) => `<a class="button small" href="${BASE_PATH}/api/v1/contracts/${c.id}/document?version=${v.version}" title="SHA-256: ${v.sha256}">↓ v${v.version} · ${fmtDate(v.created_at)}</a>`).join("")}</div></div><aside><h3>Salesforce</h3>${salesforce.link ? `<a href="${escape(salesforce.link.url)}" target="_blank" rel="noopener">${escape(salesforce.link.name)} ↗</a>` : '<p class="small muted">Sem oportunidade vinculada.</p>'}${writable ? '<button class="button small" id="link-salesforce">Vincular oportunidade</button>' : ""}<h3>Fluxo de aprovação</h3><ol class="steps">${c.workflow_snapshot.map((s, i) => `<li><span class="step-number ${i < c.current_step ? "done" : ""}">${i < c.current_step ? "✓" : i + 1}</span><div>${escape(s.name)}<span class="subline">${escape(state.users.find((u) => u.id === s.approver_id)?.name || "Revisor diferente do autor")}</span></div></li>`).join("")}</ol>${c.status === "approved" && !state.docusign.connected ? '<div class="notice">Aprovado internamente. Conecte sua conta DocuSign em Configurações para enviar à assinatura.</div>' : ""}<div class="section-label">Assistente local</div><div class="inline-actions"><button class="button small" data-ai="summary" ${state.capabilities.ai_enabled ? "" : "disabled"}>Resumir</button><button class="button small" data-ai="risks" ${state.capabilities.ai_enabled ? "" : "disabled"}>Pontos de atenção</button><button class="button small" data-ai="questions" ${state.capabilities.ai_enabled ? "" : "disabled"}>Perguntas</button></div><p class="small muted">${state.capabilities.ai_enabled ? "Processamento local. Revise as sugestões antes de utilizá-las." : "Configure o Ollama no servidor para ativar a IA local."}</p><div id="ai-result"></div><details><summary class="section-label">Respostas do formulário</summary><dl class="answer-list">${
      state.templates
        .find((t) => t.id === c.template_id)
        ?.questions.map(
          (q) =>
            `<dt>${escape(q.label)}</dt><dd>${escape(c.answers[q.key] === true ? "Sim" : c.answers[q.key] === false ? "Não" : (c.answers[q.key] ?? "—"))}</dd>`,
        )
        .join("") || ""
    }</dl></details><div class="section-label">Histórico recente</div><ul class="audit-list">${events.map((e) => `<li>${escape(actionLabels[e.action] || e.action)}<time>${fmtDate(e.created_at)} · ${escape(state.users.find((u) => u.id === e.actor_id)?.name || "Sistema")}</time>${e.details.comment ? `<p>${escape(e.details.comment)}</p>` : ""}</li>`).join("")}</ul></aside></div>`,
    true,
  );
  if ($("#link-salesforce")) $("#link-salesforce").onclick = () => salesforcePicker(c.id);
  if ($("#edit-contract"))
    $("#edit-contract").onclick = () => contractForm(c.template_id, c);
  const transition = async (action, comment = "") => {
    await post(`/contracts/${c.id}/transitions`, {
      action,
      comment,
      revision: c.revision,
    });
    toast("Contrato atualizado.");
    await showContract(c.id);
    await safeRender();
  };
  $$("[data-transition]", modal).forEach(
    (b) =>
      (b.onclick = async () => {
        b.disabled = true;
        try {
          await transition(b.dataset.transition);
        } catch (e) {
          toast(e.message);
          b.disabled = false;
        }
      }),
  );
  if ($("#reject-contract"))
    $("#reject-contract").onclick = () => {
      openModal(
        "Devolver para ajustes",
        "Explique ao autor o que precisa ser corrigido.",
        '<form id="reject"><label>Motivo da devolução<textarea name="comment" required maxlength="2000"></textarea></label><div class="error" role="alert"></div><div class="form-actions"><button class="button danger" type="submit">Devolver contrato</button></div></form>',
      );
      bindForm("#reject", async (data) =>
        transition("reject", data.get("comment")),
      );
    };
  if ($("#sign-contract")) $("#sign-contract").onclick = () => signatureForm(c);
  $$("[data-ai]", modal).forEach(
    (b) =>
      (b.onclick = async () => {
        const buttons = $$("[data-ai]", modal);
        buttons.forEach((x) => (x.disabled = true));
        $("#ai-result").textContent = "Analisando no seu servidor…";
        try {
          const r = await post(`/contracts/${c.id}/ai`, { task: b.dataset.ai });
          $("#ai-result").innerHTML =
            `<div class="ai-output">${escape(r.text)}</div>`;
        } catch (e) {
          $("#ai-result").textContent = e.message;
        } finally {
          buttons.forEach((x) => (x.disabled = false));
        }
      }),
  );
}
function signatureForm(c) {
  openModal(
    "Enviar para assinatura",
    "Assinatura eletrônica pelo DocuSign.",
    `<form id="signature"><div class="notice warn">O documento e os nomes/e-mails abaixo serão enviados ao DocuSign, um serviço externo. Os signatários receberão um e-mail para assinar, na ordem definida.</div><div id="signers"></div><button type="button" class="button small" id="add-signer">＋ Signatário</button><label class="checkbox"><input type="checkbox" name="consent" required>Autorizo o envio deste documento e dos dados dos signatários ao DocuSign.</label><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">Enviar documento ↗</button></div></form>`,
  );
  let count = 0;
  const add = () => {
    if (count >= 10) return;
    count++;
    $("#signers").insertAdjacentHTML(
      "beforeend",
      `<div class="form-grid"><label>Nome do signatário ${count}<input name="name${count}" required maxlength="120"></label><label>E-mail ${count}<input type="email" name="email${count}" required></label></div>`,
    );
  };
  add();
  $("#add-signer").onclick = add;
  bindForm("#signature", async (data) => {
    const signers = Array.from({ length: count }, (_, i) => ({
      name: data.get(`name${i + 1}`),
      email: data.get(`email${i + 1}`),
    }));
    await post(`/contracts/${c.id}/signature`, {
      revision: c.revision,
      signers,
      consent_to_external_transfer: true,
    });
    toast("Documento enviado ao DocuSign.");
    await showContract(c.id);
    await safeRender();
  });
}
function templatesPage() {
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">Crie uma vez. Use sempre.</div><h1>Modelos e formulários</h1><p>Perguntas estruturadas que se transformam em documentos.</p></div>${canEdit() ? '<button class="button primary" id="new-template">＋ Novo modelo</button>' : ""}</div><div class="cards">${state.templates.map((t) => `<article class="card"><div class="card-top"><span class="doc-icon">▧</span><span class="badge">${t.questions.length} perguntas</span></div><h3>${escape(t.name)}</h3><p>${escape(t.description)}</p><div class="small muted">⇄ ${escape(state.workflows.find((w) => w.id === t.workflow_id)?.name || "Workflow")}</div><div class="card-bottom"><span class="small muted">${fmtDate(t.created_at)}</span>${canEdit() ? `<button class="button small" data-use-template="${t.id}">Usar modelo →</button>` : ""}</div></article>`).join("") || '<div class="empty">Nenhum modelo cadastrado. Crie um modelo ou execute o seed no servidor.</div>'}</div><div class="notice">Os modelos são preservados após a criação. Para mudar perguntas ou cláusulas, crie um novo modelo. Os contratos existentes mantêm sua referência original.</div>${footer()}`;
  if ($("#new-template")) $("#new-template").onclick = templateForm;
  $$("[data-use-template]").forEach(
    (b) => (b.onclick = () => contractForm(b.dataset.useTemplate)),
  );
}
function templateForm() {
  if (!state.workflows.length) {
    toast("Crie primeiro um workflow de aprovação.");
    return;
  }
  openModal(
    "Novo modelo de documento",
    "Vincule as perguntas aos campos do documento.",
    `<form id="template-form"><label>Nome do modelo<input name="name" required maxlength="120"></label><label>Descrição<input name="description" maxlength="2000"></label><label>Workflow<select name="workflow_id">${state.workflows.map((w) => `<option value="${w.id}">${escape(w.name)}</option>`).join("")}</select></label><div class="section-label">Perguntas do formulário</div><div id="question-rows"></div><button class="button small" type="button" id="add-question">＋ Adicionar pergunta</button><label class="wide">Texto do documento<textarea name="body" rows="9" required maxlength="100000" placeholder="Ex.: A empresa {{empresa}} contrata os serviços descritos neste documento."></textarea><span class="field-help">Use {{chave_da_pergunta}} para inserir uma resposta. Apenas texto e campos simples são aceitos.</span></label><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">Criar modelo</button></div></form>`,
  );
  const add = () => {
    if ($$(".question-row").length >= 100) return;
    $("#question-rows").insertAdjacentHTML(
      "beforeend",
      `<div class="question-row"><div class="row-heading"><strong>Pergunta</strong><button class="icon-button remove-row" type="button" aria-label="Remover pergunta">✕</button></div><div class="compact-grid"><label>Chave<input data-field="key" placeholder="empresa" pattern="[a-z][a-z0-9_]{0,63}" required></label><label>Pergunta<input data-field="label" placeholder="Qual é o nome da empresa?" maxlength="200" required></label><label>Tipo<select data-field="type">${[
        ["text", "Texto curto"],
        ["textarea", "Texto longo"],
        ["number", "Número"],
        ["date", "Data"],
        ["email", "E-mail"],
        ["select", "Lista de opções"],
        ["boolean", "Sim / Não"],
      ]
        .map(([v, l]) => `<option value="${v}">${l}</option>`)
        .join(
          "",
        )}</select></label><label>Opções (para lista, uma por linha)<textarea data-field="options" rows="2"></textarea></label></div><label class="checkbox"><input data-field="required" type="checkbox" checked>Resposta obrigatória</label></div>`,
    );
    $$(".remove-row").forEach(
      (b) => (b.onclick = () => b.closest(".question-row").remove()),
    );
  };
  add();
  $("#add-question").onclick = add;
  bindForm("#template-form", async (data) => {
    const questions = $$(".question-row").map((row) => {
      const field = (k) => $(`[data-field="${k}"]`, row);
      return {
        key: field("key").value,
        label: field("label").value,
        type: field("type").value,
        required: field("required").checked,
        options:
          field("type").value === "select"
            ? field("options")
                .value.split("\n")
                .map((v) => v.trim())
                .filter(Boolean)
            : [],
      };
    });
    if (!questions.length) throw new Error("Adicione ao menos uma pergunta.");
    const template = await post("/templates", {
      ...Object.fromEntries(data),
      questions,
    });
    state.templates.push(template);
    modal.close();
    templatesPage();
    toast("Modelo e formulário criados.");
  });
}
function workflowsPage() {
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">Cada decisão, no seu lugar</div><h1>Workflows</h1><p>Defina quem aprova e em que ordem.</p></div>${state.user.role === "admin" ? '<button class="button primary" id="new-workflow">＋ Novo workflow</button>' : ""}</div><div class="cards">${state.workflows.map((w) => `<article class="card"><span class="badge">${w.steps.length} etapa${w.steps.length > 1 ? "s" : ""}</span><h3>${escape(w.name)}</h3><ol class="steps">${w.steps.map((s, i) => `<li><span class="step-number">${i + 1}</span><div>${escape(s.name)}<span class="subline">${escape(state.users.find((u) => u.id === s.approver_id)?.name || "Qualquer revisor, exceto o autor")}</span></div></li>`).join("")}</ol><div class="card-bottom"><span class="small muted">Sequencial · Aprovação humana</span></div></article>`).join("") || '<div class="empty">Nenhum workflow cadastrado.</div>'}</div><div class="notice">O contrato guarda uma cópia do fluxo escolhido. Cada etapa deve ser aprovada por um revisor autorizado diferente do autor. Devoluções exigem um motivo; a edição cria uma nova versão e reinicia a aprovação.</div>${footer()}`;
  if ($("#new-workflow")) $("#new-workflow").onclick = workflowForm;
}
function workflowForm() {
  openModal(
    "Novo workflow",
    "As etapas serão executadas na ordem abaixo.",
    '<form id="workflow-form"><label>Nome do workflow<input name="name" required maxlength="120"></label><div id="steps"></div><button class="button small" id="add-step" type="button">＋ Adicionar etapa</button><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">Criar workflow</button></div></form>',
  );
  const add = () => {
    if ($$(".step-row").length >= 20) return;
    $("#steps").insertAdjacentHTML(
      "beforeend",
      `<div class="step-row"><div class="compact-grid"><label>Nome da etapa<input data-step="name" required maxlength="120" placeholder="Ex.: Revisão jurídica"></label><label>Aprovador<select data-step="approver_id"><option value="">Qualquer revisor, exceto o autor</option>${state.users
        .filter((u) => ["admin", "reviewer"].includes(u.role))
        .map((u) => `<option value="${u.id}">${escape(u.name)}</option>`)
        .join(
          "",
        )}</select></label></div><button type="button" class="button small remove-step">Remover etapa</button></div>`,
    );
    $$(".remove-step").forEach(
      (b) => (b.onclick = () => b.closest(".step-row").remove()),
    );
  };
  add();
  $("#add-step").onclick = add;
  bindForm("#workflow-form", async (data) => {
    const steps = $$(".step-row").map((row) => ({
      name: $('[data-step="name"]', row).value,
      approver_id: $('[data-step="approver_id"]', row).value || null,
    }));
    const workflow = await post("/workflows", {
      name: data.get("name"),
      steps,
    });
    state.workflows.push(workflow);
    modal.close();
    workflowsPage();
    toast("Workflow criado.");
  });
}
async function settingsPage() {
  const keys = await api("/auth/api-keys");
  const sf = await api("/integrations/salesforce/status");
  const cap = state.capabilities;
  $("#content").innerHTML =
    `<div class="page-heading"><div><div class="eyebrow">Sua instância</div><h1>Configurações</h1><p>Integrações opcionais. Controle explícito sobre seus dados.</p></div></div><div class="cards"><section class="card"><h3>Salesforce</h3><p>Vincule oportunidades aos contratos e consulte sua origem comercial.</p><p class="small">${sf.connected ? escape(sf.instance_url) : sf.configured ? "Pronto para conectar sua conta." : "Aguardando configuração do aplicativo pelo administrador."}</p>${canEdit() ? `<button class="button" id="connect-salesforce" ${sf.configured ? "" : "disabled"}>${sf.connected ? "Reconectar" : "Conectar Salesforce"}</button>` : ""}${sf.connected ? '<button class="button small" id="disconnect-salesforce">Desconectar</button>' : ""}<p class="small muted">Busca as 25 oportunidades mais recentes correspondentes ao nome. Não altera registros no Salesforce.</p></section><section class="card"><div class="card-top"><span class="badge">Local</span><span class="small muted">${cap.ai_enabled ? "Habilitada" : "Desativada"}</span></div><h3>Assistente com Ollama</h3><p>Resuma documentos, encontre pontos de atenção e sugira perguntas com um modelo no seu servidor.</p><div class="notice">${cap.ai_enabled ? `Modelo configurado: ${escape(cap.ai_model)}. O serviço precisa estar disponível e o modelo instalado.` : "Defina AI_ENABLED=true e o endereço do Ollama no arquivo de configuração. Baixe um modelo local antes de usar."}</div><a class="small" href="${BASE_PATH}/docs">Consultar API →</a></section><section class="card"><div class="card-top"><span class="badge">Serviço externo</span><span class="small muted">${state.docusign.connected ? "Conectado" : cap.docusign_enabled ? "Disponível" : "Aguardando configuração"}</span></div><h3>DocuSign eSignature</h3><p>Envie contratos aprovados para assinatura e receba atualizações autenticadas de status.</p><div class="notice warn">O envio compartilha o documento e os dados dos signatários com o DocuSign. Você autoriza cada envio antes de compartilhar o documento.</div><p class="small">Entre na sua conta DocuSign e autorize o acesso. A conexão será renovada automaticamente enquanto a autorização estiver válida.</p>${state.docusign.connected ? `<p><strong>${escape(state.docusign.account_name)}</strong><span class="subline">Conta padrão do DocuSign conectada</span></p><div class="inline-actions"><button class="button small" id="connect-docusign">Reconectar</button><button class="button small danger" id="disconnect-docusign">Desconectar</button></div>` : canEdit() ? `<button class="button primary" id="connect-docusign" ${cap.docusign_enabled ? "" : "disabled"}>Conectar com DocuSign ↗</button>${!cap.docusign_enabled ? '<p class="small muted">O administrador precisa habilitar a integração uma única vez nesta instalação.</p>' : ""}` : ""}</section></div><section class="panel settings-panel"><div class="panel-header"><h2>Chaves da API</h2><button class="button small" id="new-key">＋ Criar chave</button></div><div class="modal-body"><p class="small muted">As chaves herdam as permissões da sua conta e possuem validade. O segredo aparece apenas na criação.</p>${keys.map((k) => `<div class="card-bottom"><span>${escape(k.name)} <span class="small muted">· expira ${fmtDate(k.expires_at)}</span></span><button class="button small danger" data-revoke="${k.id}">Revogar</button></div>`).join("") || '<p class="small muted">Você ainda não possui chaves ativas.</p>'}</div></section>${state.user.role === "admin" ? `<section class="panel"><div class="panel-header"><h2>Equipe</h2><button class="button small" id="new-user">＋ Adicionar pessoa</button></div><div class="scroll-table"><table><thead><tr><th>Nome</th><th>E-mail</th><th>Perfil</th></tr></thead><tbody>${state.users.map((u) => `<tr><td>${escape(u.name)}</td><td>${escape(u.email)}</td><td>${roles[u.role]}</td></tr>`).join("")}</tbody></table></div></section>` : ""}<div class="notice">Uma organização por instalação. Todas as pessoas autenticadas podem consultar os contratos. Os perfis controlam criação, edição, aprovação e administração. Para equipes que precisem de isolamento entre si, use instalações separadas.</div>${footer()}`;
  if ($("#connect-salesforce")) $("#connect-salesforce").onclick = async () => {
    try { location.assign((await post("/integrations/salesforce/connect", {})).authorization_url); }
    catch(e) { toast(e.message); }
  };
  if ($("#disconnect-salesforce")) $("#disconnect-salesforce").onclick = async () => {
    try { await api("/integrations/salesforce/connection", {method: "DELETE"}); await settingsPage(); }
    catch(e) { toast(e.message); }
  };
  if ($("#connect-docusign"))
    $("#connect-docusign").onclick = async () => {
      try {
        const result = await post("/integrations/docusign/connect", {});
        location.assign(result.authorization_url);
      } catch (e) {
        toast(e.message);
      }
    };
  if ($("#disconnect-docusign"))
    $("#disconnect-docusign").onclick = async () => {
      try {
        await api("/integrations/docusign/connection", { method: "DELETE" });
        state.docusign = await api("/integrations/docusign/status");
        await settingsPage();
        toast(
          "Conexão removida desta instalação. Envelopes já enviados continuam no DocuSign.",
        );
      } catch (e) {
        toast(e.message);
      }
    };
  $("#new-key").onclick = () => {
    openModal(
      "Criar chave da API",
      "Use uma conta com as permissões necessárias à integração.",
      '<form id="key-form"><label>Nome<input name="name" required maxlength="80" placeholder="Ex.: Integração ERP"></label><label>Validade em dias<input type="number" name="days" min="1" max="365" value="30" required></label><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">Criar chave</button></div></form>',
    );
    bindForm("#key-form", async (data) => {
      const k = await post("/auth/api-keys", {
        name: data.get("name"),
        days: Number(data.get("days")),
      });
      openModal(
        "Guarde sua chave",
        "Ela não será exibida novamente.",
        `<p>Use no cabeçalho <code>Authorization: Bearer SUA_CHAVE</code>.</p><div class="code api-key">${escape(k.token)}</div><p class="small muted">Mantenha esta chave em um gerenciador de segredos.</p>`,
      );
      await settingsPage();
    });
  };
  $$("[data-revoke]").forEach(
    (b) =>
      (b.onclick = async () => {
        try {
          await api(`/auth/api-keys/${b.dataset.revoke}`, { method: "DELETE" });
          await settingsPage();
          toast("Chave revogada.");
        } catch (e) {
          toast(e.message);
        }
      }),
  );
  if ($("#new-user"))
    $("#new-user").onclick = () => {
      openModal(
        "Adicionar pessoa",
        "Crie uma conta para acessar esta instalação.",
        `<form id="user-form"><label>Nome<input name="name" required maxlength="120"></label><label>E-mail<input name="email" type="email" required></label><label>Senha inicial<input name="password" type="password" minlength="12" maxlength="256" required autocomplete="new-password"><span class="field-help">Mínimo de 12 caracteres. A redefinição é feita pelo administrador no servidor.</span></label><label>Perfil<select name="role">${Object.entries(
          roles,
        )
          .map(
            ([v, l]) =>
              `<option value="${v}" ${v === "editor" ? "selected" : ""}>${l}</option>`,
          )
          .join(
            "",
          )}</select></label><div class="error" role="alert"></div><div class="form-actions"><button class="button primary" type="submit">Criar conta</button></div></form>`,
      );
      bindForm("#user-form", async (data) => {
        const user = await post("/users", Object.fromEntries(data));
        state.users.push(user);
        modal.close();
        await settingsPage();
        toast("Conta criada.");
      });
    };
}
async function boot() {
  try {
    state.user = await api("/auth/me");
    await loadWorkspace();
  } catch (error) {
    loginScreen();
    if (error.message !== "Faça login para continuar.") toast(error.message);
  }
}
boot();

function salesforcePicker(contractId) {
  openModal("Vincular oportunidade", "A vinculação fica no OpenCLM. O registro no Salesforce não será alterado.",
    '<form id="sf-search"><label>Nome da oportunidade<input name="q" maxlength="100" placeholder="Buscar pelo nome"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Buscar</button></form><div id="sf-results"></div>');
  bindForm("#sf-search", async data => {
    const result = await api(`/integrations/salesforce/opportunities?q=${encodeURIComponent(data.get("q"))}`);
    $("#sf-results").innerHTML = result.items.length ? result.items.map(item => `<div class="card"><strong>${escape(item.name)}</strong><p>${escape(item.account)} · ${escape(item.stage)}</p><button class="button small" data-opportunity="${escape(item.id)}">Vincular</button></div>`).join("") : '<p>Nenhuma oportunidade encontrada.</p>';
    $$("[data-opportunity]").forEach(button => button.onclick = async () => {
      button.disabled = true;
      try {
        await post(`/integrations/salesforce/contracts/${contractId}`, {opportunity_id: button.dataset.opportunity});
        await showContract(contractId); toast("Oportunidade vinculada.");
      } catch(e) { toast(e.message); button.disabled = false; }
    });
  });
}
