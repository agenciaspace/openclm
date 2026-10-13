"use strict";
let authOptions = { signup_enabled: true, email_enabled: false };
let pendingInvite = new URLSearchParams(location.hash.slice(1)).get("invite");

function authFrame(title, subtitle, content) {
  modal.close();
  $("#app").innerHTML = `<div class="login"><section class="login-story"><div class="brand"><img src="${BASE_PATH}/static/icon.svg" alt="">open<em>${IS_INTAKE ? "intake" : "clm"}</em></div><div><div class="eyebrow">Livre por princípio.</div><h1>${IS_INTAKE ? "Seu pedido.<br>O time certo.<br>Tudo acompanhado." : "Seus contratos.<br>Sua equipe.<br>Seu espaço."}</h1><p>${IS_INTAKE ? "Envie solicitações ao jurídico, complete as informações e acompanhe cada resposta em um espaço privado." : "Crie sua organização, convide sua equipe e acompanhe seus contratos em um espaço privado."}</p></div><div class="small">Código aberto · Uso gratuito</div></section><section class="login-form"><div class="login-box"><h2>${escape(title)}</h2><p>${escape(subtitle)}</p>${content}</div></section></div>`;
}

function renderAuth(mode = "login") {
  const register = mode === "register";
  const title = register ? "Crie sua conta gratuita" : "Acesse seu espaço";
  authFrame(title, pendingInvite ? "Entre com o e-mail que recebeu o convite." : "Organize seus contratos e trabalhe com sua equipe.", `
    <form id="${register ? "register" : "login"}">
      ${register ? '<label>Seu nome<input name="name" required minlength="2" maxlength="120" autocomplete="name"></label>' : ""}
      <label>E-mail<input name="email" type="email" required autocomplete="username"></label>
      <label>Senha<input name="password" type="password" required ${register ? 'minlength="12" maxlength="256" autocomplete="new-password"' : 'autocomplete="current-password"'}></label>
      ${register && !pendingInvite ? '<label>Nome da organização<input name="organization_name" required minlength="2" maxlength="120" placeholder="Sua empresa ou equipe"></label>' : ""}
      ${register ? '<p class="small muted">Use pelo menos 12 caracteres na senha. Você receberá códigos para recuperar seu acesso.</p>' : ""}
      <div class="error" role="alert"></div><button class="button primary" type="submit">${register ? "Criar conta gratuita" : `Entrar no ${PRODUCT_NAME}`} →</button>
    </form><div class="auth-actions">${authOptions.signup_enabled ? `<button class="text-link" id="auth-mode">${register ? "Já tenho conta" : "Criar conta gratuita"}</button>` : ""}<button class="text-link" id="forgot-password">Recuperar acesso</button></div>`);
  if ($("#auth-mode")) $("#auth-mode").onclick = () => renderAuth(register ? "login" : "register");
  $("#forgot-password").onclick = recoveryScreen;
  bindForm(register ? "#register" : "#login", async data => {
    const result = await post(register ? "/auth/register" : "/auth/login", Object.fromEntries(data));
    state.user = register ? result.user : result;
    if (register) recoveryCodesScreen(result.recovery_codes, () => loadWorkspace());
    else await loadWorkspace();
  });
}

