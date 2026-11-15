/* Log Pose AI — frontend em Vanilla JS (sem build).
 * IMPORTANTE: os `id`s de ARCS devem bater com backend/app/prompts.py (ARCS).
 */
(() => {
  "use strict";

  const ARCS = [
    { id: "east-blue", label: "East Blue", eps: "1–61" },
    { id: "alabasta", label: "Alabasta", eps: "62–135" },
    { id: "skypiea", label: "Skypiea", eps: "136–206" },
    { id: "water-7", label: "Water 7 / Enies Lobby", eps: "207–325" },
    { id: "thriller-bark", label: "Thriller Bark", eps: "326–384" },
    { id: "marineford", label: "Marineford", eps: "385–516" },
    { id: "fishman-island", label: "Ilha dos Homens-Peixe", eps: "517–574" },
    { id: "dressrosa", label: "Dressrosa", eps: "575–746" },
    { id: "whole-cake", label: "Whole Cake", eps: "747–889" },
    { id: "wano", label: "Wano", eps: "890–1085" },
    { id: "egghead", label: "Egghead", eps: "1086+" },
  ];

  const SUGGESTIONS = [
    "Quem é Monkey D. Luffy?",
    "Qual a recompensa do Zoro agora?",
    "O que a Mera Mera no Mi faz?",
    "Me fale do Going Merry",
    "Quais episódios deste arco são fillers?",
    "Quem são os Chapéus de Palha até aqui?",
    "Uma teoria sobre o One Piece (sem spoiler!)",
  ];

  const TYPE_LABEL = {
    character: "Personagem",
    devil_fruit: "Akuma no Mi",
    ship: "Navio",
    island: "Ilha",
    affiliation: "Afiliação",
  };

  const HISTORY_LIMIT = 6; // SPEC 03 (o backend também corta em 6)
  const HISTORY_ITEM_CHARS = 1500;
  const STORAGE_KEY = "logpose.arc";
  const THEME_KEY = "logpose.theme"; // o mesmo nome é lido no <head> do index.html
  const THEME_COLOR = { dark: "#0B132B", light: "#F6F0E2" }; // <meta name="theme-color">

  const $ = (sel) => document.querySelector(sel);
  const els = {
    arc: $("#arc"), log: $("#log"), messages: $("#messages"), chips: $("#chips"),
    form: $("#composer"), input: $("#input"), send: $("#send"), clear: $("#clear"), theme: $("#theme"),
  };

  /** @type {{role: "user"|"assistant", content: string}[]} */
  let history = [];
  let busy = false;

  // ------------------------------------------------------------------ helpers
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* modo privado */ } },
  };

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  marked.setOptions({ gfm: true, breaks: true });
  DOMPurify.addHook("afterSanitizeAttributes", (node) => {
    if (node.tagName === "A") { node.setAttribute("target", "_blank"); node.setAttribute("rel", "noopener noreferrer"); }
  });
  // Texto do chat sem mídia/formulários: imagens só aparecem nos cards (montados via DOM, sem innerHTML)
  const renderMarkdown = (md) =>
    DOMPurify.sanitize(marked.parse(md || ""), { FORBID_TAGS: ["img", "style", "form", "input", "iframe"] });

  const formatBerries = (n) => `฿ ${Number(n).toLocaleString("pt-BR")}`;
  const currentArc = () => ARCS.find((a) => a.id === els.arc.value);
  const scrollToBottom = () => els.log.scrollTo({ top: els.log.scrollHeight, behavior: "smooth" });

  // ------------------------------------------------------------------ tema (noite/dia)
  // O <head> já aplicou o tema antes de desenhar a página; aqui ficam o botão e a troca.
  const prefersLight = window.matchMedia ? window.matchMedia("(prefers-color-scheme: light)") : null;
  const systemTheme = () => (prefersLight?.matches ? "light" : "dark");
  const savedTheme = () => {
    const t = store.get(THEME_KEY);
    return t === "light" || t === "dark" ? t : null;
  };

  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", THEME_COLOR[theme]);
    const label = theme === "light" ? "Ativar modo noite" : "Ativar modo dia";
    els.theme.setAttribute("aria-label", label);
    els.theme.title = label;
  }

  function initTheme() {
    applyTheme(savedTheme() || systemTheme());
    els.theme.addEventListener("click", () => {
      const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
      store.set(THEME_KEY, next);
      applyTheme(next);
    });
    // Sem escolha manual, acompanha o sistema (ex.: Windows trocando de tema no fim do dia)
    prefersLight?.addEventListener?.("change", () => {
      if (!savedTheme()) applyTheme(systemTheme());
    });
  }

  // ------------------------------------------------------------------ mensagens
  function botAvatar() {
    const a = el("div", "mt-1 flex h-8 w-8 shrink-0 items-center justify-center rounded-full border border-gold/60 bg-hull text-base");
    a.textContent = "🧭";
    a.setAttribute("aria-hidden", "true");
    return a;
  }

  function addUserMessage(text) {
    const row = el("div", "flex justify-end");
    const bubble = el("div", "max-w-[85%] whitespace-pre-wrap break-words rounded-2xl rounded-br-sm border border-sea/50 bg-sea/15 px-4 py-2.5 text-sm text-ink", text);
    row.append(bubble);
    els.messages.append(row);
    scrollToBottom();
  }

  function addBotMessage(markdown, cards = [], { error = false } = {}) {
    const row = el("div", "flex gap-3");
    const col = el("div", "min-w-0 max-w-[92%] flex-1 sm:flex-none");
    const bubble = el(
      "div",
      `md rounded-2xl rounded-tl-sm border px-4 py-3 text-sm leading-relaxed ${
        error ? "is-error border-danger/50 bg-danger/10 text-danger-ink" : "border-rope bg-hull text-ink"
      }`
    );
    bubble.innerHTML = renderMarkdown(markdown);
    col.append(bubble);
    if (cards.length) col.append(renderCards(cards));
    row.append(botAvatar(), col);
    els.messages.append(row);
    scrollToBottom();
  }

  function addNotice(text) {
    const n = el("div", "mx-auto rounded-full border border-rope/70 px-3 py-1 text-center text-xs text-muted", text);
    els.messages.append(n);
    scrollToBottom();
  }

  function showTyping() {
    const row = el("div", "flex gap-3");
    row.id = "typing";
    const bubble = el("div", "flex items-center gap-2 rounded-2xl rounded-tl-sm border border-rope bg-hull px-4 py-3 text-xs text-muted");
    const dots = el("span", "flex gap-1");
    for (let i = 0; i < 3; i++) dots.append(el("span", "dot inline-block h-1.5 w-1.5 rounded-full bg-gold"));
    bubble.append(dots, el("span", null, "Consultando o Log Pose…"));
    row.append(botAvatar(), bubble);
    els.messages.append(row);
    scrollToBottom();
  }
  const hideTyping = () => document.getElementById("typing")?.remove();

  // ------------------------------------------------------------------ cards visuais
  function renderCards(cards) {
    const grid = el("div", "mt-3 grid gap-3 sm:grid-cols-2");
    cards.forEach((c) => grid.append(renderCard(c)));
    return grid;
  }

  function portrait(card) {
    const wrap = el("div", "relative h-32 w-24 shrink-0 overflow-hidden rounded-lg border border-rope bg-abyss");
    const initials = (card.name || "?").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    const fallback = el("div", "flex h-full w-full items-center justify-center bg-gradient-to-br from-rope to-abyss font-pirate text-3xl text-gold", initials);
    if (card.image_url) {
      const img = el("img", `h-full w-full ${card.type === "character" ? "object-cover object-top" : "object-contain p-1"}`);
      img.src = card.image_url;
      img.alt = `Imagem de ${card.name}`;
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
      img.onerror = () => img.replaceWith(fallback);
      wrap.append(img);
    } else {
      wrap.append(fallback);
    }
    return wrap;
  }

  function infoRow(icon, label, value) {
    const row = el("div", "flex items-start gap-1.5 text-xs text-ink-2");
    row.append(el("span", "shrink-0", icon));
    const txt = el("span", "min-w-0 break-words");
    txt.append(el("span", "text-muted", `${label}: `), document.createTextNode(value));
    row.append(txt);
    return row;
  }

  function renderCard(card) {
    const isWanted = card.type === "character" && card.bounty != null;
    const article = el("article", `flex gap-3 rounded-xl border bg-hull p-3 shadow-lg shadow-black/20 ${isWanted ? "border-gold/60" : "border-rope"}`);
    article.append(portrait(card));

    const body = el("div", "flex min-w-0 flex-1 flex-col gap-1");
    const badge = el("span", "w-fit rounded-full border border-sea/50 bg-sea/15 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-sea-ink", TYPE_LABEL[card.type] || card.type);
    body.append(badge, el("h3", "truncate text-sm font-bold text-ink", card.name));
    if (card.subtitle) body.append(el("p", "truncate text-xs text-muted", card.subtitle));

    if (card.bounty != null) {
      const b = el("div", "mt-1 rounded-md border border-gold/40 bg-gold/10 px-2 py-1");
      b.append(el("p", "font-pirate text-[11px] uppercase tracking-[0.2em] text-gold", "Wanted · Recompensa"));
      b.append(el("p", "font-mono text-sm font-bold text-gold", formatBerries(card.bounty)));
      body.append(b);
    }
    if (card.devil_fruit) body.append(infoRow("🍎", "Fruta", card.devil_fruit));
    if (card.affiliation) body.append(infoRow("🏴‍☠️", "Afiliação", card.affiliation));
    (card.details || []).forEach((d) => body.append(infoRow("•", d.label, d.value)));
    if (card.note) body.append(el("p", "mt-1 text-xs italic text-muted", card.note));

    article.append(body);
    return article;
  }

  // ------------------------------------------------------------------ envio
  function setBusy(value) {
    busy = value;
    els.send.disabled = value;
    els.input.disabled = value;
    els.chips.querySelectorAll("button").forEach((b) => (b.disabled = value));
  }

  async function sendMessage(text) {
    const message = text.trim();
    if (!message || busy) return;
    const arc = currentArc();
    if (!arc) {
      els.arc.focus();
      els.arc.classList.add("ring-2", "ring-gold");
      addNotice("⚓ Antes de zarpar, escolha no topo onde você está no anime — assim evito spoilers!");
      return;
    }

    addUserMessage(message);
    els.input.value = "";
    autoGrow();
    setBusy(true);
    showTyping();

    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message,
          current_arc: arc.id,
          // 6 mensagens x 1500 chars mantém o corpo bem abaixo do client_max_body_size 16k do nginx
          history: history.slice(-HISTORY_LIMIT).map((h) => ({ role: h.role, content: h.content.slice(0, HISTORY_ITEM_CHARS) })),
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = typeof data.detail === "string" ? data.detail : null;
        throw new Error(
          res.status === 429 && !detail ? "Calma, marujo! Muitas perguntas seguidas. Aguarde alguns segundos." : detail || `Erro ${res.status} no servidor.`
        );
      }
      hideTyping();
      addBotMessage(data.reply, Array.isArray(data.cards) ? data.cards : []);
      history.push({ role: "user", content: message }, { role: "assistant", content: data.reply });
    } catch (err) {
      hideTyping();
      const msg = err instanceof TypeError ? "Não consegui alcançar o servidor. Verifique sua conexão." : err.message;
      addBotMessage(`**Tempestade à vista!** ${msg}`, [], { error: true });
    } finally {
      setBusy(false);
      els.input.focus();
    }
  }

  function autoGrow() {
    els.input.style.height = "auto";
    els.input.style.height = `${Math.min(els.input.scrollHeight, 160)}px`;
  }

  function welcome() {
    addBotMessage(
      "Ahoy! Eu sou o **Log Pose AI** 🧭 — seu guia pelo mundo de **One Piece**.\n\n" +
        "1. Escolha no topo **onde você está no anime**: eu nunca conto nada que aconteça depois desse ponto.\n" +
        "2. Pergunte sobre personagens, Akuma no Mi, recompensas, navios, ilhas, arcos, fillers ou teorias.\n\n" +
        "_Só navego pelos mares de One Piece — outros assuntos ficam fora da minha rota._"
    );
  }

  // ------------------------------------------------------------------ init
  function init() {
    initTheme();
    ARCS.forEach((a) => {
      const opt = el("option", null, `${a.label} (ep. ${a.eps})`);
      opt.value = a.id;
      els.arc.append(opt);
    });
    const saved = store.get(STORAGE_KEY);
    if (saved && ARCS.some((a) => a.id === saved)) els.arc.value = saved;

    els.arc.addEventListener("change", () => {
      els.arc.classList.remove("ring-2", "ring-gold");
      store.set(STORAGE_KEY, els.arc.value);
      const arc = currentArc();
      if (arc) addNotice(`🗺️ Posição atualizada: ${arc.label} — respostas filtradas até aqui.`);
    });

    SUGGESTIONS.forEach((q) => {
      const chip = el("button", "shrink-0 whitespace-nowrap rounded-full border border-rope bg-hull px-3 py-1.5 text-xs text-ink-2 transition hover:border-gold hover:text-gold focus:outline-none focus:ring-2 focus:ring-gold/40 disabled:opacity-50", q);
      chip.type = "button";
      chip.addEventListener("click", () => sendMessage(q));
      els.chips.append(chip);
    });

    els.form.addEventListener("submit", (e) => { e.preventDefault(); sendMessage(els.input.value); });
    els.input.addEventListener("input", autoGrow);
    els.input.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); sendMessage(els.input.value); }
    });
    els.clear.addEventListener("click", () => {
      history = [];
      els.messages.replaceChildren();
      welcome();
      els.input.focus();
    });

    welcome();
  }

  init();
})();
