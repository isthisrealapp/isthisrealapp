(() => {
  const form = document.getElementById("check-form");
  const queryEl = document.getElementById("query");
  const btn = document.getElementById("check-btn");
  const resultEl = document.getElementById("result");
  const quotaEl = document.getElementById("quota");
  const btnLabel = btn.querySelector(".btn-label");
  const btnWait = btn.querySelector(".btn-wait");

  function setLoading(on) {
    btn.disabled = on;
    btnLabel.hidden = on;
    btnWait.hidden = !on;
  }

  function escapeHtml(str) {
    return String(str)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function showQuota(remaining, limit) {
    if (!quotaEl || remaining == null || !limit) return;
    quotaEl.hidden = false;
    quotaEl.textContent =
      remaining === 0
        ? `You've used today's ${limit} free checks.`
        : `${remaining} of ${limit} free checks left today.`;
  }

  function stepHtml(step, index) {
    let body = escapeHtml(step.text || "");
    if (step.url) {
      const label = escapeHtml(step.link_label || step.url);
      const url = escapeHtml(step.url);
      const linked = `<a href="${url}" target="_blank" rel="noopener">${label}</a>`;
      if (body.includes(label)) {
        body = body.replace(label, linked);
      } else {
        body = `${body} ${linked}`;
      }
    }
    return `<li><span>${body}</span></li>`;
  }

  function renderResult(data) {
    const sources = (data.sources || []).slice(0, 3);
    const sourceHtml = sources.length
      ? `<ul class="sources">${sources.map((s) => {
          const ago = s.time_ago ? ` <span class="time-ago">${escapeHtml(s.time_ago)}</span>` : "";
          return `<li><a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${escapeHtml(s.name)}</a>${ago}</li>`;
        }).join("")}</ul>`
      : "";
    const sub = data.sublabel
      ? `<p class="sublabel">${escapeHtml(data.sublabel)}</p>`
      : "";
    const conf = data.confidence
      ? `<p class="confidence">${escapeHtml(data.confidence)}</p>`
      : "";
    const steps = (data.steps || []).filter((s) => s && s.text);
    const stepsHtml = steps.length
      ? `<div class="steps">
          <h3>What to do now</h3>
          <ol>${steps.map(stepHtml).join("")}</ol>
        </div>`
      : (data.what_to_do
        ? `<p class="action"><strong>What to do:</strong> ${escapeHtml(data.what_to_do)}</p>`
        : "");

    resultEl.hidden = false;
    resultEl.innerHTML = `
      <article class="result-card ${escapeHtml(data.verdict)}" id="verdict-card">
        <p class="verdict-word ${escapeHtml(data.verdict)}">${escapeHtml(data.display || data.label)}</p>
        ${sub}
        <p class="reason">${escapeHtml(data.reason)}</p>
        ${conf}
        ${stepsHtml}
        ${sourceHtml}
        <button type="button" class="share-btn" id="share-btn">Share</button>
      </article>
    `;
    showQuota(data.remaining, data.daily_limit);

    const shareBtn = document.getElementById("share-btn");
    const shareUrl = new URL(data.share_url, window.location.origin).toString();
    shareBtn.addEventListener("click", async () => {
      try {
        const res = await fetch(shareUrl);
        const blob = await res.blob();
        const file = new File([blob], `isthisrealapp-${data.verdict}.png`, { type: "image/png" });
        if (navigator.share && navigator.canShare && navigator.canShare({ files: [file] })) {
          await navigator.share({
            title: "IsThisRealApp",
            text: `${data.display || data.label}: ${data.reason}`,
            files: [file],
          });
          return;
        }
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `isthisrealapp-${data.verdict}.png`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (err) {
        if (err && err.name === "AbortError") return;
        window.open(shareUrl, "_blank", "noopener");
      }
    });

    resultEl.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }

  async function runCheck(query) {
    resultEl.hidden = true;
    resultEl.innerHTML = "";
    setLoading(true);
    try {
      const res = await fetch("/api/check", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query }),
      });
      const data = await res.json();
      if (!res.ok) {
        resultEl.hidden = false;
        resultEl.innerHTML = `<p class="error-msg">${escapeHtml(data.error || "Something went wrong.")}</p>`;
        if (data.daily_limit != null) showQuota(data.remaining, data.daily_limit);
        return;
      }
      renderResult(data);
    } catch (err) {
      resultEl.hidden = false;
      resultEl.innerHTML = `<p class="error-msg">Couldn't reach the checker. Try again.</p>`;
    } finally {
      setLoading(false);
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const query = queryEl.value.trim();
    if (!query) return;
    runCheck(query);
  });

  document.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const example = chip.getAttribute("data-example") || "";
      queryEl.value = example;
      if (example) runCheck(example);
    });
  });

  function bindStatusCells(root) {
    root.querySelectorAll(".status-cell").forEach((cell) => {
      cell.addEventListener("click", () => {
        const q = cell.getAttribute("data-query") || "";
        if (!q) return;
        queryEl.value = q;
        runCheck(q);
        document.getElementById("top").scrollIntoView({ behavior: "smooth", block: "start" });
      });
    });
  }
  bindStatusCells(document);

  function renderBoard(board) {
    const grid = document.getElementById("status-grid");
    const meta = document.getElementById("board-meta");
    if (!grid || !board || !board.items || !board.items.length) return;
    if (meta && board.checked_at_label) {
      meta.textContent = `Official pages, checked ${board.checked_at_label}`;
    }
    grid.innerHTML = board.items.map((item) => {
      const flag =
        item.level === "degraded" ? `<span class="status-flag">Issue</span>`
        : item.level === "down" ? `<span class="status-flag">Outage</span>`
        : item.level === "unknown" ? `<span class="status-flag">—</span>`
        : "";
      return `<li>
        <button type="button" class="status-cell level-${escapeHtml(item.level)}" data-query="${escapeHtml(item.query)}" title="${escapeHtml(item.summary || "")}">
          <span class="dot" aria-hidden="true"></span>
          <span class="status-name">${escapeHtml(item.name)}</span>
          ${flag}
        </button>
      </li>`;
    }).join("");
    bindStatusCells(grid);
  }

  const boot = document.getElementById("board-boot");
  if (boot && boot.textContent.trim()) {
    try { renderBoard(JSON.parse(boot.textContent)); } catch {}
  }
  fetch("/api/status-board")
    .then((r) => r.json())
    .then(renderBoard)
    .catch(() => {});

  const proForm = document.getElementById("pro-form");
  const proStatus = document.getElementById("pro-status");
  proForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("pro-email").value.trim();
    const brief = (proForm.querySelector('input[name="brief"]:checked') || {}).value || "weekly";
    const watchlist = Array.from(proForm.querySelectorAll('input[name="watchlist"]:checked')).map((el) => el.value);
    proStatus.hidden = false;
    proStatus.className = "form-status";
    proStatus.textContent = "Saving…";
    try {
      const res = await fetch("/api/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, watchlist, brief }),
      });
      const data = await res.json();
      proStatus.className = "form-status " + (res.ok ? "ok" : "err");
      proStatus.textContent = res.ok
        ? (data.message || "You're on the Pro early access list.")
        : (data.error || "Try again.");
    } catch {
      proStatus.className = "form-status err";
      proStatus.textContent = "Couldn't save that. Try again.";
    }
  });
})();
