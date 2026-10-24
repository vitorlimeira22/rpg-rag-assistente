"use strict";

// Conteúdo do livro, do usuário e do modelo é sempre texto, nunca HTML.
const $ = (id) => document.getElementById(id);
const state = {
  view: "consulta", status: null, generating: false, campaigns: [],
  campaignId: null,
  campaignBusy: false,
  conversations: [], conversation: null, conversationLoading: false,
  conversationEpoch: 0, conversationListEpoch: 0, chatSending: false,
  drafts: new Map(), pendingMessages: new Map(),
};
const viewNames = { consulta: "Consultar livro", missoes: "Criar missões", fichas: "Fichas", campanha: "Campanha" };
const attributes = ["agilidade", "forca", "intelecto", "presenca", "vigor"];
const classNames = { combatente: "Combatente", especialista: "Especialista", ocultista: "Ocultista" };
const derivedLabels = { pv_base: "Pontos de vida (base)", pe_base: "Pontos de esforço (base)", san_base: "Sanidade (base)", defesa_base: "Defesa base", limite_pe_base: "Limite de PE por turno (base)", attribute_total: "Soma dos atributos" };

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((item) => {
    if (typeof item === "string") return item;
    const field = item.field || (item.loc || []).filter((part) => part !== "body").join(".");
    return `${field ? `${field}: ` : ""}${item.message || item.msg || "Valor inválido."}`;
  }).join("\n");
  if (detail && typeof detail === "object") {
    return detailText(detail.errors || detail.message || detail.detail) || "Revise os dados informados.";
  }
  return "Não foi possível concluir a operação.";
}

async function request(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 150000);
  try {
    const response = await fetch(url, {
      ...options, signal: controller.signal,
      headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers },
    });
    if (response.status === 204 && response.ok) return null;
    let data;
    try { data = await response.json(); }
    catch { throw new Error("O serviço não retornou uma resposta válida. Confira se o servidor local está funcionando."); }
    if (!response.ok) {
      const error = new Error(detailText(data.detail || data));
      error.data = data;
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("A espera excedeu dois minutos e meio. A operação pode ainda estar em andamento no servidor; confira o estado antes de repetir.");
    if (error instanceof TypeError) throw new Error("Não foi possível acessar o servidor local. Confira se ele está funcionando e tente novamente.");
    throw error;
  } finally { clearTimeout(timer); }
}

function jsonRequest(url, method, body) {
  return request(url, { method, body: JSON.stringify(body) });
}

function showNotice(container, message, kind = "") {
  container.replaceChildren(element("div", `notice ${kind}`.trim(), message));
  container.classList.remove("hidden");
}

function feedback(id, message, error = false) {
  const node = $(id);
  node.textContent = message;
  node.classList.toggle("error", error);
}

function setView(name, focus = false) {
  if (!viewNames[name]) name = "consulta";
  state.view = name;
  for (const view of Object.keys(viewNames)) $(`view-${view}`).classList.toggle("hidden", view !== name);
  document.querySelectorAll("[data-view]").forEach((link) => {
    const active = link.dataset.view === name;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page");
    else link.removeAttribute("aria-current");
  });
  $("current-view-name").textContent = viewNames[name];
  document.title = `${viewNames[name]} · Arquivo Paranormal`;
  if (focus) $("main").focus({ preventScroll: true });
}

function syncGenerationButtons() {
  const noBook = state.status && !state.status.book;
  const busy = state.generating || state.chatSending;
  ["search", "generate-mission"].forEach((id) => { $(id).disabled = busy || Boolean(noBook); });
  $("ask").disabled = busy || Boolean(noBook) || state.conversationLoading
    || state.conversation?.book_matches === false || Boolean(currentPending());
  $("question").readOnly = state.chatSending || state.conversationLoading;
  $("new-conversation").disabled = state.chatSending || state.conversationLoading;
  $("delete-conversation").disabled = state.chatSending || state.conversationLoading || Boolean(currentPending());
  $("retry-message").disabled = busy || state.conversationLoading || Boolean(noBook) || state.conversation?.book_matches === false;
  $("refresh-conversation").disabled = state.chatSending || state.conversationLoading;
  ["chat-mode", "chat-campaign"].forEach((id) => { $(id).disabled = Boolean(state.conversation) || state.chatSending || state.conversationLoading; });
  document.querySelectorAll(".conversation-item").forEach((button) => { button.disabled = state.chatSending || state.conversationLoading; });
}

async function loadStatus() {
  $("refresh-status").disabled = true;
  try {
    const status = await request("/api/status");
    state.status = status;
    $("connection-status").replaceChildren(element("span", "status-dot"), document.createTextNode("Servidor conectado"));
    $("connection-status").classList.remove("offline");
    const book = status.book;
    $("sidebar-book").textContent = book ? book.title : "Nenhum livro importado";
    $("sidebar-edition").textContent = book ? `Edição ${book.edition} · ${book.page_count} páginas` : "Importe o material pela CLI";
    $("book-title").textContent = book ? book.title : "Seu livro ainda não está aqui";
    $("book-meta").textContent = book ? `Edição ${book.edition} · ${book.page_count} páginas no PDF` : "Siga a importação descrita no README do projeto.";
    $("edition-stamp").textContent = book ? `EDIÇÃO ${book.edition}` : "SEM LIVRO";
    $("search-status").textContent = status.vector_ready ? "Textual + semântica" : (book ? "Somente textual" : "Indisponível");
    $("llm-status").textContent = status.llm_configured ? "Groq configurado" : "Chave não configurada";
    $("calls-status").textContent = `${status.used_calls ?? 0} / ${status.max_calls ?? "—"}`;
    const warnings = (status.warnings || []).filter(Boolean);
    if (!book) warnings.unshift("Nenhum livro está importado. Use o comando de importação do README para começar.");
    $("global-message").textContent = warnings.join(" ");
    $("global-message").classList.toggle("hidden", !warnings.length);
    $("global-message").classList.remove("error");
    syncGenerationButtons();
  } catch (error) {
    $("connection-status").replaceChildren(element("span", "status-dot"), document.createTextNode("Sem conexão"));
    $("connection-status").classList.add("offline");
    $("global-message").textContent = error.message;
    $("global-message").classList.remove("hidden");
    $("global-message").classList.add("error");
  } finally { $("refresh-status").disabled = false; }
}

