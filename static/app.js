const state = {
  accountId: null,
  crmAccountId: null,
  dialogRef: null,
  dialogKey: null,
  accounts: [],
  messages: [],
  dialogsSignature: "",
  messagesSignature: "",
  lastReadIncomingId: 0,
  readBusy: false,
  pollBusy: false,
  toastTimer: null,
  view: "crm",
};

const el = (id) => document.getElementById(id);
const status = el("form-status");

function csrfToken() {
  const item = document.cookie.split("; ").find((row) => row.startsWith("crm_csrf="));
  return item ? decodeURIComponent(item.split("=").slice(1).join("=")) : "";
}

function showLogin(message = "") {
  el("app-view").classList.add("hidden");
  el("login-view").classList.remove("hidden");
  el("login-status").textContent = message;
  el("login-status").className = `form-status ${message ? "error" : ""}`;
}

function showApp(username) {
  el("login-view").classList.add("hidden");
  el("app-view").classList.remove("hidden");
  el("admin-name").textContent = username;
  switchView(state.view);
}

const viewMeta = {
  crm: { eyebrow: "TELEGRAM CRM", title: "История диалогов" },
  accounts: { eyebrow: "ACCOUNT VAULT", title: "Аккаунты" },
  connect: { eyebrow: "NEW SESSION", title: "Подключение" },
};

function closeMenu() {
  el("app-view").classList.remove("menu-open");
  el("burger-button").setAttribute("aria-expanded", "false");
}

function switchView(view) {
  if (!viewMeta[view]) return;
  state.view = view;
  document.querySelectorAll(".app-section").forEach((section) => section.classList.add("hidden"));
  el(`view-${view}`).classList.remove("hidden");
  document.querySelectorAll(".nav-button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
  });
  el("view-eyebrow").textContent = viewMeta[view].eyebrow;
  el("view-title").textContent = viewMeta[view].title;
  closeMenu();
}

function setStatus(message, type = "") {
  status.textContent = message;
  status.className = `form-status ${type}`;
}

function headers() {
  return { "Content-Type": "application/json" };
}

async function request(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const protectedHeaders = method === "GET" || method === "HEAD" ? {} : { "X-CSRF-Token": csrfToken() };
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { ...headers(), ...protectedHeaders, ...options.headers },
  });
  const body = await response.json().catch(() => ({}));
  if (response.status === 401 && path !== "/auth/login") showLogin("Сессия истекла. Войдите снова.");
  if (!response.ok) throw new Error(body.detail || "Не удалось выполнить запрос.");
  return body;
}

function renderAccounts(accounts) {
  state.accounts = accounts;
  el("account-count").textContent = accounts.length;
  el("nav-account-count").textContent = accounts.length;
  const picker = el("crm-account-picker");
  const selected = state.crmAccountId || "";
  picker.innerHTML = '<option value="">Выберите аккаунт</option>' + accounts
    .filter((account) => account.status === "active")
    .map((account) => `<option value="${escapeHtml(account.id)}">${escapeHtml(account.label || account.phone)}${account.username ? ` · @${escapeHtml(account.username)}` : ""}</option>`)
    .join("");
  picker.value = selected;
  const selectedAccount = accounts.find((account) => account.id === selected);
  setAvatar(el("crm-account-avatar"), selectedAccount?.avatar_url, selectedAccount?.label || selectedAccount?.username || "TG");
  const container = el("accounts-list");
  if (!accounts.length) {
    container.innerHTML = '<div class="empty-state">Подключённых аккаунтов пока нет.</div>';
    return;
  }
  container.innerHTML = accounts.map((account) => `
    <div class="account">
      <div class="account-identity">
        ${avatarMarkup(account.avatar_url, account.label || account.username || account.phone, "account-avatar")}
        <div>
          <div class="account-title">${escapeHtml(account.label || "Без названия")}</div>
          <div class="account-username">${account.username ? `@${escapeHtml(account.username)}` : "username не задан"}</div>
          <div class="account-phone">${escapeHtml(account.phone)}${account.telegram_peer_id ? ` · ID ${escapeHtml(account.telegram_peer_id)}` : ""}</div>
        </div>
      </div>
      <div class="account-actions">
        ${account.status === "active" ? `
          <button class="open-crm-button" type="button" data-account-id="${escapeHtml(account.id)}" data-account-title="${escapeHtml(account.label || account.phone)}">Открыть CRM</button>
          <button class="revoke-sessions-button" type="button" data-account-id="${escapeHtml(account.id)}" data-account-title="${escapeHtml(account.label || account.phone)}">Закрыть другие сессии</button>
        ` : ""}
        <span class="status ${escapeHtml(account.status)}">${account.status === "active" ? "АКТИВЕН" : "ОЖИДАЕТ"}</span>
      </div>
    </div>`).join("");
}

