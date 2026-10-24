// node --test tests/frontend.test.cjs
// Exercita as funções reais da interface com DOM mínimo e transporte controlado.
// Não abre navegador, não acessa o banco e não chama serviços externos.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { join } = require("node:path");
const vm = require("node:vm");

const staticDir = join(__dirname, "../src/rpg_assistente/static");
const html = readFileSync(join(staticDir, "index.html"), "utf8");
const source = readFileSync(join(staticDir, "app.js"), "utf8");
const sheetSource = readFileSync(join(staticDir, "sheets.js"), "utf8");

class Node {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.children = []; this.attributes = {};
    this.value = ""; this.disabled = false; this.className = ""; this.dataset = {};
    this.classList = {
      add: (name) => this.classList.toggle(name, true),
      remove: (name) => this.classList.toggle(name, false),
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(" ").filter(Boolean));
        if (enabled ?? !names.has(name)) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  set value(value) { this._value = String(value); }
  get value() { return this._value || ""; }
  remove() { if (this.parentElement) this.parentElement.children = this.parentElement.children.filter((node) => node !== this); }
  set textContent(value) { this.children = []; this.text = String(value); }
  get textContent() { return (this.text || "") + this.children.map((child) => child.textContent).join(""); }
  set innerHTML(_) { throw new Error("HTML não confiável não deve ser interpretado"); }
  append(...nodes) { nodes.forEach((node) => { node.parentElement = this; this.children.push(node); }); }
  replaceChildren(...nodes) { this.text = ""; this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  addEventListener() {}
  focus() {}
  reset() {}
  reportValidity() { return true; }
  get options() { return this.children; }
  querySelectorAll(selector) {
    return this.descendants().filter((node) => selector.split(", ").some((tag) => tag.startsWith(".") ? node.className.split(" ").includes(tag.slice(1)) : node.tagName === tag.toUpperCase()));
  }
  descendants() { return this.children.flatMap((node) => [node, ...node.descendants()]); }
}

function fixture() {
  const nodes = new Map();
  for (const match of html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)) {
    const node = new Node(match[1]);
    node.value = /\bvalue="([^"]*)"/.exec(match[0])?.[1] || "";
    nodes.set(match[2], node);
  }
  for (const match of html.matchAll(/<form\b[^>]*id="([^"]+)"[^>]*>([\s\S]*?)<\/form>/g)) {
    nodes.get(match[1]).append(...[...match[2].matchAll(/\bid="([^"]+)"/g)].map((item) => nodes.get(item[1])));
  }
  const calls = [], replies = [], storage = new Map();
  const context = vm.createContext({
    document: {
      getElementById: (id) => { assert.ok(nodes.has(id), `ID ausente no HTML: ${id}`); return nodes.get(id); },
      createElement: (tag) => new Node(tag),
      createTextNode: (text) => { const node = new Node("#text"); node.textContent = text; return node; },
      querySelectorAll: () => [],
    },
    Option: function (text, value) { const node = new Node("option"); node.textContent = text; node.value = value; return node; },
    fetch: (url, options) => {
      calls.push({ url, method: options.method || "GET", body: options.body && JSON.parse(options.body) });
      assert.ok(replies.length, `Requisição inesperada: ${url}`);
      return replies.shift()();
    },
    AbortController, setTimeout, clearTimeout, Intl,
    crypto: require("node:crypto"),
    localStorage: { getItem: (key) => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) },
  });
  // Isola a camada de funções; o bootstrap com listeners é conferido no navegador.
  vm.runInContext(source.slice(0, source.indexOf("const chatEmptyTemplate =")), context);
  vm.runInContext(sheetSource.slice(0, sheetSource.indexOf("const sheetBootstrap =")), context);
  const app = vm.runInContext("({state, saveCampaign, selectCampaign, newCampaign, saveEvent, renderText, renderSources, renderResult, sheetState, blankSheet, fillSheet, applySheetRecord, addSheetRow, readSheetDraft, saveSheet, openSheet, newSheet, sheetChanged, submitSheetChat, sendSheetPending, storeSheetPending, downloadSheetPdf})", context);
  return { app, calls, nodes, reply(data, status = 200) {
    replies.push(() => Promise.resolve({ ok: status < 400, status, json: async () => data }));
  }, pause() {
    let release;
    replies.push(() => new Promise((resolve) => { release = (data, status = 200) => resolve({ ok: status < 400, status, json: async () => data }); }));
    return (data, status) => release(data, status);
  } };
}

