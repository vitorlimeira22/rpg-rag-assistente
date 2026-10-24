"use strict";

// A ficha é a única fonte de estado do editor e da conversa. Revisões impedem
// que uma resposta atrasada substitua uma edição mais recente.
const sheetState = { current: null, list: [], busy: false, dirty: false, pending: null, rowId: 0,
  rows: { skills: [], powers: [], equipment: [], rituals: [] } };
const sheetKeys = ["name", "player", "origin", "track", "rank", "notes"];
const sheetAttributeLabels = { agilidade: "AGI", forca: "FOR", intelecto: "INT", presenca: "PRE", vigor: "VIG" };
const sheetCollections = {
  skills: [{ key: "name", label: "Perícia", max: 80 }, { key: "bonus", label: "Treinamento", type: "training" },
    { key: "attribute", label: "Atributo", type: "attribute" }, { key: "notes", label: "Observações", max: 200 }],
  powers: [{ key: "name", label: "Poder ou habilidade", max: 100 }, { key: "description", label: "Efeito e condições", type: "textarea", max: 3000 }],
  equipment: [{ key: "name", label: "Item", max: 100 }, { key: "quantity", label: "Quantidade", type: "number", min: 1, max: 99 },
    { key: "category", label: "Categoria", type: "number", min: 0, max: 4 }, { key: "spaces", label: "Espaços por item", type: "number", min: 0, max: 100, step: "any" },
    { key: "description", label: "Detalhes", type: "textarea", max: 3000 }],
  rituals: [{ key: "name", label: "Ritual", max: 100 }, { key: "description", label: "Execução, custo e efeito", type: "textarea", max: 3000 }],
};

function blankSheet() {
  return { name: "Novo agente", player: "", character_class: "combatente", nex: 5, origin: "", track: "", rank: "Recruta",
    attributes: Object.fromEntries(attributes.map((key) => [key, 1])), skills: [], powers: [], equipment: [], rituals: [], notes: "" };
}

function sheetNotice(message, error = false) { feedback("sheet-feedback", message, error); }

function storeSheetPending(pending) {
  sheetState.pending = pending;
  if (!sheetState.current) return;
  try {
    const key = `rpg-sheet-pending-${sheetState.current.id}`;
    if (pending) localStorage.setItem(key, JSON.stringify(pending)); else localStorage.removeItem(key);
  } catch { /* O fluxo também funciona quando o armazenamento do navegador é bloqueado. */ }
}

function restoreSheetPending() {
  sheetState.pending = null;
  if (!sheetState.current) return;
  try {
    const pending = JSON.parse(localStorage.getItem(`rpg-sheet-pending-${sheetState.current.id}`) || "null");
    if (pending && typeof pending.content === "string" && typeof pending.request_id === "string" && Number.isSafeInteger(pending.revision)) sheetState.pending = pending;
  } catch { /* Uma entrada inválida não deve impedir a abertura da ficha. */ }
  if (sheetState.pending && (sheetState.current.messages || []).some((message) => message.role === "assistant" && message.request_id === sheetState.pending.request_id)) storeSheetPending(null);
}

function syncSheetControls() {
  const pendingServer = Boolean(sheetState.current?.pending_request_id && !sheetState.current?.pending_expired);
  const frozen = sheetState.busy || Boolean(sheetState.pending) || pendingServer;
  $("sheet-form").setAttribute("aria-busy", String(sheetState.busy));
  $("sheet-form").querySelectorAll("input, select, textarea, button").forEach((control) => { control.disabled = frozen; });
  $("sheet-chat-form").querySelectorAll("textarea, button").forEach((control) => { control.disabled = frozen; });
  if (sheetState.current?.book_matches === false) $("sheet-send").disabled = true;
  ["sheet-picker", "sheet-new"].forEach((id) => { $(id).disabled = frozen; });
  $("sheet-reload").disabled = sheetState.busy || !sheetState.current;
  $("sheet-discard").disabled = frozen || !sheetState.dirty;
  $("sheet-export").disabled = frozen || sheetState.dirty || !sheetState.current?.pdf_available;
  $("sheet-chat-retry").classList.toggle("hidden", !sheetState.pending);
  $("sheet-chat-retry").disabled = sheetState.busy;
  $("sheet-save-status").textContent = sheetState.busy ? "ATUALIZANDO…" : sheetState.pending ? "ENVIO A CONFIRMAR"
    : sheetState.dirty ? "ALTERAÇÕES NÃO SALVAS" : sheetState.current ? `SALVO · REVISÃO ${sheetState.current.revision}` : "NOVA FICHA";
}