function inlineText(target, text, prefix, sourceCount) {
  text = String(text);
  const pattern = /\*\*([^*]+)\*\*|(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!\w)_([^_\n]+)_(?!\w)|\[(\d+)\]|(<br\s*\/?>)/gi;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    target.append(document.createTextNode(text.slice(end, match.index)));
    if (match[1] !== undefined || match[2] !== undefined || match[3] !== undefined) {
      const formatting = element(match[1] !== undefined ? "strong" : "em");
      inlineText(formatting, match[1] ?? match[2] ?? match[3], prefix, sourceCount);
      target.append(formatting);
    } else if (match[5]) target.append(element("br"));
    else {
      const number = Number(match[4]);
      if (number >= 1 && number <= sourceCount) {
        const link = element("a", "citation", match[0]);
        link.href = `#${prefix}-source-${number}`;
        link.setAttribute("aria-label", `Conferir fonte ${number}`);
        link.addEventListener("click", () => {
          const source = $(`${prefix}-source-${number}`);
          if (source) {
            source.open = true;
            let parent = source.parentElement;
            while (parent) {
              if (parent.tagName === "DETAILS") parent.open = true;
              parent = parent.parentElement;
            }
          }
        });
        target.append(link);
      } else target.append(document.createTextNode(match[0]));
    }
    end = match.index + match[0].length;
  }
  target.append(document.createTextNode(text.slice(end)));
}

function tableCells(line) {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|") && !text.endsWith("\\|")) text = text.slice(0, -1);
  const cells = [];
  let cell = "";
  for (let i = 0; i < text.length; i++) {
    if (text[i] === "\\" && text[i + 1] === "|") { cell += "|"; i++; }
    else if (text[i] === "|") { cells.push(cell.trim()); cell = ""; }
    else cell += text[i];
  }
  cells.push(cell.trim());
  return cells;
}

function tableAlignment(line, columnCount) {
  if (!line) return null;
  const cells = tableCells(line);
  if (cells.length !== columnCount || !cells.every((cell) => /^:?-{3,}:?$/.test(cell))) return null;
  return cells.map((cell) => cell.startsWith(":") && cell.endsWith(":") ? "center" : cell.endsWith(":") ? "right" : "left");
}

function tableRow(cells, alignment, header, prefix, sourceCount) {
  const row = element("tr");
  // Uma linha irregular não descarta conteúdo: células excedentes são
  // preservadas, em texto, na última coluna.
  const values = cells.length > alignment.length
    ? [...cells.slice(0, alignment.length - 1), cells.slice(alignment.length - 1).join(" | ")]
    : cells;
  alignment.forEach((align, index) => {
    const cell = element(header ? "th" : "td", `table-align-${align}`);
    if (header) cell.scope = "col";
    inlineText(cell, values[index] || "", prefix, sourceCount);
    row.append(cell);
  });
  return row;
}

function renderText(target, text, prefix, sourceCount) {
  target.replaceChildren();
  let paragraph = [], list = null;
  const flush = () => {
    if (paragraph.length) {
      const p = element("p");
      inlineText(p, paragraph.join("\n"), prefix, sourceCount);
      target.append(p);
      paragraph = [];
    }
  };
  const lines = String(text || "").replaceAll("\r\n", "\n").split("\n");
  for (let index = 0; index < lines.length; index++) {
    const line = lines[index];
    const heading = /^#{1,4}\s+(.+)/.exec(line);
    const item = /^\s*(?:[-*]\s+|(\d+)\.\s+)(.+)/.exec(line);
    const headers = line.includes("|") ? tableCells(line) : [];
    const alignment = headers.length ? tableAlignment(lines[index + 1], headers.length) : null;
    if (!line.trim()) { flush(); list = null; continue; }
    if (alignment) {
      flush(); list = null;
      const wrapper = element("div", "answer-table-scroll");
      wrapper.tabIndex = 0;
      wrapper.setAttribute("role", "region");
      wrapper.setAttribute("aria-label", "Tabela da resposta; use as setas para rolar quando necessário");
      const table = element("table", "answer-table"), head = element("thead"), body = element("tbody");
      head.append(tableRow(headers, alignment, true, prefix, sourceCount));
      index++; // Separador Markdown do cabeçalho.
      while (index + 1 < lines.length && lines[index + 1].trim() && lines[index + 1].includes("|")) {
        index++;
        body.append(tableRow(tableCells(lines[index]), alignment, false, prefix, sourceCount));
      }
      table.append(head, body); wrapper.append(table); target.append(wrapper);
    } else if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      flush(); list = null; target.append(element("hr"));
    } else if (heading) {
      flush(); list = null;
      const h = element("h3"); inlineText(h, heading[1], prefix, sourceCount); target.append(h);
    } else if (item) {
      flush();
      const tag = item[1] ? "OL" : "UL";
      if (!list || list.tagName !== tag) { list = element(tag.toLowerCase()); target.append(list); }
      const li = element("li"); inlineText(li, item[2], prefix, sourceCount); list.append(li);
    } else { list = null; paragraph.push(line); }
  }
  flush();
}

