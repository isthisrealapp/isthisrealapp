(() => {
  const form = document.getElementById("check-form");
  const queryEl = document.getElementById("query");
  const btn = document.getElementById("check-btn");
  const resultEl = document.getElementById("result");
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

    resultEl.hidden = false;
    resultEl.innerHTML = `
      <article class="result-card ${escapeHtml(data.verdict)}" id="verdict-card">
        <p class="verdict-word ${escapeHtml(data.verdict)}">${escapeHtml(data.display || data.label)}</p>
        ${sub}
        <p class="reason">${escapeHtml(data.reason)}</p>
        <p class="action"><strong>What to do:</strong> ${escapeHtml(data.what_to_do)}</p>
        ${sourceHtml}
        <button type="button" class="share-btn" id="share-btn">Share</button>
      </article>
    `;

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

  const alerts = document.getElementById("alerts-form");
  const status = document.getElementById("alerts-status");
  alerts.addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("alerts-email").value.trim();
    const phone = document.getElementById("alerts-phone").value.trim();
    const channel = (alerts.querySelector('input[name="channel"]:checked') || {}).value || "email";
    status.hidden = false;
    status.className = "form-status";
    status.textContent = "Saving…";
    try {
      const res = await fetch("/api/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, phone, channel }),
      });
      const data = await res.json();
      status.className = "form-status " + (res.ok ? "ok" : "err");
      status.textContent = res.ok ? "You're on the early access list." : (data.error || "Try again.");
    } catch {
      status.className = "form-status err";
      status.textContent = "Couldn't save that. Try again.";
    }
  });
})();