function setSheetBusy(value) { sheetState.busy = value; syncSheetControls(); }

function sheetChanged() {
  if (sheetState.busy || sheetState.pending) return;
  sheetState.dirty = true;
  $("sheet-document-title").textContent = $("sheet-name").value.trim() || "Novo personagem";
  const total = attributes.reduce((sum, key) => sum + Number($(`sheet-${key}`).value || 0), 0);
  $("sheet-attribute-summary").textContent = `Soma dos atributos: ${total}. A distribuição e a evolução são conferidas ao salvar.`;
  $("sheet-derived-help").textContent = "Você alterou a ficha. Salve para atualizar os cálculos e a conferência.";
  $("sheet-validation").classList.add("sheet-validation-stale");
  syncSheetControls();
}

function addSheetRow(kind, value = {}, markDirty = true) {
  if ((sheetState.busy || sheetState.pending) && markDirty) return;
  if (sheetState.rows[kind].length >= (kind === "skills" ? 40 : 30)) { sheetNotice("Esta seção atingiu o limite de entradas por ficha.", true); return; }
  const host = $(`sheet-${kind}`), row = element("div", `sheet-row sheet-row-${kind}`), fields = {};
  row.dataset.sheetRow = kind;
  const uid = ++sheetState.rowId;
  for (const spec of sheetCollections[kind]) {
    const wrapper = element("div", `field sheet-row-field${spec.type === "textarea" ? " sheet-row-wide" : ""}`);
    const label = element("label", "", spec.label), input = element(["attribute", "training"].includes(spec.type) ? "select" : spec.type === "textarea" ? "textarea" : "input");
    input.id = `sheet-row-${uid}-${spec.key}`; label.htmlFor = input.id;
    if (spec.type === "attribute") input.append(new Option("—", ""), ...attributes.map((key) => new Option(sheetAttributeLabels[key], key)));
    else if (spec.type === "training") {
      input.append(...[[0, "Destreinado (+0)"], [5, "Treinado (+5)"], [10, "Veterano (+10)"], [15, "Expert (+15)"]].map(([number, text]) => new Option(text, String(number))));
      if (value.bonus != null && ![0, 5, 10, 15].includes(value.bonus)) input.append(new Option(`${value.bonus} · revisar`, String(value.bonus)));
    }
    else if (spec.type === "number") { input.type = "number"; input.min = spec.min; input.max = spec.max; input.step = spec.step || 1; input.required = true; }
    else { input.maxLength = spec.max; if (spec.type === "textarea") input.rows = 2; else input.type = "text"; }
    input.value = value[spec.key] ?? (spec.key === "quantity" || spec.key === "spaces" ? 1 : spec.key === "bonus" ? 5 : spec.type === "number" ? 0 : "");
    input.addEventListener("input", sheetChanged); input.addEventListener("change", sheetChanged);
    wrapper.append(label, input); row.append(wrapper); fields[spec.key] = input;
  }
  const remove = element("button", "sheet-row-remove", "Remover"); remove.type = "button";
  remove.setAttribute("aria-label", `Remover ${kind === "skills" ? "perícia" : kind === "equipment" ? "item" : kind === "rituals" ? "ritual" : "poder"}`);
  const entry = { node: row, fields };
  remove.addEventListener("click", () => {
    if (sheetState.busy || sheetState.pending) return;
    sheetState.rows[kind] = sheetState.rows[kind].filter((item) => item !== entry); row.remove(); updateSheetRowEmpty(kind); sheetChanged();
  });
  row.append(remove); sheetState.rows[kind].push(entry); host.append(row); updateSheetRowEmpty(kind);
  if (markDirty) { sheetChanged(); fields.name.focus(); }
}