function recoveryCodesScreen(codes, next) {
  authFrame("Guarde seus códigos de recuperação", "Eles permitem recuperar a conta mesmo sem acesso ao e-mail. Cada código só pode ser usado uma vez.", `<div class="recovery-codes">${codes.map(code => `<code>${escape(code)}</code>`).join("")}</div><p class="small muted">Guarde em um gerenciador de senhas ou arquivo seguro. Não compartilhe esses códigos. Ao gerar novos códigos, os anteriores deixam de funcionar.</p><div class="form-actions"><button class="button" id="download-codes">Baixar códigos</button><button class="button primary" id="continue-account">Guardei, continuar →</button></div>`);
  $("#download-codes").onclick = () => {
    const blob = new Blob([`OpenCLM — códigos de recuperação\nConta: ${state.user?.email || "sua conta"}\n\n${codes.join("\n")}\n\nNão compartilhe. Cada código pode ser usado uma vez.`], { type: "text/plain;charset=utf-8" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob); link.download = "openclm-codigos-recuperacao.txt"; link.click();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
  };
  $("#continue-account").onclick = () => Promise.resolve(next()).catch(error => toast(error.message));
}

function recoveryScreen() {
  authFrame("Recuperar acesso", "Use um dos códigos que você guardou ao criar sua conta.", `<form id="recover"><label>E-mail<input name="email" type="email" required autocomplete="username"></label><label>Código de recuperação<input name="code" required autocomplete="off" minlength="20"></label><label>Nova senha<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Redefinir senha</button></form>${authOptions.email_enabled ? '<div class="notice"><button class="text-link" id="recover-email">Receber um link por e-mail</button></div>' : ""}<div class="auth-actions"><button class="text-link" id="back-login">Voltar para entrar</button></div>`);
  $("#back-login").onclick = () => renderAuth();
  bindForm("#recover", async data => {
    const result = await post("/auth/recover", Object.fromEntries(data));
    state.user = null;
    recoveryCodesScreen(result.recovery_codes, () => renderAuth());
  });
  if ($("#recover-email")) $("#recover-email").onclick = () => {
    authFrame("Recuperar por e-mail", "Informe o e-mail da sua conta.", '<form id="forgot"><label>E-mail<input name="email" type="email" required autocomplete="username"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Enviar instruções</button></form><div class="auth-actions"><button class="text-link" id="back-login">Voltar</button></div>');
    $("#back-login").onclick = () => renderAuth();
    bindForm("#forgot", async data => {
      const result = await post("/auth/forgot-password", Object.fromEntries(data));
      $("#forgot").replaceWith(Object.assign(document.createElement("p"), { textContent: result.message }));
    });
  };
}

async function handleAccountLink() {
  const params = new URLSearchParams(location.hash.slice(1));
  const verify = params.get("verify"); const reset = params.get("reset");
  if (!verify && !reset) return false;
  history.replaceState(null, "", BASE_PATH + "/");
  if (verify) {
    try { const result = await post("/auth/verify-email", { token: verify }); renderAuth(); toast(result.message); }
    catch (error) { renderAuth(); toast(error.message); }
  } else {
    authFrame("Defina sua nova senha", "Todas as sessões e chaves antigas serão encerradas.", '<form id="reset"><label>Nova senha<input name="password" type="password" required minlength="12" maxlength="256" autocomplete="new-password"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Salvar senha</button></form>');
    bindForm("#reset", async data => {
      const result = await post("/auth/reset-password", { token: reset, password: data.get("password") });
      state.user = null; recoveryCodesScreen(result.recovery_codes, () => renderAuth());
    });
  }
  return true;
}

async function invitationScreen() {
  let preview;
  try { preview = await post("/invitations/preview", { token: pendingInvite }); }
  catch (error) { pendingInvite = null; history.replaceState(null, "", BASE_PATH + "/"); await loadWorkspace(); toast(error.message); return; }
  const sameEmail = preview.email.toLowerCase() === state.user.email.toLowerCase();
  authFrame(`Convite para ${preview.organization_name}`, `Perfil: ${roles[preview.role]}. Convite destinado a ${preview.email}.`, `<p>Você está conectado como <strong>${escape(state.user.email)}</strong>.</p><form id="accept-invite"><div class="error" role="alert"></div><button class="button primary" type="submit" ${sameEmail ? "" : "disabled"}>Aceitar convite</button></form><div class="auth-actions"><button class="text-link" id="invite-other">Entrar com outra conta</button><button class="text-link" id="invite-cancel">Agora não</button></div>`);
  bindForm("#accept-invite", async () => {
    const org = await post("/invitations/accept", { token: pendingInvite });
    state.user = await post(`/organizations/${org.id}/activate`, {});
    pendingInvite = null; history.replaceState(null, "", BASE_PATH + "/");
    await loadWorkspace(); toast("Você entrou na organização.");
  });
  $("#invite-other").onclick = async () => { await post("/auth/logout", {}); state.user = null; renderAuth(); };
  $("#invite-cancel").onclick = async () => { pendingInvite = null; history.replaceState(null, "", BASE_PATH + "/"); await loadWorkspace(); };
}

function organizationOnboarding() {
  authFrame("Escolha seu espaço", "Crie uma organização gratuita ou abra o link de convite que recebeu.", '<form id="organization"><label>Nome da organização<input name="name" required minlength="2" maxlength="120"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Criar organização</button></form><div class="auth-actions"><button class="text-link" id="onboarding-logout">Sair da conta</button></div>');
  $("#onboarding-logout").onclick = async () => { await post("/auth/logout", {}); state.user = null; renderAuth(); };
  bindForm("#organization", async data => {
    const org = await post("/organizations", Object.fromEntries(data));
    state.user = await post(`/organizations/${org.id}/activate`, {}); await loadWorkspace();
  });
  if (state.organizations?.length) {
    const select = document.createElement("select"); select.setAttribute("aria-label", "Organização existente");
    select.innerHTML = '<option value="">Selecione uma organização existente</option>' + state.organizations.map(org => `<option value="${escape(org.id)}">${escape(org.name)}</option>`).join("");
    $("#organization").before(select);
    select.onchange = async () => { if (!select.value) return; state.user = await post(`/organizations/${select.value}/activate`, {}); await loadWorkspace(); };
  }
}

function renderOrganizationSwitcher() {
  const bar = document.createElement("div"); bar.className = "organization-bar";
  bar.innerHTML = `<label>Organização<select id="organization-switch" aria-label="Organização ativa">${state.organizations.map(org => `<option value="${escape(org.id)}" ${org.id === state.user.organization_id ? "selected" : ""}>${escape(org.name)} · ${roles[org.role]}</option>`).join("")}</select></label><button class="button small" id="create-organization">＋ Nova organização</button>`;
  $(".topbar").after(bar);
  $("#organization-switch").onchange = async event => {
    const select = event.target; select.disabled = true;
    try {
      state.user = await post(`/organizations/${select.value}/activate`, {});
      state.view = "contracts"; state.query = ""; state.status = ""; state.offset = 0; state.contract = null;
      modal.close(); $("#content").textContent = "Carregando organização…"; await loadWorkspace();
    } catch (error) { toast(error.message); select.disabled = false; }
  };
  $("#create-organization").onclick = () => {
    openModal("Nova organização", "Cada organização possui seus próprios contratos, modelos e equipe.", '<form id="new-organization"><label>Nome<input name="name" required minlength="2" maxlength="120"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Criar organização gratuita</button></form>');
    bindForm("#new-organization", async data => {
      const org = await post("/organizations", Object.fromEntries(data));
      state.user = await post(`/organizations/${org.id}/activate`, {}); state.view = "contracts"; modal.close(); await loadWorkspace();
    });
  };
}

function accountSettingsHTML() {
  return `<section class="panel settings-panel"><div class="panel-header"><h2>Sua conta</h2></div><div class="modal-body"><p>${escape(state.user.email)} · ${state.user.email_verified ? "E-mail confirmado" : "E-mail ainda não confirmado"}</p><div class="inline-actions">${!state.user.email_verified && authOptions.email_enabled ? '<button class="button" id="verify-email">Confirmar e-mail</button>' : ""}<button class="button" id="recovery-codes">Gerar códigos de recuperação</button></div><p class="small muted">Guarde seus códigos para recuperar o acesso se perder a senha.</p></div></section>${state.user.role === "admin" ? `<section class="panel"><div class="panel-header"><h2>Equipe</h2><button class="button small" id="new-user">＋ Convidar pessoa</button></div><div class="scroll-table"><table><thead><tr><th>Nome</th><th>E-mail</th><th>Perfil</th><th>Ações</th></tr></thead><tbody>${state.users.map(user => `<tr><td>${escape(user.name)}${user.is_owner ? '<span class="subline">Proprietário</span>' : ""}</td><td>${escape(user.email)}</td><td><select aria-label="Perfil de ${escape(user.name)}" data-member-role="${escape(user.id)}" ${user.is_owner ? "disabled" : ""}>${Object.entries(roles).map(([value, label]) => `<option value="${value}" ${value === user.role ? "selected" : ""}>${label}</option>`).join("")}</select></td><td>${!user.is_owner ? `<button class="button small danger" data-remove-member="${escape(user.id)}">Remover</button>${state.user.is_owner ? `<button class="button small" data-transfer-owner="${escape(user.id)}">Transferir propriedade</button>` : ""}` : ""}</td></tr>`).join("")}</tbody></table></div><div class="modal-body"><h3>Convites pendentes</h3><div id="pending-invitations"></div><button class="button small" id="rename-organization">Renomear organização</button></div></section>` : ""}<div class="notice">Os contratos são compartilhados apenas com os membros desta organização. Administradores gerenciam a equipe; editores criam contratos; revisores aprovam; leitores consultam.</div>`;
}

async function bindAccountSettings() {
  if ($("#verify-email")) $("#verify-email").onclick = async () => { try { toast((await post("/auth/resend-verification", {})).message); } catch (error) { toast(error.message); } };
  $("#recovery-codes").onclick = () => {
    openModal("Novos códigos de recuperação", "Os códigos anteriores deixarão de funcionar.", '<form id="recovery-confirm"><label>Senha atual<input name="password" type="password" required autocomplete="current-password"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Gerar códigos</button></form>');
    bindForm("#recovery-confirm", async data => { const result = await post("/auth/recovery-codes", Object.fromEntries(data)); recoveryCodesScreen(result.recovery_codes, () => loadWorkspace()); });
  };
  if (state.user.role !== "admin") return;
  const invites = await api("/organizations/current/invitations");
  $("#pending-invitations").innerHTML = invites.length ? invites.map(item => `<div class="invitation-row"><span>${escape(item.email)} · ${roles[item.role]}<span class="subline">Expira em ${fmtDate(item.expires_at)}</span></span><button class="button small danger" data-revoke-invite="${escape(item.id)}">Revogar</button></div>`).join("") : '<p class="small muted">Nenhum convite pendente.</p>';
  $$("[data-revoke-invite]").forEach(button => button.onclick = async () => { try { await api(`/organizations/current/invitations/${button.dataset.revokeInvite}`, { method: "DELETE" }); await settingsPage(); } catch (error) { toast(error.message); } });
  $$("[data-member-role]").forEach(select => select.onchange = async () => {
    try { await api(`/organizations/current/members/${select.dataset.memberRole}`, { method: "PATCH", body: JSON.stringify({ role: select.value }) }); state.user = await api("/auth/me"); await settingsPage(); toast("Perfil atualizado."); }
    catch (error) { toast(error.message); await settingsPage(); }
  });
  $$("[data-remove-member], [data-transfer-owner]").forEach(button => button.onclick = () => {
    const transfer = Boolean(button.dataset.transferOwner); const id = button.dataset.transferOwner || button.dataset.removeMember;
    const user = state.users.find(item => item.id === id);
    openModal(transfer ? "Transferir propriedade" : "Remover da organização", transfer ? `${user.name} poderá administrar a propriedade desta organização.` : `${user.name} perderá o acesso a esta organização imediatamente.`, '<form id="confirm-member"><div class="error" role="alert"></div><button class="button danger" type="submit">Confirmar</button></form>');
    bindForm("#confirm-member", async () => {
      if (transfer) await post("/organizations/current/transfer-ownership", { user_id: id });
      else await api(`/organizations/current/members/${id}`, { method: "DELETE" });
      modal.close(); await loadWorkspace(); toast(transfer ? "Propriedade transferida." : "Membro removido.");
    });
  });
  $("#rename-organization").onclick = () => {
    openModal("Nome da organização", "Atualize como a equipe identifica este espaço.", `<form id="rename-org"><label>Nome<input name="name" required minlength="2" maxlength="120" value="${escape(state.user.organization_name)}"></label><div class="error" role="alert"></div><button class="button primary" type="submit">Salvar nome</button></form>`);
    bindForm("#rename-org", async data => { await api("/organizations/current", { method: "PATCH", body: JSON.stringify(Object.fromEntries(data)) }); modal.close(); await loadWorkspace(); });
  };
  $("#new-user").onclick = () => {
    openModal("Convidar pessoa", "A pessoa define a própria senha e só entra nesta organização ao aceitar.", `<form id="invite-form"><label>E-mail<input name="email" type="email" required></label><label>Perfil<select name="role">${Object.entries(roles).map(([value, label]) => `<option value="${value}" ${value === "editor" ? "selected" : ""}>${label}</option>`).join("")}</select></label><div class="error" role="alert"></div><button class="button primary" type="submit">Criar convite</button></form>`);
    bindForm("#invite-form", async data => {
      const invitation = await post("/organizations/current/invitations", Object.fromEntries(data));
      openModal("Convite criado", invitation.email_queued ? "O envio por e-mail está na fila. Você também pode compartilhar o link." : "Compartilhe este link diretamente com a pessoa convidada.", `<label>Link pessoal, válido por 7 dias<input id="invitation-link" readonly value="${escape(invitation.link)}"></label><p class="small muted">Destinatário: ${escape(invitation.email)}. O link só pode ser usado uma vez.</p><button class="button primary" id="copy-invitation">Copiar link</button>`);
      $("#copy-invitation").onclick = async () => { try { await navigator.clipboard.writeText(invitation.link); toast("Link copiado."); } catch { $("#invitation-link").select(); toast("Selecione e copie o link."); } };
      await settingsPage();
    });
  };
}