function renderSources(prefix, sources, options = {}) {
  const container = options.container || $(`${prefix}-sources`);
  container.replaceChildren();
  container.classList.toggle("hidden", !sources.length);
  if (!sources.length) return;
  const heading = element("div", "section-heading");
  heading.append(element("h2", "", prefix === "mission" ? "Material consultado no livro" : "Fontes para conferir"));
  heading.append(element("span", "", `${sources.length} ${sources.length === 1 ? "fonte disponível" : "fontes disponíveis"}`));
  const grid = element("div", "source-grid");
  sources.forEach((source, index) => {
    const card = element("details", "source");
    card.id = `${prefix}-source-${index + 1}`;
    card.open = !options.collapsed && index === 0;
    const summary = element("summary"), description = element("span");
    description.append(element("span", "source-title", source.section || source.chapter || source.title || "Trecho do livro"));
    description.append(element("span", "source-meta", `PDF p. ${source.pdf_page}${source.printed_page ? ` · Impressa p. ${source.printed_page}` : ""} · ${source.title || "Livro"} ${source.edition || ""}`));
    const sourceKind = source.source_kind === "page"
      ? source.truncated === false ? "Texto completo da página" : source.truncated === true ? "Página · texto parcial" : "Texto da página"
      : "Trecho do livro";
    description.append(element("span", "source-meta", sourceKind));
    summary.append(element("span", "source-number", index + 1), description);
    const content = element("div", "source-content");
    if (source.truncated === true) {
      const measured = Number.isInteger(source.returned_chars) && Number.isInteger(source.original_chars)
        && source.returned_chars >= 0 && source.original_chars >= source.returned_chars;
      const counts = measured ? ` Foram disponibilizados ${source.returned_chars.toLocaleString("pt-BR")} de ${source.original_chars.toLocaleString("pt-BR")} caracteres extraídos.` : "";
      content.append(element("p", "notice neutral", `Este conteúdo foi limitado para caber na consulta.${counts} Abra a página original para conferir o restante.`));
    }
    const excerpt = element("pre", "", source.text);
    excerpt.tabIndex = 0;
    excerpt.setAttribute("role", "region");
    excerpt.setAttribute("aria-label", `Texto da fonte ${index + 1}; use as setas para rolar`);
    content.append(excerpt);
    const footer = element("div", "source-footer");
    const validHash = typeof options.bookHash === "string" && /^[a-f0-9]{64}$/i.test(options.bookHash);
    const allowImage = options.imagesAvailable !== false && (!options.historical || validHash);
    if (Number.isSafeInteger(source.pdf_page) && source.pdf_page > 0 && allowImage) {
      const link = element("a", "button secondary small", "Abrir página original ↗");
      // A página permanece estável quando a extração muda a divisão dos chunks.
      link.href = `/api/book/pages/${source.pdf_page}/image${validHash ? `?book_sha256=${encodeURIComponent(options.bookHash)}` : ""}`;
      link.target = "_blank"; link.rel = "noopener noreferrer";
      footer.append(link);
    } else if (options.historical && !allowImage) {
      footer.append(element("span", "", "Texto preservado no histórico. A imagem deste livro não está disponível nesta conversa."));
    }
    footer.append(element("span", "", (source.methods || []).join(" + ")));
    content.append(footer); card.append(summary, content); grid.append(card);
  });
  container.append(heading, grid);
}

function renderRuleChecks(checks, prefix, sourceCount) {
  const entries = Array.isArray(checks) ? checks.filter((check) => check?.validation && check?.arguments) : [];
  if (!entries.length) return null;
  const disclosure = element("details", "rule-checks");
  disclosure.id = `${prefix}-rule-checks`;
  const needsCorrection = entries.some((check) => check.validation.valid === false);
  disclosure.open = needsCorrection;
  disclosure.append(element("summary", "", `Conferência parcial em Python${needsCorrection ? " · ajustes necessários" : ""}`));
  disclosure.append(element("p", "muted", "Confira os dados que o assistente enviou ao validador. Se algum valor estiver diferente da sua ficha, peça a correção. Esta conferência cobre apenas as regras implementadas; não aprova uma ficha completa."));
  const attributeLabels = { agilidade: "Agilidade", forca: "Força", intelecto: "Intelecto", presenca: "Presença", vigor: "Vigor" };
  entries.forEach((check, index) => {
    const { arguments: parameters, validation } = check;
    const section = element("section", "rule-check");
    section.append(element("h3", "", `Dados conferidos${entries.length > 1 ? ` (${index + 1})` : ""}`));
    const inputs = element("dl", "rule-check-values");
    const values = [
      ["Classe", classNames[parameters.character_class] || parameters.character_class || "Não informada"],
      ["NEX", parameters.nex == null ? "Não informado" : `${parameters.nex}%`],
      ...attributes.map((name) => [attributeLabels[name], parameters.attributes?.[name] ?? "Não informado"]),
    ];
    values.forEach(([label, value]) => {
      const pair = element("div"); pair.append(element("dt", "", label), element("dd", "", value)); inputs.append(pair);
    });
    section.append(inputs, element("p", "", validation.valid === true
      ? "Esses parâmetros passaram nas regras suportadas."
      : validation.valid === false ? "Esses parâmetros precisam de correção:" : "A conferência não foi concluída."));
    if (validation.errors?.length) section.append(element("p", "notice error", detailText(validation.errors)));
    const derived = Object.entries(validation.derived || {}).filter(([, value]) => typeof value === "number" || typeof value === "string");
    if (derived.length) {
      section.append(element("h3", "", "Valores base (sem origem, equipamentos ou poderes)"));
      const results = element("dl", "rule-check-values");
      derived.forEach(([key, value]) => {
        const pair = element("div"); pair.append(element("dt", "", derivedLabels[key] || key.replaceAll("_", " ")), element("dd", "", value)); results.append(pair);
      });
      section.append(results);
    }
    if (Array.isArray(validation.warnings) && validation.warnings.length) {
      const warnings = element("ul", "muted");
      validation.warnings.forEach((warning) => warnings.append(element("li", "", warning)));
      section.append(warnings);
    }
    const scope = typeof validation.scope === "string" ? validation.scope : validation.scope?.description;
    if (scope) section.append(element("p", "muted", scope));
    const citations = Array.isArray(check.citation_ids) ? check.citation_ids.filter((id) => Number.isSafeInteger(id) && id >= 1 && id <= sourceCount) : [];
    if (citations.length) {
      const references = element("p");
      inlineText(references, `Referências da conferência: ${[...new Set(citations)].map((id) => `[${id}]`).join(" ")}`, prefix, sourceCount);
      section.append(references);
    }
    disclosure.append(section);
  });
  return disclosure;
}