function updateSheetRowEmpty(kind) {
  const host = $(`sheet-${kind}`);
  host.querySelectorAll(".sheet-row-empty").forEach((node) => node.remove());
  if (!sheetState.rows[kind].length) host.append(element("p", "sheet-row-empty tiny muted", "Nenhuma escolha registrada. Adicione manualmente ou peça sugestões no chat."));
}

function readSheetDraft() {
  const data = { ...Object.fromEntries(sheetKeys.map((key) => [key, $(`sheet-${key}`).value.trim()])),
    character_class: $("sheet-class").value, nex: Number($("sheet-nex").value),
    attributes: Object.fromEntries(attributes.map((key) => [key, Number($(`sheet-${key}`).value)])) };
  for (const [kind, specs] of Object.entries(sheetCollections)) data[kind] = sheetState.rows[kind].map(({ fields }) => Object.fromEntries(specs.map((spec) => [spec.key,
    ["number", "training"].includes(spec.type) ? Number(fields[spec.key].value) : fields[spec.key].value.trim()]))).filter((item) => item.name || Object.entries(item).some(([key, value]) => key !== "name" && typeof value === "string" && value));
  return data;
}

function fillSheet(data) {
  const defaults = blankSheet();
  sheetKeys.forEach((key) => { $(`sheet-${key}`).value = data[key] ?? defaults[key]; });
  $("sheet-class").value = data.character_class || "combatente"; $("sheet-nex").value = data.nex ?? 5;
  attributes.forEach((key) => { $(`sheet-${key}`).value = data.attributes?.[key] ?? 1; });
  for (const kind of Object.keys(sheetCollections)) {
    $(`sheet-${kind}`).replaceChildren(); sheetState.rows[kind] = [];
    (data[kind] || []).forEach((item) => addSheetRow(kind, item, false)); updateSheetRowEmpty(kind);
  }
  $("sheet-document-title").textContent = data.name || "Novo personagem";
  $("sheet-attribute-summary").textContent = `Soma dos atributos: ${attributes.reduce((sum, key) => sum + Number(data.attributes?.[key] ?? 1), 0)}.`;
}

function renderSheetValidation(record) {
  const report = record?.validation || {}, container = $("sheet-validation");
  container.replaceChildren(); container.classList.remove("sheet-validation-stale");
  if (record) {
    const title = element("h3", "", report.valid === false ? "Conferência · há pendências" : "Conferência da ficha"); container.append(title);
    if (report.errors?.length) {
      const labels = { name: "Nome", character_class: "Classe", nex: "NEX", origin: "Origem", track: "Trilha", rank: "Patente", attributes: "Atributos", skills: "Perícias", powers: "Poderes", equipment: "Equipamento", rituals: "Rituais" };
      container.append(element("p", "notice error", detailText(report.errors.map((issue) => typeof issue === "string" ? issue : { ...issue, field: labels[issue.field] || issue.field }))));
    }
    const scope = typeof report.scope === "string" ? report.scope : report.scope?.description;
    if (scope) container.append(element("p", "small muted", scope));
    if (report.warnings?.length) { const list = element("ul", "tiny muted"); report.warnings.forEach((warning) => list.append(element("li", "", typeof warning === "string" ? warning : detailText(warning)))); container.append(list); }
    if (!scope && !report.warnings?.length) container.append(element("p", "tiny muted", "Salvo como rascunho. A conferência automática cobre apenas as regras implementadas; confira as escolhas no livro."));
  }
  const derived = record?.data?.derived || report.derived || {};
  const aliases = [ ["PV", "pv", "pv_base"], ["PE", "pe", "pe_base"], ["SAN", "san", "san_base"], ["Defesa", "defense", "defesa_base"], ["PE / turno", "pe_per_turn", "limite_pe_base"] ];
  $("sheet-derived").replaceChildren(...aliases.map(([label, key, oldKey]) => {
    const box = element("div", "sheet-stat"); box.append(element("span", "", label), element("strong", "", derived[key] ?? derived[oldKey] ?? "—")); return box;
  }));
  $("sheet-derived-help").textContent = "Valores calculados: confira os efeitos incluídos na conferência abaixo. O traço indica um valor ainda não calculado.";
  $("sheet-pdf-help").textContent = !record ? "Salve a ficha para verificar a exportação no modelo PDF. Revise os campos antes de usá-la na mesa." : record.pdf_available
    ? "O PDF usa a ficha salva. Salve suas alterações antes de exportar e confira eventuais pendências."
    : "Exportação disponível quando o modelo de ficha PDF estiver configurado no servidor. Você já pode editar, conversar e salvar aqui.";
}