test("campanha usa o retorno salvo sem GET extra nem troca de editor durante gravação", async () => {
  const f = fixture(); f.app.state.campaignId = 4;
  f.nodes.get("campaign-name").value = "Campanha original";
  f.nodes.get("event-text").value = "Rascunho de um acontecimento ainda não enviado";
  const finish = f.pause(); f.reply([{ id: 4, name: "Campanha original" }]);
  const saving = f.app.saveCampaign({ preventDefault() {} });
  await f.app.selectCampaign(8); f.app.newCampaign();
  assert.equal(f.app.state.campaignId, 4);
  assert.equal(f.calls.length, 1);
  finish({ id: 4, name: "Campanha original", events: [] }); await saving;
  assert.equal(f.calls[0].url, "/api/campaigns/4");
  assert.equal(f.calls.length, 2);
  assert.equal(f.nodes.get("campaign-name").disabled, false);
  assert.equal(f.nodes.get("event-text").value, "Rascunho de um acontecimento ainda não enviado");
  assert.match(f.nodes.get("campaign-feedback").textContent, /Campanha salva/);
});

test("evento salvo continua confirmado se a atualização visual falhar", async () => {
  const f = fixture(); f.app.state.campaignId = 4;
  f.nodes.get("event-text").value = "Os agentes encontraram a pista.";
  const finish = f.pause(); f.reply({ detail: "Falha ao atualizar" }, 500);
  const saving = f.app.saveEvent({ preventDefault() {} });
  await f.app.selectCampaign(8); f.app.newCampaign();
  assert.equal(f.calls.length, 1);
  finish({ id: 30 }); await saving;
  assert.equal(f.nodes.get("event-text").value, "");
  assert.match(f.nodes.get("event-feedback").textContent, /Acontecimento registrado\./);
  assert.match(f.nodes.get("event-feedback").textContent, /não é necessário registrar novamente/);
  assert.equal(f.nodes.get("save-event").disabled, false);
});