function escapeHtml(value) {
  const node = document.createElement("span");
  node.textContent = String(value);
  return node.innerHTML;
}

function initialsFor(value) {
  return String(value || "TG").trim().split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "TG";
}

function avatarMarkup(url, title, extraClass = "") {
  return `<span class="avatar ${extraClass}"><span>${escapeHtml(initialsFor(title))}</span>${url ? `<img class="avatar-image" src="${escapeHtml(url)}" alt="">` : ""}</span>`;
}

function setAvatar(container, url, title) {
  if (!container) return;
  container.innerHTML = `<span>${escapeHtml(initialsFor(title))}</span>${url ? `<img class="avatar-image" src="${escapeHtml(url)}" alt="">` : ""}`;
}

function formatDate(value, withDate = false) {
  if (!value) return "";
  const date = new Date(value);
  return new Intl.DateTimeFormat("ru-RU", withDate
    ? { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }
    : { hour: "2-digit", minute: "2-digit" }).format(date);
}

function showToast(message, type = "") {
  const toast = el("crm-toast");
  window.clearTimeout(state.toastTimer);
  toast.textContent = message;
  toast.className = `crm-toast ${type}`;
  state.toastTimer = window.setTimeout(() => toast.classList.add("hidden"), 3500);
}

function renderProfile(profile) {
  el("chat-title").textContent = profile.title;
  setAvatar(el("chat-avatar"), profile.avatar_url, profile.title);
  const subtitleParts = [];
  if (profile.status) subtitleParts.push(profile.status);
  if (profile.participants_count) subtitleParts.push(`${profile.participants_count} участников`);
  if (!subtitleParts.length && profile.username) subtitleParts.push(`@${profile.username}`);
  el("chat-subtitle").textContent = subtitleParts.join(" · ") || "Информация о чате";

  const rows = [];
  if (profile.username) rows.push(`<div><span>Имя пользователя</span><strong>@${escapeHtml(profile.username)}</strong></div>`);
  if (profile.phone) rows.push(`<div><span>Телефон</span><strong>+${escapeHtml(profile.phone)}</strong></div>`);
  if (profile.participants_count) rows.push(`<div><span>Участники</span><strong>${escapeHtml(profile.participants_count)}</strong></div>`);
  if (profile.about) rows.push(`<div class="profile-about"><span>Описание</span><strong>${escapeHtml(profile.about).replaceAll("\n", "<br>")}</strong></div>`);
  el("chat-profile-content").innerHTML = rows.length
    ? rows.join("")
    : '<div class="profile-empty">Telegram не вернул дополнительных данных.</div>';
}

function renderBotButtons(message) {
  if (!message.buttons?.length) return "";
  return `<div class="bot-buttons">${message.buttons.map((row) => `
    <div class="bot-button-row">${row.map((button) => {
      if (button.type === "url") {
        return `<a class="bot-button" href="${escapeHtml(button.url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(button.text)} <span aria-hidden="true">↗</span></a>`;
      }
      if (button.type === "callback" || button.type === "text") {
        return `<button class="bot-button bot-callback-button" type="button" data-message-id="${message.id}" data-row="${button.row}" data-column="${button.column}">${escapeHtml(button.text)}</button>`;
      }
      return `<button class="bot-button" type="button" disabled title="Эта кнопка требует действие, которое CRM не отправляет автоматически">${escapeHtml(button.text)}</button>`;
    }).join("")}</div>`).join("")}</div>`;
}