function renderResult(prefix, data, searchOnly = false, options = {}) {
  const container = options.container || $(`${prefix}-result`);
  const mission = options.mode === "mission" || prefix === "mission";
  const sources = Array.isArray(data.sources) ? data.sources : [];
  const card = element("article", "card answer-card"), header = element("div", "answer-header");
  const successful = data.status === "answered" || searchOnly;
  header.append(element("h2", "", searchOnly ? "Trechos encontrados" : mission ? "Proposta do assistente" : "Resposta do assistente"));
  if (successful) header.append(element("span", `label-pill${mission ? " creative" : ""}`, searchOnly ? "BUSCA LOCAL" : mission ? "SUGESTÃO CRIATIVA" : sources.length ? "CONSULTA COM FONTES" : "CONVERSA"));
  if (data.status === "needs_review") header.append(element("span", "label-pill creative", "REVISÃO NECESSÁRIA"));
  const text = element("div", "answer-text");
  const message = data.answer || (sources.length ? `Encontrei ${sources.length} fontes para você consultar. Abra as fontes abaixo e confira a página original.` : "Não encontrei trechos para esta pergunta. Tente descrever o assunto com outro termo.");
  renderText(text, message, prefix, sources.length);
  card.append(header);
  if (mission) card.append(element("p", "notice neutral mission-review",
    "Esta narrativa é uma proposta criativa e precisa da revisão do mestre. A presença de citações não garante fidelidade às regras: confira custos, condições e efeitos nas páginas originais."));
  card.append(text);
  const ruleChecks = renderRuleChecks(data.rule_checks, prefix, sources.length);
  if (ruleChecks) card.append(ruleChecks);
  const warnings = [...new Set((data.warnings || []).concat(sources.flatMap((s) => s.retrieval_warnings || [])))];
  if (warnings.length) {
    const warningContainer = element("div", "result-warnings");
    warnings.forEach((warning) => warningContainer.append(element("div", "notice", warning)));
    card.append(warningContainer);
  }
  if (data.usage || data.retrieval || data.research) {
    const usage = data.usage, retrieval = data.retrieval, research = data.research, meta = element("div", "result-usage");
    const seconds = (ms) => `${new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 1 }).format(ms / 1000)} s`;
    if (retrieval?.strategy === "structured_catalog") {
      meta.append(element("span", "", "Comparação por VD impresso"));
      const coverage = data.structured_catalog?.coverage;
      if (Number.isInteger(coverage?.parsed_statblocks) && Number.isInteger(coverage?.scanned_pages)) {
        meta.append(element("span", "", `Catálogo: ${coverage.parsed_statblocks} fichas extraídas · ${coverage.scanned_pages} páginas examinadas`));
      }
    }
    if (Number.isFinite(retrieval?.elapsed_ms)) meta.append(element("span", "", `Recuperação: ${seconds(retrieval.elapsed_ms)}`));
    if (retrieval?.rerank_status === "applied") meta.append(element("span", "", "Fontes reordenadas localmente"));
    const modelCalls = research?.model_calls ?? usage?.call_count;
    if (Number.isInteger(modelCalls) && modelCalls >= 0) meta.append(element("span", "", `Chamadas ao modelo: ${modelCalls}`));
    if (Number.isInteger(research?.tool_calls) && research.tool_calls >= 0) meta.append(element("span", "", `Operações de ferramentas locais: ${research.tool_calls}`));
    if (usage) {
      meta.append(element("span", "", usage.model || "Groq"));
      if (Number.isFinite(usage.elapsed_ms)) meta.append(element("span", "", `Geração: ${seconds(usage.elapsed_ms)}`));
      if (usage.rate_wait_ms > 0) meta.append(element("span", "", `Espera pela cota da API: ${seconds(usage.rate_wait_ms)}`));
      if (Number.isFinite(usage.total_ms)) meta.append(element("span", "", `Total no servidor: ${seconds(usage.total_ms)}`));
      meta.append(element("span", "", `Tokens: ${usage.input_tokens ?? "—"} entrada · ${usage.output_tokens ?? "—"} saída`));
      if (usage.estimated_usd != null) meta.append(element("span", "", `Estimativa: US$ ${Number(usage.estimated_usd).toFixed(6)}`));
    }
    card.append(meta);
  }
  container.replaceChildren(card);
  renderSources(prefix, sources, {
    container: options.sourcesContainer, collapsed: options.collapsedSources,
    historical: options.historical, imagesAvailable: options.imagesAvailable, bookHash: data.book_sha256,
  });
}

async function generate(prefix, searchOnly = false) {
  if (state.generating || state.chatSending) return;
  let body;
  if (prefix === "consult") {
    const question = $("question").value.trim();
    if (question.length < 3) {
      $("chat-status").textContent = "Para buscar trechos, escreva pelo menos 3 caracteres. Mensagens de conversa podem ser mais curtas.";
      $("question").focus(); return;
    }
    body = { question, mode: "consult" };
    $("local-search-panel").classList.remove("hidden"); $("local-search-panel").open = true;
  } else {
    if (!$("mission-form").reportValidity()) return;
    const brief = $("mission-brief").value.trim();
    if (brief.length < 3) { $("mission-brief").focus(); return; }
    const question = `${brief}\nGrupo: ${$("mission-team").value.trim() || "não informado"}.\nTom: ${$("mission-tone").value}.\nDuração: ${$("mission-duration").value}.`;
    body = { question, mode: "mission", campaign_id: $("mission-campaign").value ? Number($("mission-campaign").value) : null };
  }
  state.generating = true; syncGenerationButtons();
  const container = $(`${prefix}-result`);
  container.setAttribute("aria-busy", "true");
  const loading = element("div", "card loading-card"), description = element("div");
  description.append(element("h2", "", searchOnly ? "Buscando no livro…" : prefix === "mission" ? "Preparando a investigação…" : "Consultando as fontes…"));
  description.append(element("p", "", searchOnly
    ? "A primeira busca pode levar mais tempo para carregar os modelos locais."
    : prefix === "consult"
      ? "O assistente pode buscar outros trechos, ler páginas e aguardar a cota da API entre etapas. A consulta pode levar cerca de dois minutos."
      : "Preparando uma proposta com o material recuperado e o contexto selecionado. O carregamento dos modelos locais pode levar mais tempo na primeira consulta."));
  loading.append(element("span", "spinner"), description);
  container.replaceChildren(loading); renderSources(prefix, []);
  try {
    const data = await jsonRequest(searchOnly ? "/api/search" : "/api/ask", "POST", searchOnly ? { question: body.question } : body);
    renderResult(prefix, data, searchOnly);
  } catch (error) { showNotice(container, error.message, "error"); }
  finally {
    state.generating = false; container.removeAttribute("aria-busy"); syncGenerationButtons();
    await loadStatus();
  }
}

const chatStorage = { active: "arquivo-paranormal.active-conversation", pending: "arquivo-paranormal.pending-messages" };

function currentPending() {
  return state.conversation ? state.pendingMessages.get(state.conversation.id) || null : null;
}

function persistActiveConversation(id) {
  try {
    if (id) localStorage.setItem(chatStorage.active, String(id));
    else localStorage.removeItem(chatStorage.active);
  } catch { /* O histórico permanece no servidor quando o navegador bloqueia storage. */ }
}

function persistPendingMessages() {
  try { sessionStorage.setItem(chatStorage.pending, JSON.stringify([...state.pendingMessages.values()])); }
  catch { /* A mesma sessão ainda pode retomar pelo identificador mantido em memória. */ }
}