function renderSheetMessages() {
  const feed = $("sheet-messages"); feed.replaceChildren();
  const messages = sheetState.current?.messages || [];
  for (const [index, message] of messages.entries()) {
    const article = element("article", `chat-message ${message.role === "user" ? "chat-user" : "chat-assistant"}`);
    if (message.role === "user") article.append(element("p", "chat-user-text", message.content));
    else {
      const prefix = `sheet-${sheetState.current.id}-message-${message.id ?? index}`, answer = element("div"), sources = element("section", "sources-section"), disclosure = element("details", "chat-source-disclosure");
      const result = message.result || { answer: message.content };
      renderResult(prefix, result, false, { container: answer, sourcesContainer: sources, collapsedSources: true, historical: true });
      article.append(answer);
      if (result.sources?.length) { disclosure.append(element("summary", "", `Conferir fontes (${result.sources.length})`), sources); article.append(disclosure); }
    }
    feed.append(article);
  }
  if (sheetState.pending && !messages.some((message) => message.role === "user" && message.request_id === sheetState.pending.request_id)) {
    const pending = element("article", "chat-message chat-user"); pending.append(element("p", "chat-user-text", sheetState.pending.content)); feed.append(pending);
  }
  if (!messages.length && !sheetState.pending) {
    const empty = element("div", "sheet-chat-empty"); empty.append(element("span", "eyebrow", "UMA FICHA, UMA CONVERSA"), element("h3", "", "Comece pelo conceito."),
      element("p", "small muted", "“Quero um agente que combate à distância.” Você também pode pedir uma ficha completa, escolher atributos ou revisar equipamentos.")); feed.append(empty);
  }
  feed.scrollTop = feed.scrollHeight;
}

function applySheetRecord(record) {
  if (!record || !Number.isSafeInteger(record.id) || !Number.isSafeInteger(record.revision) || !record.data) throw new Error("A ficha retornou um formato inesperado.");
  sheetState.current = record; sheetState.dirty = false;
  try { localStorage.setItem("rpg-active-sheet", String(record.id)); } catch { /* Opcional. */ }
  fillSheet(record.data); renderSheetValidation(record); restoreSheetPending(); renderSheetMessages(); syncSheetControls();
  $("sheet-chat-status").textContent = record.book_matches === false ? "O livro ativo mudou. Você pode editar esta ficha manualmente; para usar o assistente com o novo livro, crie outra ficha."
    : sheetState.pending ? "O último envio ainda precisa de confirmação. Retome o mesmo envio ou reabra a ficha salva."
    : record.pending_request_id && !record.pending_expired ? "Há uma resposta em andamento para esta ficha. Use “Reabrir salva” para conferir quando estiver concluída." : "";
  $("sheet-picker").value = String(record.id);
}

async function loadSheetList(restore = false) {
  try {
    sheetState.list = await request("/api/sheets");
    $("sheet-picker").replaceChildren(new Option("Nova ficha", ""), ...sheetState.list.map((item) => new Option(`${item.name} · ${classNames[item.character_class] || item.character_class} · NEX ${item.nex}%`, String(item.id))));
    $("sheet-picker").value = sheetState.current ? String(sheetState.current.id) : "";
    if (restore && !sheetState.current && !sheetState.dirty && !sheetState.busy) {
      let id = null; try { id = Number(localStorage.getItem("rpg-active-sheet")); } catch { /* Opcional. */ }
      if (sheetState.list.some((item) => item.id === id)) await openSheet(id);
    }
  } catch (error) { sheetNotice(`Não foi possível atualizar a lista de fichas. ${error.message}`, true); }
}

function sheetCanLeave() {
  if (sheetState.busy || sheetState.pending) return false;
  if (sheetState.dirty) { sheetNotice("Salve ou desfaça as alterações antes de trocar de personagem.", true); $("sheet-picker").value = sheetState.current ? String(sheetState.current.id) : ""; return false; }
  return true;
}