function formatFileSize(bytes) {
  if (!Number.isFinite(Number(bytes)) || Number(bytes) <= 0) return "";
  const units = ["Б", "КБ", "МБ", "ГБ"];
  let value = Number(bytes);
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value >= 10 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`;
}

function renderMedia(message) {
  const media = message.media;
  if (!media || !state.crmAccountId || !state.dialogKey) return "";
  const baseUrl = `/accounts/${encodeURIComponent(state.crmAccountId)}/media/${encodeURIComponent(state.dialogKey)}/${message.id}`;
  const downloadUrl = `${baseUrl}?download=true`;
  const size = formatFileSize(media.size);
  if (!media.available) {
    return `<div class="message-media media-unavailable"><strong>${escapeHtml(media.name)}</strong><span>${escapeHtml(size)} · файл больше лимита 100 МБ</span></div>`;
  }

  let preview = "";
  if (media.kind === "photo" || media.kind === "gif" && media.mime_type.startsWith("image/")) {
    preview = `<a class="media-photo-link" href="${downloadUrl}" target="_blank" rel="noopener"><img class="media-photo" src="${baseUrl}" alt="${escapeHtml(media.name)}" loading="lazy"></a>`;
  } else if (media.kind === "video" || media.kind === "gif" || media.kind === "sticker" && media.mime_type.startsWith("video/")) {
    const animated = media.kind === "sticker" || media.kind === "gif";
    preview = `<video class="media-video" src="${baseUrl}" ${animated ? "autoplay muted loop" : "controls"} playsinline preload="metadata"></video>`;
  } else if (media.kind === "voice" || media.kind === "audio") {
    preview = `<audio class="media-audio" src="${baseUrl}" controls preload="metadata"></audio>`;
  } else if (media.kind === "sticker" && media.mime_type.startsWith("image/")) {
    preview = `<img class="media-sticker" src="${baseUrl}" alt="Стикер" loading="lazy">`;
  } else {
    preview = `<a class="media-document" href="${downloadUrl}"><span class="media-document-icon">↓</span><span><strong>${escapeHtml(media.name)}</strong><small>${escapeHtml(size || media.mime_type)}</small></span></a>`;
  }

  const showFooter = !preview.includes("media-document");
  return `<div class="message-media">${preview}${showFooter ? `<div class="media-footer"><span>${escapeHtml(size || media.name)}</span><a href="${downloadUrl}">Скачать</a></div>` : ""}</div>`;
}

function renderDialogs(dialogs) {
  const container = el("dialogs-list");
  if (!dialogs.length) {
    container.innerHTML = '<div class="empty-state">Telegram не вернул доступных диалогов.</div>';
    return;
  }
  const activeDialog = dialogs.find((dialog) => dialog.key === state.dialogKey);
  if (activeDialog) state.dialogRef = activeDialog.ref;
  container.innerHTML = dialogs.map((dialog) => `
    <button class="dialog-button ${dialog.key === state.dialogKey ? "active" : ""}" type="button" data-dialog-key="${escapeHtml(dialog.key)}" data-dialog-ref="${escapeHtml(dialog.ref)}" data-dialog-title="${escapeHtml(dialog.title)}" data-avatar-url="${escapeHtml(dialog.avatar_url || "")}">
      ${avatarMarkup(dialog.avatar_url, dialog.title, "dialog-avatar")}
      <span class="dialog-content"><span class="dialog-title">${escapeHtml(dialog.title)}</span><span class="dialog-preview">${escapeHtml(dialog.preview || "Нет сообщений")}</span></span>
      <span><span class="dialog-time">${escapeHtml(formatDate(dialog.last_message_at))}</span>${dialog.unread_count ? `<span class="unread">${dialog.unread_count}</span>` : ""}</span>
    </button>`).join("");
}

function renderMessages(messages, preserveScroll = false) {
  state.messages = messages;
  el("message-count").textContent = messages.length;
  const container = el("messages-list");
  const wasNearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 90;
  if (!messages.length) {
    container.innerHTML = '<div class="empty-state">В этом диалоге нет доступных сообщений.</div>';
    return;
  }
  container.innerHTML = messages.map((message) => `
    <article class="message ${message.outgoing ? "outgoing" : ""}">
      <div class="message-sender">${escapeHtml(message.sender_name)}</div>
      ${message.text ? `<div class="message-text">${escapeHtml(message.text).replaceAll("\n", "<br>")}</div>` : ""}
      ${renderMedia(message)}
      ${renderBotButtons(message)}
      <div class="message-meta">${escapeHtml(formatDate(message.date, true))}</div>
    </article>`).join("");
  if (!preserveScroll || wasNearBottom) container.scrollTop = container.scrollHeight;
}

function updateDialogs(dialogs) {
  const signature = JSON.stringify(dialogs.map((dialog) => [
    dialog.key, dialog.title, dialog.unread_count, dialog.preview, dialog.last_message_at, dialog.avatar_url,
  ]));
  if (signature === state.dialogsSignature) return;
  state.dialogsSignature = signature;
  renderDialogs(dialogs);
}

function updateMessages(messages, preserveScroll = true) {
  acknowledgeVisibleMessages(messages);
  const signature = JSON.stringify(messages.map((message) => [message.id, message.text, message.date, message.media, message.buttons]));
  if (signature === state.messagesSignature) return;
  state.messagesSignature = signature;
  renderMessages(messages, preserveScroll);
}

async function acknowledgeVisibleMessages(messages) {
  if (
    state.readBusy ||
    document.hidden ||
    state.view !== "crm" ||
    !state.crmAccountId ||
    !state.dialogRef
  ) return;
  const incomingIds = messages.filter((message) => !message.outgoing).map((message) => Number(message.id));
  if (!incomingIds.length) return;
  const newestIncomingId = Math.max(...incomingIds);
  if (newestIncomingId <= state.lastReadIncomingId) return;
  const maxId = Math.max(...messages.map((message) => Number(message.id)));
  const accountId = state.crmAccountId;
  const dialogRef = state.dialogRef;
  const dialogKey = state.dialogKey;
  state.readBusy = true;
  try {
    await request(`/accounts/${accountId}/mark-read`, {
      method: "POST",
      body: JSON.stringify({ dialog_ref: dialogRef, max_id: maxId }),
    });
    if (state.crmAccountId === accountId && state.dialogRef === dialogRef) {
      state.lastReadIncomingId = newestIncomingId;
      document.querySelectorAll(".dialog-button").forEach((button) => {
        if (button.dataset.dialogKey === dialogKey) button.querySelector(".unread")?.remove();
      });
    }
  } catch (_) {
    // Retry on the next polling tick if Telegram was temporarily unavailable.
  } finally {
    state.readBusy = false;
  }
}

async function openCrm(accountId, accountTitle) {
  state.crmAccountId = accountId;
  state.dialogRef = null;
  state.dialogKey = null;
  state.dialogsSignature = "";
  state.messagesSignature = "";
  state.lastReadIncomingId = 0;
  switchView("crm");
  el("crm-account-picker").value = accountId;
  const crmAccount = state.accounts.find((account) => account.id === accountId);
  setAvatar(el("crm-account-avatar"), crmAccount?.avatar_url, crmAccount?.label || crmAccount?.username || accountTitle);
  el("dialogs-list").innerHTML = '<div class="loading-state">Загружаем диалоги…</div>';
  el("messages-list").innerHTML = '<div class="empty-state">Выберите диалог слева.</div>';
  el("message-form").classList.add("hidden");
  el("message-text").value = "";
  el("chat-title").textContent = "Выберите диалог";
  el("chat-subtitle").textContent = "Откройте чат слева";
  setAvatar(el("chat-avatar"), "", "TG");
  el("chat-info-button").disabled = true;
  el("chat-info-button").setAttribute("aria-expanded", "false");
  el("chat-profile-panel").classList.add("hidden");
  el("global-search-panel").classList.add("hidden");
  el("message-count").textContent = "0";
  try {
    updateDialogs(await request(`/accounts/${accountId}/dialogs?limit=50`));
  } catch (error) {
    el("dialogs-list").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  }
}

async function openDialog(dialogRef, title, dialogKey, avatarUrl = "", button = null) {
  state.dialogRef = dialogRef;
  state.dialogKey = dialogKey;
  state.messagesSignature = "";
  state.lastReadIncomingId = 0;
  document.querySelectorAll(".dialog-button").forEach((item) => item.classList.remove("active"));
  if (button) button.classList.add("active");
  el("chat-title").textContent = title;
  el("chat-subtitle").textContent = "Загружаем информацию…";
  setAvatar(el("chat-avatar"), avatarUrl, title);
  el("chat-info-button").disabled = false;
  el("chat-info-button").setAttribute("aria-expanded", "false");
  el("chat-profile-panel").classList.add("hidden");
  el("message-form").classList.remove("hidden");
  el("composer-status").textContent = "Ctrl + Enter — отправить";
  el("composer-status").className = "composer-status";
  el("messages-list").innerHTML = '<div class="loading-state">Загружаем историю…</div>';
  const [historyResult, profileResult] = await Promise.allSettled([
    request(`/accounts/${state.crmAccountId}/chat-history`, {
      method: "POST", body: JSON.stringify({ dialog_ref: dialogRef, limit: 100 }),
    }),
    request(`/accounts/${state.crmAccountId}/chat-profile`, {
      method: "POST", body: JSON.stringify({ dialog_ref: dialogRef }),
    }),
  ]);
  if (state.dialogRef !== dialogRef) return;
  if (historyResult.status === "fulfilled") updateMessages(historyResult.value, false);
  else el("messages-list").innerHTML = `<div class="empty-state">${escapeHtml(historyResult.reason.message)}</div>`;
  if (profileResult.status === "fulfilled") renderProfile(profileResult.value);
  else el("chat-subtitle").textContent = "Информация недоступна";
}

function renderSearchResults(results, query) {
  el("global-search-panel").classList.remove("hidden");
  el("global-search-title").textContent = `${results.length} · «${query}»`;
  const container = el("global-search-results");
  if (!results.length) {
    container.innerHTML = '<div class="empty-state">Совпадений в доступных переписках не найдено.</div>';
    return;
  }
  container.innerHTML = results.map((result) => `
    <button class="search-result" type="button" data-dialog-ref="${escapeHtml(result.dialog_ref)}" data-dialog-key="${escapeHtml(result.dialog_key)}" data-dialog-title="${escapeHtml(result.dialog_title)}" data-avatar-url="${escapeHtml(result.avatar_url || "")}">
      ${avatarMarkup(result.avatar_url, result.dialog_title, "search-avatar")}
      <span class="search-result-content">
        <span class="search-result-head"><strong>${escapeHtml(result.dialog_title)}</strong><time>${escapeHtml(formatDate(result.date, true))}</time></span>
        <span class="search-result-sender">${escapeHtml(result.sender_name)}</span>
        <span class="search-result-text">${escapeHtml(result.text)}</span>
      </span>
    </button>`).join("");
}

function updateNewDialogFields() {
  const kind = el("new-dialog-kind").value;
  const isPrivate = kind === "private";
  el("new-dialog-private-fields").classList.toggle("hidden", !isPrivate);
  el("new-dialog-collective-fields").classList.toggle("hidden", isPrivate);
  el("new-dialog-title").textContent = isPrivate ? "Новый личный чат" : kind === "group" ? "Новая группа" : "Новый канал";
  el("participants-hint").textContent = kind === "group"
    ? "обязательно, по одному username или телефону в строке"
    : "необязательно, по одному username или телефону в строке";
}

function openNewDialogModal() {
  if (!state.crmAccountId) return showToast("Сначала выберите Telegram-аккаунт", "error");
  el("new-dialog-status").textContent = "";
  el("new-dialog-status").className = "form-status modal-status";
  updateNewDialogFields();
  el("new-dialog-modal").classList.remove("hidden");
  window.setTimeout(() => {
    (el("new-dialog-kind").value === "private" ? el("new-dialog-target") : el("new-dialog-name")).focus();
  }, 0);
}

function closeNewDialogModal() {
  el("new-dialog-modal").classList.add("hidden");
}

async function refreshAccounts() {
  try {
    renderAccounts(await request("/accounts"));
  } catch (error) {
    setStatus(error.message, "error");
  }
}

el("phone-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  setStatus("Запрашиваем код у Telegram…");
  try {
    const data = await request("/accounts/login/start", {
      method: "POST",
      body: JSON.stringify({ phone: el("phone").value.trim(), label: el("label").value.trim() || null }),
    });
    state.accountId = data.account_id;
    el("phone-form").classList.add("hidden");
    el("code-form").classList.remove("hidden");
    el("code").focus();
    setStatus("Код отправлен. Введите его, чтобы продолжить.", "success");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

el("code-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const data = await request(`/accounts/${state.accountId}/login/complete`, {
      method: "POST", body: JSON.stringify({ code: el("code").value.trim() }),
    });
    if (data.next_step === "password_required") {
      el("code-form").classList.add("hidden");
      el("password-form").classList.remove("hidden");
      el("password").focus();
      return setStatus("Нужен пароль двухэтапной проверки.");
    }
    setStatus("Аккаунт подключён.", "success");
    await refreshAccounts();
    switchView("accounts");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

el("password-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    await request(`/accounts/${state.accountId}/login/password`, {
      method: "POST", body: JSON.stringify({ password: el("password").value }),
    });
    el("password").value = "";
    setStatus("Аккаунт подключён.", "success");
    await refreshAccounts();
    switchView("accounts");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

el("refresh-button").addEventListener("click", async () => {
  const button = el("refresh-button");
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Обновляем…";
  state.pollBusy = true;
  try {
    if (state.view === "crm" && state.crmAccountId) {
      const dialogs = await request(`/accounts/${state.crmAccountId}/dialogs?limit=50&force=true`);
      state.dialogsSignature = "";
      updateDialogs(dialogs);
      if (state.dialogRef) {
        const messages = await request(`/accounts/${state.crmAccountId}/chat-history`, {
          method: "POST",
          body: JSON.stringify({ dialog_ref: state.dialogRef, limit: 100, force: true }),
        });
        state.messagesSignature = "";
        updateMessages(messages, true);
      }
    } else {
      await refreshAccounts();
    }
    button.textContent = "Готово";
  } catch (error) {
    button.textContent = "Ошибка";
    setStatus(error.message, "error");
  } finally {
    state.pollBusy = false;
    window.setTimeout(() => { button.textContent = originalText; }, 700);
    button.disabled = false;
  }
});
document.querySelectorAll(".nav-button").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.view));
});
document.querySelectorAll("[data-go-view]").forEach((button) => {
  button.addEventListener("click", () => switchView(button.dataset.goView));
});
el("burger-button").addEventListener("click", () => {
  const mobile = window.matchMedia("(max-width: 920px)").matches;
  if (mobile) {
    const open = !el("app-view").classList.contains("menu-open");
    el("app-view").classList.toggle("menu-open", open);
    el("burger-button").setAttribute("aria-expanded", String(open));
  } else {
    const collapsed = !el("app-view").classList.contains("menu-collapsed");
    el("app-view").classList.toggle("menu-collapsed", collapsed);
    el("burger-button").setAttribute("aria-expanded", String(!collapsed));
  }
});
el("sidebar-overlay").addEventListener("click", closeMenu);
el("crm-account-picker").addEventListener("change", (event) => {
  const account = state.accounts.find((item) => item.id === event.target.value);
  if (account) openCrm(account.id, account.label || account.phone);
  else setAvatar(el("crm-account-avatar"), "", "TG");
});
el("new-dialog-button").addEventListener("click", openNewDialogModal);
el("new-dialog-kind").addEventListener("change", updateNewDialogFields);
el("new-dialog-close").addEventListener("click", closeNewDialogModal);
el("new-dialog-cancel").addEventListener("click", closeNewDialogModal);
el("new-dialog-modal").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeNewDialogModal();
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !el("new-dialog-modal").classList.contains("hidden")) closeNewDialogModal();
});
el("new-dialog-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.crmAccountId) return closeNewDialogModal();
  const kind = el("new-dialog-kind").value;
  const participants = el("new-dialog-participants").value
    .split(/[\n,;]+/)
    .map((value) => value.trim())
    .filter(Boolean);
  const payload = kind === "private"
    ? {
      kind,
      target: el("new-dialog-target").value.trim(),
      first_message: el("new-dialog-first-message").value.trim() || null,
    }
    : {
      kind,
      title: el("new-dialog-name").value.trim(),
      about: el("new-dialog-about").value.trim() || null,
      participants,
    };
  if (kind === "private" && !payload.target) {
    el("new-dialog-status").textContent = "Укажите @username или телефон.";
    return el("new-dialog-status").classList.add("error");
  }
  if (kind !== "private" && !payload.title) {
    el("new-dialog-status").textContent = "Введите название.";
    return el("new-dialog-status").classList.add("error");
  }
  if (kind === "group" && !participants.length) {
    el("new-dialog-status").textContent = "Для группы нужен хотя бы один участник.";
    return el("new-dialog-status").classList.add("error");
  }

  const button = event.submitter;
  button.disabled = true;
  el("new-dialog-status").textContent = kind === "private" ? "Ищем пользователя…" : "Создаём в Telegram…";
  el("new-dialog-status").className = "form-status modal-status";
  try {
    const response = await request(`/accounts/${state.crmAccountId}/dialogs/create`, {
      method: "POST",
      body: JSON.stringify(payload),
    });
    closeNewDialogModal();
    el("new-dialog-form").reset();
    updateNewDialogFields();
    state.dialogsSignature = "";
    try {
      updateDialogs(await request(`/accounts/${state.crmAccountId}/dialogs?limit=50&force=true`));
    } catch (_) {
      // The returned dialog can still be opened even if refreshing the list is delayed.
    }
    const dialog = response.dialog;
    await openDialog(dialog.ref, dialog.title, dialog.key, dialog.avatar_url);
    showToast(response.warnings?.[0] || (kind === "private" ? "Чат открыт" : "Создано в Telegram"), response.warnings?.length ? "error" : "success");
  } catch (error) {
    el("new-dialog-status").textContent = error.message;
    el("new-dialog-status").className = "form-status modal-status error";
  } finally {
    button.disabled = false;
  }
});
el("admin-login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  el("login-status").textContent = "Проверяем доступ…";
  try {
    const data = await request("/auth/login", {
      method: "POST",
      body: JSON.stringify({
        username: el("admin-username").value.trim(),
        password: el("admin-password").value,
      }),
    });
    el("admin-password").value = "";
    showApp(data.username);
    await refreshAccounts();
  } catch (error) {
    showLogin(error.message);
  } finally {
    button.disabled = false;
  }
});
el("logout-button").addEventListener("click", async () => {
  try { await request("/auth/logout", { method: "POST", body: "{}" }); } catch (_) { /* Session may already be gone. */ }
  showLogin();
});
el("accounts-list").addEventListener("click", (event) => {
  const crmButton = event.target.closest(".open-crm-button");
  if (crmButton) return openCrm(crmButton.dataset.accountId, crmButton.dataset.accountTitle);
  const revokeButton = event.target.closest(".revoke-sessions-button");
  if (!revokeButton) return;
  const acknowledged = window.confirm(
    `Закрыть все другие Telegram-сессии аккаунта «${revokeButton.dataset.accountTitle}»?\n\n` +
    "Это отключит Telegram на остальных телефонах, компьютерах и веб-клиентах. Текущая CRM-сессия останется активной.",
  );
  if (!acknowledged) return;
  const confirmation = window.prompt(
    `Будут закрыты все Telegram-сессии аккаунта «${revokeButton.dataset.accountTitle}», кроме CRM. Введите ЗАКРЫТЬ для подтверждения.`,
  );
  if (confirmation !== "ЗАКРЫТЬ") return;
  revokeButton.disabled = true;
  const originalText = revokeButton.textContent;
  revokeButton.textContent = "Закрываем…";
  request(`/accounts/${revokeButton.dataset.accountId}/sessions/revoke-others`, {
    method: "POST",
    body: JSON.stringify({ confirmation: "REVOKE" }),
  }).then((result) => {
    revokeButton.textContent = `Закрыто: ${result.revoked_count}`;
  }).catch((error) => {
    revokeButton.textContent = "Ошибка";
    window.alert(error.message);
  }).finally(() => {
    window.setTimeout(() => { revokeButton.textContent = originalText; }, 1800);
    revokeButton.disabled = false;
  });
});
el("dialogs-list").addEventListener("click", (event) => {
  const button = event.target.closest(".dialog-button");
  if (button) openDialog(button.dataset.dialogRef, button.dataset.dialogTitle, button.dataset.dialogKey, button.dataset.avatarUrl, button);
});
el("chat-info-button").addEventListener("click", () => {
  const panel = el("chat-profile-panel");
  const open = panel.classList.contains("hidden");
  panel.classList.toggle("hidden", !open);
  el("chat-info-button").setAttribute("aria-expanded", String(open));
});
el("global-search-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const query = el("global-search-input").value.trim();
  if (!state.crmAccountId) return showToast("Сначала выберите Telegram-аккаунт", "error");
  if (query.length < 2) return showToast("Введите минимум 2 символа", "error");
  const button = event.submitter;
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "Ищем…";
  el("global-search-panel").classList.remove("hidden");
  el("global-search-title").textContent = `Ищем «${query}»…`;
  el("global-search-results").innerHTML = '<div class="loading-state">Telegram выполняет глобальный поиск…</div>';
  try {
    const results = await request(`/accounts/${state.crmAccountId}/search?q=${encodeURIComponent(query)}&limit=30`);
    renderSearchResults(results, query);
  } catch (error) {
    el("global-search-results").innerHTML = `<div class="empty-state">${escapeHtml(error.message)}</div>`;
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
});
el("global-search-close").addEventListener("click", () => el("global-search-panel").classList.add("hidden"));
el("global-search-results").addEventListener("click", (event) => {
  const result = event.target.closest(".search-result");
  if (!result) return;
  el("global-search-panel").classList.add("hidden");
  openDialog(result.dataset.dialogRef, result.dataset.dialogTitle, result.dataset.dialogKey, result.dataset.avatarUrl);
});
el("messages-list").addEventListener("click", async (event) => {
  const button = event.target.closest(".bot-callback-button");
  if (!button || !state.crmAccountId || !state.dialogRef) return;
  button.disabled = true;
  try {
    const answer = await request(`/accounts/${state.crmAccountId}/bot-button`, {
      method: "POST",
      body: JSON.stringify({
        dialog_ref: state.dialogRef,
        message_id: Number(button.dataset.messageId),
        row: Number(button.dataset.row),
        column: Number(button.dataset.column),
      }),
    });
    if (answer.message && answer.alert) window.alert(answer.message);
    else showToast(answer.message || "Команда отправлена боту", "success");
    if (answer.url && window.confirm("Бот предлагает открыть внешнюю ссылку. Открыть её в новой вкладке?")) {
      window.open(answer.url, "_blank", "noopener,noreferrer");
    }
    state.messagesSignature = "";
    const messages = await request(`/accounts/${state.crmAccountId}/chat-history`, {
      method: "POST",
      body: JSON.stringify({ dialog_ref: state.dialogRef, limit: 100, force: true }),
    });
    updateMessages(messages, true);
  } catch (error) {
    showToast(error.message, "error");
    button.disabled = false;
  }
});
el("message-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!state.crmAccountId || !state.dialogRef) return;
  const text = el("message-text").value.trim();
  if (!text) return;
  const button = event.submitter || event.currentTarget.querySelector('button[type="submit"]');
  button.disabled = true;
  el("composer-status").textContent = "Отправляем…";
  el("composer-status").className = "composer-status";
  try {
    const sent = await request(`/accounts/${state.crmAccountId}/send-message`, {
      method: "POST",
      body: JSON.stringify({ dialog_ref: state.dialogRef, text }),
    });
    el("message-text").value = "";
    state.messagesSignature = "";
    updateMessages([...state.messages, sent], false);
    el("composer-status").textContent = "Сообщение отправлено";
    el("composer-status").className = "composer-status success";
    el("message-text").focus();
  } catch (error) {
    el("composer-status").textContent = error.message;
    el("composer-status").className = "composer-status error";
  } finally {
    button.disabled = false;
  }
});
el("message-text").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && event.ctrlKey) {
    event.preventDefault();
    el("message-form").requestSubmit();
  }
});

async function pollActiveCrm() {
  if (
    state.pollBusy ||
    document.hidden ||
    state.view !== "crm" ||
    !state.crmAccountId ||
    el("app-view").classList.contains("hidden")
  ) return;
  state.pollBusy = true;
  try {
    const dialogs = await request(`/accounts/${state.crmAccountId}/dialogs?limit=50`);
    updateDialogs(dialogs);
    if (state.dialogRef) {
      const messages = await request(`/accounts/${state.crmAccountId}/chat-history`, {
        method: "POST",
        body: JSON.stringify({ dialog_ref: state.dialogRef, limit: 100 }),
      });
      updateMessages(messages, true);
    }
  } catch (_) {
    // The next one-second tick retries without replacing the current CRM view.
  } finally {
    state.pollBusy = false;
  }
}

window.setInterval(pollActiveCrm, 1000);

document.addEventListener("error", (event) => {
  if (event.target.matches?.("img.avatar-image")) event.target.classList.add("hidden");
}, true);

async function initialize() {
  try {
    const admin = await request("/auth/me");
    showApp(admin.username);
    await refreshAccounts();
  } catch (_) {
    showLogin();
  }
}

initialize();