function restorePendingMessages() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(chatStorage.pending) || "[]");
    if (!Array.isArray(saved)) return;
    for (const pending of saved.slice(0, 20)) {
      if (Number.isSafeInteger(pending?.conversationId) && pending.conversationId > 0
          && typeof pending.requestId === "string" && /^[a-f0-9-]{36}$/i.test(pending.requestId)
          && typeof pending.content === "string" && pending.content.trim() && pending.content.length <= 2000) {
        state.pendingMessages.set(pending.conversationId, pending);
      }
    }
  } catch { /* Conteúdo inválido de storage não vira instrução nem mensagem. */ }
}

function newRequestId() {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64; bytes[8] = (bytes[8] & 63) | 128;
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function updateQuestionCount() {
  $("question-count").textContent = `${$("question").value.length} / 2000`;
}

function saveChatDraft() {
  state.drafts.set(state.conversation?.id || "new", $("question").value);
}

function conversationModeName(mode) {
  return mode === "mission" ? "Criação de missão" : "Consulta ao livro";
}

function renderConversationList() {
  const list = $("conversation-list");
  list.replaceChildren();
  $("conversation-total").textContent = state.conversations.length;
  if (!state.conversations.length) {
    list.append(element("p", "saved-empty", "Suas conversas aparecerão aqui depois da primeira mensagem."));
    return;
  }
  for (const conversation of state.conversations) {
    const button = element("button", `saved-item conversation-item${state.conversation?.id === conversation.id ? " active" : ""}`);
    button.type = "button";
    if (state.conversation?.id === conversation.id) button.setAttribute("aria-current", "true");
    button.append(element("strong", "", conversation.title || "Nova conversa"));
    button.append(element("span", "", `${conversationModeName(conversation.mode)} · ${formatDate(conversation.updated_at || conversation.created_at)}`));
    if (state.pendingMessages.has(conversation.id)) button.append(element("span", "pending-label", "Envio a conferir"));
    button.addEventListener("click", () => selectConversation(conversation.id));
    list.append(button);
  }
  syncGenerationButtons();
}

async function loadConversations() {
  const epoch = ++state.conversationListEpoch;
  try {
    const conversations = await request("/api/conversations");
    if (epoch !== state.conversationListEpoch) return;
    if (!Array.isArray(conversations)) throw new Error("A lista de conversas retornou um formato inesperado.");
    state.conversations = conversations.filter((item) => Number.isSafeInteger(item?.id) && item.id > 0);
    renderConversationList();
  } catch (error) {
    if (epoch !== state.conversationListEpoch) return;
    const message = element("p", "small muted", error.message);
    const retry = element("button", "button secondary small", "Atualizar conversas");
    retry.type = "button"; retry.addEventListener("click", loadConversations);
    $("conversation-list").replaceChildren(message, retry);
  }
}

function reconcileConversation(conversation) {
  conversation.messages = Array.isArray(conversation.messages) ? conversation.messages : [];
  let localPending = state.pendingMessages.get(conversation.id);
  if (localPending && conversation.messages.some((message) => message.role === "assistant" && message.request_id === localPending.requestId)) {
    state.pendingMessages.delete(conversation.id);
    localPending = null;
  }
  // Se outra aba enviou uma mensagem, conserve o envio local ainda não aceito.
  // Repetir seu UUID após a outra resposta evita perder ou duplicar o texto.
  if (conversation.pending_request_id && (!localPending || localPending.requestId === conversation.pending_request_id)) {
    const userMessage = conversation.messages.find((message) => message.role === "user" && message.request_id === conversation.pending_request_id);
    if (userMessage) {
      state.pendingMessages.set(conversation.id, {
        conversationId: conversation.id, requestId: conversation.pending_request_id,
        content: userMessage.content, createdAt: userMessage.created_at,
        error: conversation.pending_expired
          ? "Este envio foi interrompido. Use “Retomar envio” para confirmar seu estado; a mesma solicitação não será gerada novamente."
          : "Esta mensagem ainda está em processamento ou aguarda confirmação. Confira o histórico antes de continuar.",
      });
    }
  }
  persistPendingMessages();
}

function updateConversationControls() {
  const conversation = state.conversation;
  $("conversation-title").textContent = conversation?.title || "Nova conversa";
  $("delete-conversation").classList.toggle("hidden", !conversation);
  $("chat-options").classList.toggle("hidden", Boolean(conversation));
  $("chat-options-help").classList.toggle("hidden", Boolean(conversation));
  $("chat-mode").value = conversation?.mode || $("chat-mode").value || "consult";
  if (conversation) {
    const campaign = state.campaigns.find((item) => item.id === conversation.campaign_id);
    if (conversation.campaign_id && ![...$("chat-campaign").options].some((option) => Number(option.value) === conversation.campaign_id)) {
      $("chat-campaign").append(new Option(campaign?.name || `Campanha ${conversation.campaign_id}`, String(conversation.campaign_id)));
    }
    $("chat-campaign").value = conversation.campaign_id ? String(conversation.campaign_id) : "";
    $("conversation-subtitle").textContent = [
      conversationModeName(conversation.mode), campaign?.name || (conversation.campaign_id ? `Campanha ${conversation.campaign_id}` : "Sem campanha"),
      "Histórico salvo localmente",
    ].join(" · ");
  } else {
    $("conversation-subtitle").textContent = "Escolha o modo e, se quiser, uma campanha antes da primeira mensagem.";
  }
  $("chat-suggestions").classList.toggle("hidden", Boolean(conversation?.messages?.length));
  $("question").placeholder = $("chat-mode").value === "mission"
    ? "Conte a ideia da missão ou peça ajustes à proposta anterior…"
    : "Pergunte sobre o livro ou continue: “e nesse caso?”, “compare com a outra opção”…";
  syncGenerationButtons();
}

function appendChatMessage(message, conversation, index) {
  const article = element("article", `chat-message ${message.role === "user" ? "chat-user" : "chat-assistant"}`);
  const safeId = Number.isSafeInteger(message.id) && message.id > 0 ? message.id : `local-${index}`;
  const prefix = `chat-${conversation.id}-message-${safeId}`;
  article.id = prefix;
  if (message.request_id) article.dataset.requestId = message.request_id;
  if (message.role === "user") {
    const header = element("div", "chat-message-heading");
    header.append(element("strong", "", "Você"), element("span", "", formatDate(message.created_at)));
    article.append(header, element("p", "chat-user-text", message.content || ""));
  } else {
    const result = message.result && typeof message.result === "object" ? message.result : {};
    const data = { ...result, answer: message.content || result.answer || "", sources: Array.isArray(result.sources) ? result.sources : [] };
    const content = element("div"), sources = element("section", "sources-section");
    sources.id = `${prefix}-sources`;
    const disclosure = element("details", "chat-source-disclosure");
    disclosure.id = `${prefix}-source-disclosure`;
    disclosure.append(element("summary", "", `Conferir fontes desta resposta (${data.sources.length})`), sources);
    renderResult(prefix, data, false, {
      container: content, sourcesContainer: sources, collapsedSources: true,
      mode: conversation.mode, historical: true, imagesAvailable: conversation.book_matches !== false,
    });
    article.append(content);
    if (data.sources.length) article.append(disclosure);
    if (message.created_at) article.append(element("p", "chat-message-time", formatDate(message.created_at)));
  }
  $("chat-messages").append(article);
  return article;
}

function renderChat({ scrollToEnd = false } = {}) {
  const feed = $("chat-messages"), previousScroll = feed.scrollTop;
  const opened = [...feed.querySelectorAll("details[open]")].map((node) => node.id).filter(Boolean);
  feed.replaceChildren();
  const conversation = state.conversation;
  const messages = conversation?.messages || [];
  for (const [index, message] of messages.entries()) {
    if (message.role === "user" || message.role === "assistant") appendChatMessage(message, conversation, index);
  }
  const pending = currentPending();
  if (pending && !messages.some((message) => message.role === "user" && message.request_id === pending.requestId)) {
    appendChatMessage({ role: "user", content: pending.content, created_at: pending.createdAt, request_id: pending.requestId }, conversation, "pending");
  }
  if (!messages.length && !pending) feed.append(chatEmptyTemplate.cloneNode(true));
  for (const id of opened) { const detail = $(id); if (detail) detail.open = true; }
  feed.scrollTop = scrollToEnd ? feed.scrollHeight : previousScroll;
  $("chat-pending-actions").classList.toggle("hidden", !pending || state.chatSending);
  $("chat-status").classList.toggle("working", state.chatSending);
  if (state.chatSending) {
    $("chat-status").replaceChildren(element("span", "spinner"), element("span", "", "Preparando a resposta. O assistente pode consultar trechos, ler páginas e aguardar a cota da API. Isso pode levar cerca de dois minutos."));
  } else if (pending) {
    $("chat-status").textContent = pending.error || "O envio ainda não foi confirmado. Retome a mesma mensagem ou confira o histórico salvo.";
  } else {
    $("chat-status").textContent = messages.length && messages[messages.length - 1].role === "assistant"
      ? "Resposta disponível no histórico. Você pode continuar a conversa." : "";
  }
  if (conversation?.book_matches === false) {
    showNotice($("conversation-feedback"), "O livro ativo mudou desde esta conversa. O histórico e os textos das fontes continuam disponíveis. Comece uma nova conversa para consultar o livro atual.", "neutral");
  }
  updateConversationControls();
  renderConversationList();
}

async function selectConversation(id, { focus = false } = {}) {
  if (state.chatSending || !Number.isSafeInteger(id) || id < 1) return;
  saveChatDraft();
  const epoch = ++state.conversationEpoch;
  state.conversationLoading = true;
  $("delete-conversation-confirm").classList.add("hidden");
  $("conversation-feedback").classList.add("hidden");
  $("chat-status").textContent = "Abrindo conversa salva…";
  syncGenerationButtons();
  try {
    const conversation = await request(`/api/conversations/${id}`);
    if (epoch !== state.conversationEpoch) return;
    if (conversation?.id !== id) throw new Error("A conversa retornou um formato inesperado.");
    reconcileConversation(conversation);
    state.conversation = conversation; persistActiveConversation(id);
    $("question").value = state.drafts.get(id) || ""; updateQuestionCount();
    renderChat({ scrollToEnd: true });
    if (focus) $("question").focus({ preventScroll: true });
  } catch (error) {
    if (epoch !== state.conversationEpoch) return;
    showNotice($("conversation-feedback"), error.message, "error");
    $("chat-status").textContent = "";
    if (error.status === 404) {
      state.conversations = state.conversations.filter((item) => item.id !== id);
      state.pendingMessages.delete(id); persistPendingMessages();
      if (!state.conversation) persistActiveConversation(null);
    }
  } finally {
    if (epoch === state.conversationEpoch) {
      state.conversationLoading = false; updateConversationControls(); renderConversationList();
    }
  }
}

function newConversation() {
  if (state.chatSending) return;
  saveChatDraft(); state.conversationEpoch++;
  state.conversationLoading = false; state.conversation = null; persistActiveConversation(null);
  $("question").value = state.drafts.get("new") || ""; updateQuestionCount();
  $("chat-mode").value = "consult"; $("chat-campaign").value = "";
  $("conversation-feedback").classList.add("hidden");
  $("delete-conversation-confirm").classList.add("hidden");
  renderChat(); $("question").focus({ preventScroll: true });
}

async function deleteConversation() {
  const id = state.conversation?.id;
  if (!id || state.chatSending || currentPending()) return;
  state.conversationLoading = true; syncGenerationButtons();
  $("confirm-delete-conversation").disabled = true;
  try {
    await jsonRequest(`/api/conversations/${id}`, "DELETE", {});
    state.conversationListEpoch++;
    state.conversations = state.conversations.filter((item) => item.id !== id);
    state.pendingMessages.delete(id); persistPendingMessages();
    newConversation(); state.drafts.delete(id);
    $("chat-status").textContent = "Conversa excluída. Você pode começar outra quando quiser.";
  } catch (error) { showNotice($("conversation-feedback"), error.message, "error"); }
  finally {
    state.conversationLoading = false; $("confirm-delete-conversation").disabled = false;
    $("delete-conversation-confirm").classList.add("hidden"); syncGenerationButtons();
  }
}

function mergeChatMessages(conversation, pair, requestId) {
  if (pair?.user_message?.role !== "user" || pair?.assistant_message?.role !== "assistant"
      || pair.user_message.request_id !== requestId || pair.assistant_message.request_id !== requestId
      || !Number.isSafeInteger(pair.user_message.id) || !Number.isSafeInteger(pair.assistant_message.id)) {
    throw new Error("A resposta não trouxe o par de mensagens esperado. Confira o histórico salvo.");
  }
  for (const incoming of [pair.user_message, pair.assistant_message]) {
    const index = conversation.messages.findIndex((message) =>
      message.id === incoming.id || (message.request_id === incoming.request_id && message.role === incoming.role));
    if (index >= 0) conversation.messages[index] = incoming;
    else conversation.messages.push(incoming);
  }
  conversation.pending_request_id = null;
}

async function sendPendingMessage(pending) {
  const conversation = state.conversation;
  if (!conversation || conversation.id !== pending.conversationId) return;
  const feed = $("chat-messages");
  const followEnd = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 90;
  state.chatSending = true;
  $("conversation-feedback").classList.add("hidden");
  renderChat({ scrollToEnd: true });
  try {
    const pair = await jsonRequest(`/api/conversations/${conversation.id}/messages`, "POST", { content: pending.content, request_id: pending.requestId });
    mergeChatMessages(conversation, pair, pending.requestId);
    state.pendingMessages.delete(conversation.id); persistPendingMessages();
    state.drafts.delete(conversation.id);
  } catch (error) {
    const code = error.data?.detail?.code;
    if (error.status === 422 || error.status === 404 || code === "book_changed" || code === "request_id_conflict") {
      state.pendingMessages.delete(conversation.id); persistPendingMessages();
      $("question").value = pending.content; updateQuestionCount();
      if (code === "book_changed") conversation.book_matches = false;
      showNotice($("conversation-feedback"), error.message, "error");
    } else {
      pending.error = code === "request_in_progress"
        ? "O servidor ainda está processando esta mensagem. Aguarde e confira o histórico salvo; retomar mantém o mesmo envio."
        : error.message;
      state.pendingMessages.set(conversation.id, pending); persistPendingMessages();
    }
  } finally {
    state.chatSending = false;
    renderChat({ scrollToEnd: followEnd });
    if (!currentPending() && conversation.book_matches !== false && state.view === "consulta") $("question").focus({ preventScroll: true });
    void loadStatus(); void loadConversations();
  }
}

async function submitChat(event) {
  event?.preventDefault();
  if (state.chatSending || state.generating || state.conversationLoading || currentPending() || state.conversation?.book_matches === false) return;
  const content = $("question").value.trim();
  if (!content || !$("consult-form").reportValidity()) { $("question").focus(); return; }
  state.chatSending = true; syncGenerationButtons();
  $("conversation-feedback").classList.add("hidden");
  $("chat-status").textContent = state.conversation ? "Enviando mensagem…" : "Salvando a nova conversa…";
  try {
    if (!state.conversation) {
      const conversation = await jsonRequest("/api/conversations", "POST", {
        title: content.slice(0, 100), mode: $("chat-mode").value,
        campaign_id: $("chat-campaign").value ? Number($("chat-campaign").value) : null,
      });
      if (!Number.isSafeInteger(conversation?.id) || conversation.id < 1) throw new Error("Não foi possível identificar a conversa criada.");
      state.conversation = { ...conversation, messages: conversation.messages || [] };
      state.conversationListEpoch++;
      state.conversations.unshift(conversation); persistActiveConversation(conversation.id);
      state.drafts.delete("new");
    }
    const pending = { conversationId: state.conversation.id, requestId: newRequestId(), content, createdAt: new Date().toISOString() };
    state.pendingMessages.set(pending.conversationId, pending); persistPendingMessages();
    $("question").value = ""; updateQuestionCount();
    await sendPendingMessage(pending);
  } catch (error) {
    state.chatSending = false; syncGenerationButtons();
    $("chat-status").textContent = "";
    showNotice($("conversation-feedback"), error.message, "error");
  }
}

async function restoreChat() {
  restorePendingMessages();
  const epoch = state.conversationEpoch;
  await loadConversations();
  if (epoch !== state.conversationEpoch || state.chatSending || state.conversation) return;
  let id = null;
  try { id = Number(localStorage.getItem(chatStorage.active)); } catch { /* Seleção opcional. */ }
  if (!Number.isSafeInteger(id) || id < 1) id = [...state.pendingMessages.keys()][0];
  if (id) await selectConversation(id);
  else updateConversationControls();
}

function formatDate(value) {
  if (!value) return "";
  const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(date);
}

function renderSavedList(id, items, activeId, kind, onSelect) {
  const list = $(id); list.replaceChildren();
  if (!items.length) {
    list.append(element("p", "saved-empty", kind === "character" ? "Seus personagens aparecerão aqui depois de salvos." : "Crie uma campanha para guardar o contexto da sua mesa."));
    return;
  }
  items.forEach((item) => {
    const button = element("button", `saved-item${activeId === item.id ? " active" : ""}`);
    button.type = "button";
    button.disabled = state[`${kind}Busy`];
    if (activeId === item.id) button.setAttribute("aria-current", "true");
    button.append(element("strong", "", item.name || "Sem nome"));
    button.append(element("span", "", kind === "character" ? `${classNames[item.character_class] || item.character_class} · NEX ${item.nex || 5}%` : formatDate(item.updated_at || item.created_at)));
    button.addEventListener("click", () => onSelect(item.id));
    list.append(button);
  });
}

function setEditorBusy(kind, busy) {
  // A validação pode precisar de duas chamadas. O destino e os campos não
  // podem mudar entre elas, nem durante o carregamento de outro registro.
  state[`${kind}Busy`] = busy;
  const forms = [$("campaign-form"), $("event-form")];
  forms.forEach((form) => {
    form.setAttribute("aria-busy", String(busy));
    form.querySelectorAll("input, select, textarea, button").forEach((control) => { control.disabled = busy; });
  });
  $(`new-${kind}`).disabled = busy;
  $(`${kind}-list`).querySelectorAll("button").forEach((button) => { button.disabled = busy; });
}

async function loadCampaigns() {
  try {
    state.campaigns = await request("/api/campaigns");
    $("campaign-total").textContent = state.campaigns.length;
    renderSavedList("campaign-list", state.campaigns, state.campaignId, "campaign", selectCampaign);
    const selected = $("mission-campaign").value;
    $("mission-campaign").replaceChildren(new Option("Missão independente", ""));
    state.campaigns.forEach((campaign) => $("mission-campaign").append(new Option(campaign.name, String(campaign.id))));
    if (state.campaigns.some((c) => String(c.id) === selected)) $("mission-campaign").value = selected;
    const selectedChat = $("chat-campaign").value;
    $("chat-campaign").replaceChildren(new Option("Sem campanha", ""));
    state.campaigns.forEach((campaign) => $("chat-campaign").append(new Option(campaign.name, String(campaign.id))));
    if (state.campaigns.some((c) => String(c.id) === selectedChat)) $("chat-campaign").value = selectedChat;
    updateConversationControls();
  } catch (error) { $("campaign-list").replaceChildren(element("p", "small muted", error.message)); }
}

function renderEvents(events) {
  const list = $("campaign-events"); list.replaceChildren();
  if (!events.length) { list.append(element("p", "muted small", "Nenhum acontecimento registrado nesta campanha.")); return; }
  [...events].reverse().forEach((event) => {
    const item = element("article", "timeline-entry"), time = element("time", "", formatDate(event.created_at));
    if (event.created_at) time.dateTime = event.created_at;
    item.append(time, element("p", "", event.text)); list.append(item);
  });
}

function fillCampaign(campaign, { keepEventDraft = false } = {}) {
  state.campaignId = campaign.id;
  $("campaign-form-title").textContent = "Editar campanha";
  $("campaign-name").value = campaign.name || "";
  $("campaign-summary").value = campaign.summary || "";
  $("campaign-house-rules").value = campaign.house_rules || "";
  $("campaign-events-panel").classList.remove("hidden");
  if (!keepEventDraft) $("event-text").value = "";
  feedback("campaign-feedback", ""); feedback("event-feedback", "");
  renderEvents(campaign.events || []);
  renderSavedList("campaign-list", state.campaigns, state.campaignId, "campaign", selectCampaign);
}

async function selectCampaign(id) {
  if (state.campaignBusy) return;
  setEditorBusy("campaign", true);
  feedback("campaign-feedback", "Abrindo campanha…");
  try {
    fillCampaign(await request(`/api/campaigns/${id}`));
  } catch (error) { feedback("campaign-feedback", error.message, true); }
  finally { setEditorBusy("campaign", false); $("campaign-name").focus({ preventScroll: true }); }
}

function newCampaign() {
  if (state.campaignBusy) return;
  state.campaignId = null; $("campaign-form").reset();
  $("campaign-form-title").textContent = "Nova campanha";
  $("campaign-events-panel").classList.add("hidden"); feedback("campaign-feedback", "");
  renderSavedList("campaign-list", state.campaigns, null, "campaign", selectCampaign);
  $("campaign-name").focus();
}

async function saveCampaign(event) {
  event.preventDefault();
  if (state.campaignBusy || !$("campaign-form").reportValidity()) return;
  const body = { name: $("campaign-name").value.trim(), summary: $("campaign-summary").value.trim(), house_rules: $("campaign-house-rules").value.trim() };
  if (!body.name) { feedback("campaign-feedback", "Informe um nome para a campanha.", true); return; }
  const id = state.campaignId;
  setEditorBusy("campaign", true); feedback("campaign-feedback", "Salvando…");
  try {
    const campaign = await jsonRequest(id ? `/api/campaigns/${id}` : "/api/campaigns", id ? "PUT" : "POST", body);
    fillCampaign(campaign, { keepEventDraft: true });
    await loadCampaigns();
    $("mission-campaign").value = String(campaign.id);
    feedback("campaign-feedback", "Campanha salva. O contexto já pode ser usado na criação de missões.");
  } catch (error) { feedback("campaign-feedback", error.message, true); }
  finally { setEditorBusy("campaign", false); }
}

async function saveEvent(event) {
  event.preventDefault();
  if (state.campaignBusy || !state.campaignId || !$("event-form").reportValidity()) return;
  const text = $("event-text").value.trim();
  if (text.length < 3) { feedback("event-feedback", "Descreva o acontecimento antes de registrar.", true); return; }
  setEditorBusy("campaign", true); feedback("event-feedback", "Registrando…");
  const id = state.campaignId;
  let saved = false;
  try {
    await jsonRequest(`/api/campaigns/${id}/events`, "POST", { text });
    saved = true; $("event-text").value = "";
    const campaign = await request(`/api/campaigns/${id}`);
    renderEvents(campaign.events || []);
    feedback("event-feedback", "Acontecimento registrado na campanha.");
  } catch (error) {
    feedback("event-feedback", saved
      ? `Acontecimento registrado. Não foi possível atualizar o histórico: ${error.message} Reabra esta campanha para conferir; não é necessário registrar novamente.`
      : error.message, true);
  } finally { setEditorBusy("campaign", false); }
}

const chatEmptyTemplate = $("consult-empty").cloneNode(true);
$("consult-form").addEventListener("submit", submitChat);
$("search").addEventListener("click", () => generate("consult", true));
$("question").addEventListener("input", () => { updateQuestionCount(); saveChatDraft(); });
$("question").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!$("ask").disabled) $("consult-form").requestSubmit();
  }
});
$("chat-mode").addEventListener("change", updateConversationControls);
$("new-conversation").addEventListener("click", newConversation);
$("retry-message").addEventListener("click", () => {
  const pending = currentPending();
  if (pending && !$("retry-message").disabled) sendPendingMessage(pending);
});
$("refresh-conversation").addEventListener("click", () => {
  if (state.conversation && !$("refresh-conversation").disabled) selectConversation(state.conversation.id);
});
$("delete-conversation").addEventListener("click", () => {
  $("delete-conversation-confirm").classList.remove("hidden"); $("cancel-delete-conversation").focus();
});
function cancelDeleteConversation() {
  $("delete-conversation-confirm").classList.add("hidden"); $("delete-conversation").focus();
}
$("cancel-delete-conversation").addEventListener("click", cancelDeleteConversation);
$("delete-conversation-confirm").addEventListener("keydown", (event) => {
  if (event.key === "Escape") cancelDeleteConversation();
});
$("confirm-delete-conversation").addEventListener("click", deleteConversation);
document.querySelectorAll("[data-question]").forEach((button) => button.addEventListener("click", () => {
  $("question").value = button.dataset.question; $("question").dispatchEvent(new Event("input")); $("question").focus();
}));
$("mission-form").addEventListener("submit", (event) => { event.preventDefault(); generate("mission"); });
$("refresh-status").addEventListener("click", loadStatus);
$("new-campaign").addEventListener("click", newCampaign); $("campaign-form").addEventListener("submit", saveCampaign); $("event-form").addEventListener("submit", saveEvent);
window.addEventListener("hashchange", () => { const name = window.location.hash.slice(1); if (viewNames[name]) setView(name, true); });
setView(window.location.hash.slice(1));
updateQuestionCount();
Promise.allSettled([loadStatus(), loadCampaigns(), restoreChat()]);