async function openSheet(id, reload = false) {
  if (sheetState.busy || (!reload && !sheetCanLeave())) return;
  if (reload && sheetState.dirty) { sheetNotice("Há alterações não salvas. Salve ou use “Desfazer edição” antes de reabrir.", true); return; }
  if (!Number.isSafeInteger(id) || id < 1) return;
  setSheetBusy(true); sheetNotice("Abrindo ficha…");
  try { applySheetRecord(await request(`/api/sheets/${id}`)); sheetNotice("Ficha carregada."); }
  catch (error) { sheetNotice(error.message, true); }
  finally { setSheetBusy(false); }
}

function newSheet() {
  if (!sheetCanLeave()) return;
  sheetState.current = null; sheetState.pending = null; sheetState.dirty = false;
  try { localStorage.removeItem("rpg-active-sheet"); } catch { /* Opcional. */ }
  fillSheet(blankSheet()); renderSheetValidation(null); renderSheetMessages(); syncSheetControls();
  $("sheet-picker").value = ""; $("sheet-question").value = ""; $("sheet-chat-status").textContent = "";
  sheetNotice("Nova ficha. Preencha os campos ou peça uma proposta ao assistente."); $("sheet-name").focus();
}

async function persistSheet() {
  const current = sheetState.current, data = readSheetDraft();
  const record = await jsonRequest(current ? `/api/sheets/${current.id}` : "/api/sheets", current ? "PUT" : "POST", current ? { revision: current.revision, data } : data);
  applySheetRecord(record); return record;
}

async function saveSheet(event) {
  event?.preventDefault();
  if (sheetState.busy || sheetState.pending || !$("sheet-form").reportValidity()) return;
  setSheetBusy(true); sheetNotice("Salvando e conferindo a ficha…");
  try { await persistSheet(); sheetNotice("Ficha salva. Confira as pendências indicadas abaixo."); await loadSheetList(); }
  catch (error) { sheetNotice(error.status === 409 ? "A ficha foi alterada em outra sessão. Sua edição foi preservada na tela; copie os ajustes antes de reabrir a versão salva." : error.message, true); }
  finally { setSheetBusy(false); }
}

async function sendSheetPending() {
  if (sheetState.busy || !sheetState.pending || !sheetState.current) return;
  const pending = sheetState.pending, id = sheetState.current.id;
  setSheetBusy(true); sheetNotice("A ficha está salva. O assistente está preparando a resposta.");
  $("sheet-chat-status").replaceChildren(element("span", "spinner"), element("span", "", "Consultando o livro e conferindo as escolhas. A ficha será atualizada ao concluir; a cota da API pode aumentar a espera."));
  $("sheet-chat-status").classList.add("working"); renderSheetMessages();
  try {
    const reply = await jsonRequest(`/api/sheets/${id}/chat`, "POST", pending);
    const record = reply.sheet;
    if (record && Array.isArray(reply.messages)) record.messages = reply.messages;
    applySheetRecord(record); storeSheetPending(null); $("sheet-question").value = "";
    renderSheetMessages(); sheetNotice("Resposta recebida. Confira a ficha e as observações do assistente.");
    $("sheet-chat-status").textContent = "Você pode editar os campos ou continuar a conversa."; await loadSheetList();
  } catch (error) {
    if (error.status === 409 && error.data?.detail?.code === "sheet_busy") {
      $("sheet-chat-status").textContent = "A resposta continua em andamento. Aguarde e use “Reabrir salva” ou retome o mesmo envio para conferir.";
    } else if (error.status === 409 || error.status === 422 || error.status === 404) {
      storeSheetPending(null); $("sheet-question").value = pending.content;
      $("sheet-chat-status").textContent = `${error.message} Reabra a ficha salva para conferir o estado antes de tentar novamente.`;
    } else $("sheet-chat-status").textContent = `${error.message} Use “Retomar mesmo envio” para conferir a mesma solicitação sem criar outra.`;
    sheetNotice("A atualização não foi confirmada. Seus dados e sua mensagem foram preservados.", true);
  } finally { $("sheet-chat-status").classList.remove("working"); setSheetBusy(false); }
}