test("Markdown mantém HTML hostil como texto e só cria citações para fontes existentes", () => {
  const f = fixture(), target = new Node();
  f.app.renderText(target, '**Regra** <img src=x onerror=alert(1)> [1] [99]\n\n| Dado | Regra |\n| --- | --- |\n| −2d20 [na quantidade de dados] | <script>alert(1)</script> |', "result", 2);
  const descendants = target.descendants();
  assert.equal(descendants.some((node) => ["IMG", "SCRIPT"].includes(node.tagName)), false);
  assert.match(target.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.match(target.textContent, /−2d20 \[na quantidade de dados\]/);
  assert.match(target.textContent, /\[99\]/);
  assert.deepEqual(descendants.filter((node) => node.tagName === "A").map((node) => node.href), ["#result-source-1"]);
});

test("fontes históricas só abrem a página original quando há hash válido", () => {
  const f = fixture(), container = new Node();
  const sources = [{ pdf_page: 47, text: "<img src=x onerror=alert(1)>", title: "Livro" }];
  f.app.renderSources("test", sources, { container, historical: true });
  assert.equal(container.descendants().some((node) => node.tagName === "A"), false);
  f.app.renderSources("test", sources, { container, historical: true, bookHash: "a".repeat(64) });
  const link = container.descendants().find((node) => node.tagName === "A");
  assert.equal(link.href, `/api/book/pages/47/image?book_sha256=${"a".repeat(64)}`);
  const excerpt = container.descendants().find((node) => node.tagName === "PRE");
  assert.equal(excerpt.tabIndex, 0);
  assert.match(excerpt.attributes["aria-label"], /Texto da fonte 1/);
  assert.equal(container.descendants().some((node) => node.tagName === "IMG"), false);
});

test("conferência do chat exibe parâmetros, valores base, escopo e fontes", () => {
  const f = fixture();
  f.app.renderResult("consult", {
    status: "answered", answer: "Veja a conferência da ficha.", sources: [{ pdf_page: 22, text: "Atributos iniciais" }],
    rule_checks: [{
      arguments: { character_class: "combatente", nex: 5, attributes: { agilidade: 2, forca: 3, intelecto: 1, presenca: 1, vigor: 2 } },
      validation: { valid: true, derived: { pv_base: 22, pe_base: 3, defesa_base: 12 }, warnings: ["Poderes não foram conferidos."], scope: { description: "Apenas criação em NEX 5%." } },
      citation_ids: [1, 1, 99],
    }],
  });
  const check = f.nodes.get("consult-result").descendants().find((node) => node.className === "rule-checks");
  assert.ok(check);
  assert.equal(check.open, false);
  for (const text of ["Conferência parcial em Python", "ClasseCombatente", "NEX5%", "Força3", "Pontos de vida (base)22", "Poderes não foram conferidos.", "Apenas criação em NEX 5%.", "não aprova uma ficha completa"]) assert.ok(check.textContent.includes(text), text);
  assert.deepEqual(check.descendants().filter((node) => node.tagName === "A").map((node) => node.href), ["#consult-source-1"]);
});

test("conferência inválida fica aberta, preserva atributo zero e trata erros como texto", () => {
  const f = fixture();
  f.app.renderResult("consult", { answer: "Revise os dados.", rule_checks: [{
    arguments: { character_class: "especialista", nex: 35, attributes: { forca: 0 } },
    validation: { valid: false, errors: [{ field: "nex", message: "<img src=x onerror=alert(1)> Escopo limitado a NEX 5%." }] },
  }] });
  const check = f.nodes.get("consult-result").descendants().find((node) => node.className === "rule-checks");
  assert.equal(check.open, true);
  assert.match(check.textContent, /ajustes necessários/);
  assert.match(check.textContent, /Força0/);
  assert.match(check.textContent, /AgilidadeNão informado/);
  assert.match(check.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(check.descendants().some((node) => node.tagName === "IMG"), false);
});

test("histórico sem rule_checks não ganha conferência artificial", () => {
  const f = fixture();
  f.app.renderResult("consult", { status: "answered", answer: "Resposta antiga." });
  assert.equal(f.nodes.get("consult-result").descendants().some((node) => node.className === "rule-checks"), false);
});

function sheetRecord(f, overrides = {}) {
  return { id: 7, revision: 3, data: { ...f.app.blankSheet(), name: "Agente original", nex: 15 },
    messages: [], validation: { valid: false, errors: [{ field: "attributes", message: "Distribua os pontos." }] }, pdf_available: true, ...overrides };
}

test("ficha salva rascunho com revisão e bloqueia troca de destino durante gravação", async () => {
  const f = fixture(), record = sheetRecord(f); f.app.applySheetRecord(record);
  f.nodes.get("sheet-name").value = "Agente editado"; f.app.sheetChanged();
  const finish = f.pause(); f.reply([{ id: 7, name: "Agente editado", nex: 15, character_class: "combatente" }]);
  const saving = f.app.saveSheet();
  assert.equal(f.nodes.get("sheet-name").disabled, true);
  await f.app.openSheet(99); f.app.newSheet();
  assert.equal(f.calls.length, 1); assert.equal(f.calls[0].url, "/api/sheets/7");
  assert.equal(f.calls[0].body.revision, 3); assert.equal(f.calls[0].body.data.name, "Agente editado");
  assert.equal("derived" in f.calls[0].body.data, false);
  finish({ ...record, revision: 4, data: { ...record.data, name: "Agente editado" } }); await saving;
  assert.equal(f.app.sheetState.current.revision, 4); assert.equal(f.nodes.get("sheet-name").disabled, false);
  assert.match(f.nodes.get("sheet-validation").textContent, /Distribua os pontos/);
});

test("conflito de revisão mantém edição manual e não substitui estado por dados antigos", async () => {
  const f = fixture(); f.app.applySheetRecord(sheetRecord(f));
  f.nodes.get("sheet-notes").value = "Minha mudança ainda não salva"; f.app.sheetChanged();
  f.reply({ detail: "Revisão antiga" }, 409); await f.app.saveSheet();
  assert.equal(f.app.sheetState.current.revision, 3); assert.equal(f.app.sheetState.dirty, true);
  assert.equal(f.nodes.get("sheet-notes").value, "Minha mudança ainda não salva");
  assert.equal(f.nodes.get("sheet-notes").disabled, false);
  assert.match(f.nodes.get("sheet-feedback").textContent, /edição foi preservada/);
});

test("chat salva alterações antes da geração e aplica a mesma ficha retornada", async () => {
  const f = fixture(), record = sheetRecord(f); f.app.applySheetRecord(record);
  f.nodes.get("sheet-nex").value = "15"; f.nodes.get("sheet-origin").value = "Militar"; f.app.sheetChanged();
  f.nodes.get("sheet-question").value = "Sugira os equipamentos e preserve os atributos.";
  const saved = { ...record, revision: 4, data: { ...record.data, origin: "Militar" } };
  f.reply(saved);
  const finish = f.pause(); f.reply([{ id: 7, name: "Agente original", nex: 15, character_class: "combatente" }]);
  const sending = f.app.submitSheetChat();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls[0].method, "PUT"); assert.equal(f.calls[1].url, "/api/sheets/7/chat");
  assert.equal(f.calls[1].body.revision, 4); assert.ok(f.calls[1].body.request_id);
  assert.equal(f.nodes.get("sheet-origin").disabled, true);
  await f.app.openSheet(9); assert.equal(f.calls.length, 2);
  finish({ sheet: { ...saved, revision: 5, data: { ...saved.data, equipment: [{ name: "Mochila", quantity: 1, category: 0, spaces: 1, description: "Escolha sugerida" }] } }, messages: [{ id: 1, role: "assistant", content: "Equipamentos sugeridos." }] });
  await sending;
  assert.equal(f.app.sheetState.current.revision, 5);
  assert.equal(f.app.readSheetDraft().equipment[0].name, "Mochila");
  assert.equal(f.nodes.get("sheet-question").value, ""); assert.equal(f.app.sheetState.pending, null);
});

test("falha de transporte no chat preserva UUID e congela ficha até confirmação", async () => {
  const f = fixture(), record = sheetRecord(f); f.app.applySheetRecord(record);
  f.nodes.get("sheet-question").value = "Revise minhas escolhas.";
  f.reply({ detail: "Conexão interrompida" }, 502); await f.app.submitSheetChat();
  const pending = f.calls[0].body;
  assert.equal(f.app.sheetState.pending.request_id, pending.request_id);
  assert.equal(f.nodes.get("sheet-name").disabled, true);
  f.reply({ sheet: { ...record, revision: 4 }, messages: [{ id: 2, role: "assistant", request_id: pending.request_id, content: "Revisão concluída." }] });
  f.reply([]); await f.app.sendSheetPending();
  assert.equal(f.calls[1].body.request_id, pending.request_id); assert.equal(f.calls[1].body.revision, pending.revision);
  assert.equal(f.nodes.get("sheet-name").disabled, false); assert.equal(f.app.sheetState.pending, null);
});

test("arrays da ficha preservam zero e texto hostil sem interpretar HTML", () => {
  const f = fixture(); f.app.applySheetRecord(sheetRecord(f));
  f.app.addSheetRow("skills", { name: "Luta", bonus: 5, attribute: "forca", notes: "<script>alert(1)</script>" });
  f.app.addSheetRow("equipment", { name: "<img src=x>", quantity: 2, category: 0, spaces: 0.5, description: "Meu item" });
  const data = f.app.readSheetDraft();
  assert.equal(data.skills[0].bonus, 5); assert.equal(data.skills[0].attribute, "forca");
  assert.equal(data.equipment[0].category, 0); assert.equal(data.equipment[0].spaces, 0.5);
  assert.equal(data.equipment[0].name, "<img src=x>");
  assert.equal(f.nodes.get("sheet-equipment").descendants().some((node) => node.tagName === "IMG"), false);
});

test("abrir ficha reconcilia envio salvo quando a resposta já existe", () => {
  const f = fixture(), record = sheetRecord(f); f.app.applySheetRecord(record);
  f.app.storeSheetPending({ content: "Criar agente", request_id: "same-request", revision: 3 });
  f.app.applySheetRecord({ ...record, revision: 4, messages: [{ id: 1, request_id: "same-request", role: "assistant", content: "Pronto." }] });
  assert.equal(f.app.sheetState.pending, null); assert.equal(f.nodes.get("sheet-name").disabled, false);
});

test("exportação mostra rejeição do PDF e libera o editor sem perder a ficha", async () => {
  const f = fixture(); f.app.applySheetRecord(sheetRecord(f));
  const finish = f.pause(); const exporting = f.app.downloadSheetPdf();
  assert.equal(f.nodes.get("sheet-export").disabled, true);
  finish({ detail: "Este modelo PDF não suporta o caractere informado no nome." }, 422); await exporting;
  assert.equal(f.calls[0].url, "/api/sheets/7/pdf");
  assert.equal(f.nodes.get("sheet-export").disabled, false);
  assert.equal(f.nodes.get("sheet-name").value, "Agente original");
  assert.match(f.nodes.get("sheet-feedback").textContent, /não suporta o caractere/);
});
