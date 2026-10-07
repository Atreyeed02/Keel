// The posting form, in the page. Without this file the form still works:
// "Add line" and "Remove" ask the server for the form again (formmethod="get"),
// the server draws the balance panel, and the server decides what posts. With
// it:
// - the balance panel follows every keystroke, per currency, in whole cents
//   (BigInt), never floating point, so 0.10 + 0.20 balances 0.30 exactly;
// - its sentences are the server's (data-text, app/posting_messages.PANEL_TEXT);
// - "Add line" and "Remove" work without a round trip;
// - choosing an account fills in the line's currency, and a line whose
//   currency differs from its account's says so, as does an amount the
//   server would refuse;
// - while anything is out of balance, Post looks disabled but stays focusable:
//   pressing it moves the focus to the panel instead of posting;
// - screen readers hear the panel's state when it changes, once typing pauses;
// - once a post is on its way, the button shows "Posting…" and ignores a second
//   click (the submission_key already makes a repeat harmless).
(() => {
  const form = document.querySelector('form[action="/post-transaction"]');
  if (!form) return;
  const lines = form.querySelector("#lines");
  const template = document.querySelector("#line-template");
  const panel = form.querySelector(".balance");
  const banner = panel.querySelector("#balance-status");
  const announcer = panel.querySelector("#balance-announce");
  const table = panel.querySelector(".balance-table");
  const post = form.querySelector("#post-button");
  const postHint = form.querySelector("#post-hint");
  const addButton = form.querySelector("#add");
  const text = JSON.parse(panel.dataset.text);
  const postLabel = post.textContent;
  let state = "empty";

  const fill = (sentence, values) =>
    sentence.replace(/\{(\w+)\}/g, (match, key) => (key in values ? values[key] : match));

  const money = (cents) => {
    const whole = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return `${whole}.${(cents % 100n).toString().padStart(2, "0")}`;
  };

  const join = (items) =>
    items.length <= 1 ? items.join("") : `${items.slice(0, -1).join(", ")} and ${items.at(-1)}`;

  // An amount as the server reads it, to the cent: { cents }, { problem } or {}.
  const readAmount = (input) => {
    if (input.validity.badInput) return { problem: "amount_not_number" };
    const raw = input.value.trim();
    if (raw === "") return {};
    if (raw.startsWith("-")) return { problem: "amount_not_positive" };
    const match = /^(\d+)(?:\.(\d+))?$/.exec(raw);
    if (!match) return { problem: "amount_not_number" };
    const fraction = match[2] || "";
    if (/[^0]/.test(fraction.slice(2))) return { problem: "amount_places" };
    const cents = BigInt(match[1]) * 100n + BigInt((fraction + "00").slice(0, 2));
    return cents > 0n ? { cents } : { problem: "amount_not_positive" };
  };

  const allLines = () => [...lines.querySelectorAll("fieldset.line")];

  // Each line's hint slot gets an id of its own, kept when lines are renumbered.
  let hintCount = 0;
  const prepare = (line) => {
    line.querySelector(".line-hint").id = `hint-${++hintCount}`;
    line.hints = {};
  };

  // A hint under a line, one message per field, tied to that field. A field
  // the server marked (its "-problems" message) stays marked.
  const setHint = (line, field, message) => {
    if (message) line.hints[field] = message;
    else delete line.hints[field];
    const slot = line.querySelector(".line-hint");
    const messages = Object.values(line.hints);
    slot.textContent = messages.join(" ");
    slot.hidden = messages.length === 0;
    const input = line.querySelector(field);
    const ids = (input.getAttribute("aria-describedby") || "")
      .split(" ")
      .filter((id) => id && id !== slot.id);
    if (message) ids.push(slot.id);
    if (ids.length) input.setAttribute("aria-describedby", ids.join(" "));
    else input.removeAttribute("aria-describedby");
    if (message || ids.some((id) => id.endsWith("-problems"))) input.setAttribute("aria-invalid", "true");
    else input.removeAttribute("aria-invalid");
  };

  const checkCurrency = (line) => {
    const option = line.querySelector(".line-account").selectedOptions[0];
    const typed = line.querySelector(".currency").value.trim().toUpperCase();
    const held = option && option.dataset.currency;
    const wrong = held && typed && typed !== held;
    setHint(line, ".currency", wrong ? fill(text.wrong_currency, { account: option.dataset.name, currency: held }) : "");
  };

  const checkAmount = (line) => {
    const { problem } = readAmount(line.querySelector(".amount"));
    setHint(line, ".amount", problem ? text[problem] : "");
  };

  // Line numbers live in ids, the legend, the visible number and "Remove".
  const renumber = () => {
    allLines().forEach((line, index) => {
      const n = index + 1;
      line.id = `line-${n}`;
      line.querySelector("legend").textContent = `Line ${n}`;
      line.querySelector(".line-number").textContent = String(n).padStart(2, "0");
      const remove = line.querySelector(".remove");
      remove.value = String(n);
      remove.querySelector(".sr-only").textContent = ` line ${n}`;
    });
    addButton.formAction = `/post-transaction#line-${allLines().length + 1}`;
  };

  // Debits and credits per currency, in the order each currency first appears.
  const totals = () => {
    const byCurrency = new Map();
    for (const line of allLines()) {
      const { cents } = readAmount(line.querySelector(".amount"));
      const currency = line.querySelector(".currency").value.trim().toUpperCase();
      if (!cents || !currency) continue;
      if (!byCurrency.has(currency)) byCurrency.set(currency, { debits: 0n, credits: 0n });
      const side = line.querySelector(".line-side").value === "debit" ? "debits" : "credits";
      byCurrency.get(currency)[side] += cents;
    }
    return byCurrency;
  };

  const element = (tag, content, className) => {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  };

  // Screen readers hear the state once typing pauses, and only when it changed.
  let announced = banner.querySelector(".balance-title").textContent;
  let pending;
  const announce = (title) => {
    clearTimeout(pending);
    pending = setTimeout(() => {
      if (title !== announced) {
        announced = title;
        announcer.textContent = title;
      }
    }, 750);
  };

  const render = () => {
    const byCurrency = totals();
    const off = [...byCurrency].filter(([, sums]) => sums.debits !== sums.credits);
    let title;
    let detail = "";
    if (byCurrency.size === 0) {
      state = "empty";
      title = text.empty;
    } else if (off.length === 0) {
      state = "balanced";
      title = text.balanced_title;
      detail = fill(text.balanced_detail, { currencies: join([...byCurrency.keys()]) });
    } else if (off.length === 1) {
      const [currency, { debits, credits }] = off[0];
      const diff = money(debits > credits ? debits - credits : credits - debits);
      state = "off";
      title = fill(text.off_title, { diff, currency });
      detail = fill(debits > credits ? text.off_debits : text.off_credits, { diff });
    } else {
      state = "off";
      title = fill(text.off_many_title, { count: off.length });
      const directions = new Set(off.map(([, sums]) => sums.debits > sums.credits));
      detail = directions.size === 2 ? text.no_conversion : text.off_many_detail;
    }

    banner.className = `balance-banner is-${state}`;
    const icon = { balanced: "check-circle", off: "error", empty: "info" }[state];
    banner.querySelector("use").setAttribute("href", `/static/icons/icons.svg#${icon}`);
    banner.querySelector(".balance-title").textContent = title;
    let detailNode = banner.querySelector(".balance-detail");
    if (!detailNode) {
      detailNode = element("span", "", "balance-detail");
      banner.querySelector("p").append(" ", detailNode);
    }
    detailNode.textContent = detail;

    table.querySelector("tbody").replaceChildren(
      ...[...byCurrency].map(([currency, { debits, credits }]) => {
        const diff = debits > credits ? debits - credits : credits - debits;
        const row = document.createElement("tr");
        const head = element("th", currency, "mono");
        head.scope = "row";
        const status = document.createElement("td");
        status.append(
          element(
            "span",
            diff === 0n ? text.chip_balanced : fill(text.chip_off, { diff: money(diff) }),
            `badge ${diff === 0n ? "badge-ok" : "badge-off"}`
          )
        );
        row.append(
          head,
          element("td", money(debits), "num debit"),
          element("td", money(credits), "num credit"),
          element("td", money(diff), "num"),
          status
        );
        return row;
      })
    );
    table.hidden = byCurrency.size === 0;

    const blocked = state === "off";
    post.classList.toggle("is-blocked", blocked);
    postHint.hidden = !blocked;
    if (blocked || post.classList.contains("is-busy")) post.setAttribute("aria-disabled", "true");
    else post.removeAttribute("aria-disabled");
    if (blocked) post.setAttribute("aria-describedby", postHint.id);
    else post.removeAttribute("aria-describedby");
    announce(title);
  };

  lines.addEventListener("input", render);
  lines.addEventListener("change", (event) => {
    const line = event.target.closest("fieldset.line");
    if (!line) return;
    if (event.target.matches(".line-account")) {
      const option = event.target.selectedOptions[0];
      if (option && option.dataset.currency) line.querySelector(".currency").value = option.dataset.currency;
    }
    if (event.target.matches(".amount")) checkAmount(line);
    checkCurrency(line);
    render();
  });
  lines.addEventListener("focusout", (event) => {
    const line = event.target.closest("fieldset.line");
    if (!line) return;
    if (event.target.matches(".amount")) checkAmount(line);
    if (event.target.matches(".currency")) checkCurrency(line);
  });

  // "Add line" and "Remove", without asking the server.
  addButton.addEventListener("click", (event) => {
    event.preventDefault();
    lines.append(template.content.cloneNode(true));
    const added = allLines().at(-1);
    prepare(added);
    renumber();
    render();
    added.querySelector(".line-account").focus();
  });
  lines.addEventListener("click", (event) => {
    const remove = event.target.closest(".remove");
    if (!remove) return;
    event.preventDefault();
    const all = allLines();
    if (all.length <= 2) return;
    const line = remove.closest("fieldset.line");
    const at = all.indexOf(line);
    const next = all[at + 1] || all[at - 1];
    line.remove();
    renumber();
    render();
    next.querySelector(".line-account").focus();
  });

  form.addEventListener("submit", (event) => {
    if (post.classList.contains("is-busy")) {
      event.preventDefault();
      return;
    }
    if (state === "off") {
      event.preventDefault();
      banner.focus();
      announced = banner.querySelector(".balance-title").textContent;
      announcer.textContent = announced;
      return;
    }
    post.classList.add("is-busy");
    post.setAttribute("aria-disabled", "true");
    post.textContent = "Posting…";
  });

  // Back from the next page, the browser may restore this one as it was left:
  // busy. Put the button back.
  window.addEventListener("pageshow", () => {
    post.classList.remove("is-busy");
    post.textContent = postLabel;
    render();
  });

  allLines().forEach((line) => {
    prepare(line);
    if (line.querySelector(".line-account").value) checkCurrency(line);
  });
  render();
})();