async function submitSheetChat(event) {
  event?.preventDefault();
  if (sheetState.busy || sheetState.pending || !$("sheet-form").reportValidity()) return;
  const content = $("sheet-question").value.trim(); if (!content) return;
  setSheetBusy(true);
  try {
    if (!sheetState.current || sheetState.dirty) await persistSheet();
    storeSheetPending({ content, request_id: crypto.randomUUID(), revision: sheetState.current.revision });
  } catch (error) { sheetNotice(`A mensagem não foi enviada porque a ficha não pôde ser salva. ${error.message}`, true); }
  finally { setSheetBusy(false); }
  if (sheetState.pending) await sendSheetPending();
}

function setSheetPanel(panel, focus = false) {
  $("sheet-workspace").dataset.mobilePanel = panel;
  for (const key of ["editor", "chat"]) { const button = $(`sheet-tab-${key}`); button.setAttribute("aria-selected", String(key === panel)); button.tabIndex = key === panel ? 0 : -1; }
  if (focus) $(`sheet-tab-${panel}`).focus();
}

async function downloadSheetPdf() {
  if (sheetState.busy || sheetState.dirty || sheetState.pending || !sheetState.current?.pdf_available) return;
  const id = sheetState.current.id, controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 60000);
  setSheetBusy(true); sheetNotice("Preparando o PDF da ficha salva…");
  try {
    const response = await fetch(`/api/sheets/${id}/pdf`, { signal: controller.signal, headers: { Accept: "application/pdf" } });
    if (!response.ok) {
      let message = "Não foi possível exportar esta ficha.";
      try { const data = await response.json(); message = detailText(data.detail || data); } catch { /* Mantém mensagem segura. */ }
      throw new Error(message);
    }
    const url = URL.createObjectURL(await response.blob()), link = element("a");
    link.href = url; link.download = "ficha-do-agente.pdf"; document.body.append(link);
    try { link.click(); } finally { link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000); }
    sheetNotice("PDF preparado para download. Confira os campos e as pendências antes de usar a ficha.");
  } catch (error) { sheetNotice(error.name === "AbortError" ? "A exportação demorou além do esperado. Tente novamente." : error.message, true); }
  finally { clearTimeout(timeout); setSheetBusy(false); }
}

const sheetBootstrap = true;
$("sheet-form").addEventListener("submit", saveSheet);
$("sheet-form").addEventListener("input", sheetChanged);
$("sheet-form").addEventListener("change", sheetChanged);
$("sheet-chat-form").addEventListener("submit", submitSheetChat);
$("sheet-question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) { event.preventDefault(); if (!$("sheet-send").disabled) $("sheet-chat-form").requestSubmit(); }
});
$("sheet-picker").addEventListener("change", () => { const id = Number($("sheet-picker").value); if (id) openSheet(id); else newSheet(); });
$("sheet-new").addEventListener("click", newSheet);
$("sheet-reload").addEventListener("click", () => { if (sheetState.current) openSheet(sheetState.current.id, true); });
$("sheet-discard").addEventListener("click", () => {
  if (sheetState.busy || sheetState.pending) return;
  sheetState.dirty = false; fillSheet(sheetState.current?.data || blankSheet()); renderSheetValidation(sheetState.current); syncSheetControls(); sheetNotice("Edição desfeita. Os valores salvos foram restaurados.");
});
$("sheet-export").addEventListener("click", downloadSheetPdf);
$("sheet-chat-retry").addEventListener("click", sendSheetPending);
document.querySelectorAll("[data-sheet-add]").forEach((button) => button.addEventListener("click", () => addSheetRow(button.dataset.sheetAdd)));
for (const key of ["editor", "chat"]) {
  $(`sheet-tab-${key}`).addEventListener("click", () => setSheetPanel(key));
  $(`sheet-tab-${key}`).addEventListener("keydown", (event) => { if (["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) { event.preventDefault(); setSheetPanel(event.key === "Home" ? "editor" : event.key === "End" ? "chat" : key === "editor" ? "chat" : "editor", true); } });
}
window.addEventListener("beforeunload", (event) => { if (sheetState.dirty) { event.preventDefault(); event.returnValue = ""; } });
fillSheet(blankSheet()); renderSheetValidation(null); renderSheetMessages(); syncSheetControls(); loadSheetList(true);
