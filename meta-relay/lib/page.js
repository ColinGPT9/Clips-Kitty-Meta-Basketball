/**
 * The approval page a WhatsApp link opens. Plain HTML and a form: no
 * script, no external resource, nothing to load but this page and its
 * thumbnails. A GET never changes anything (link previews and crawlers are
 * harmless); a decision is a POST carrying the page's CSRF token, and an
 * approval also needs the creator's PIN and an explicit "post publicly" tick.
 */

import { safeLink } from "./meta.js";

const LABELS = { instagram: "Instagram Reels", facebook: "Facebook Page Reels", threads: "Threads" };

export function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

const STYLE = `
:root{color-scheme:light dark;--bg:#0f1115;--card:#181b22;--text:#f2f3f5;--muted:#a3a9b6;--line:#2a2f3a;--accent:#e4572e;--ok:#2fbf71;--bad:#ef4444}
@media (prefers-color-scheme: light){:root{--bg:#f6f7f9;--card:#fff;--text:#14161a;--muted:#5b6170;--line:#e3e6ec}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:560px;margin:0 auto;padding:16px}
h1{font-size:1.3rem;margin:8px 0 4px}h2{font-size:1rem;margin:20px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin:10px 0}
.muted{color:var(--muted)}.small{font-size:.85rem}
.clip{display:flex;gap:12px;align-items:flex-start}.clip img{width:72px;height:128px;object-fit:cover;border-radius:8px;background:#000;flex:none}
.clip .ph{width:72px;height:128px;border-radius:8px;background:linear-gradient(160deg,#2a2f3a,#111);flex:none;display:flex;align-items:center;justify-content:center;font-size:1.6rem}
label.row{display:flex;gap:10px;align-items:flex-start;cursor:pointer}input[type=checkbox]{width:22px;height:22px;margin-top:2px;accent-color:var(--accent);flex:none}
details{margin-top:6px}summary{cursor:pointer;color:var(--muted);font-size:.85rem}pre{white-space:pre-wrap;font:inherit;font-size:.85rem;margin:6px 0 0}
.pill{display:inline-block;padding:2px 8px;border-radius:999px;background:var(--line);font-size:.75rem;margin-right:4px}
input[type=password]{width:100%;font-size:1.3rem;padding:10px 12px;border-radius:10px;border:1px solid var(--line);background:var(--bg);color:var(--text);letter-spacing:.3em}
.buttons{display:grid;gap:10px;margin:16px 0 28px}
button{font:inherit;font-weight:600;padding:14px;border-radius:12px;border:1px solid var(--line);background:var(--card);color:var(--text)}
button.go{background:var(--accent);border-color:var(--accent);color:#fff}
.ok{color:var(--ok)}.bad{color:var(--bad)}.notice{border-left:4px solid var(--accent)}
a{color:var(--accent)}footer{color:var(--muted);font-size:.75rem;text-align:center;margin:24px 0}
`;

function shell(title, body, refresh = 0) {
  return `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">${refresh ? `<meta http-equiv="refresh" content="${refresh}">` : ""}
<title>${esc(title)}</title><style>${STYLE}</style></head>
<body><main>${body}<footer>Clips Kitty remote approval. Nothing is posted without your approval on this page.</footer></main></body></html>`;
}

export function message(title, text, { tone = "" } = {}) {
  return shell(title, `<h1>${esc(title)}</h1><div class="card notice"><p class="${tone}">${esc(text)}</p></div>`);
}

function left(expiresAt, nowS) {
  const s = Math.max(0, expiresAt - nowS);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h} h ${m} min` : `${m} min`;
}

function clipCard(c, token, { selectable, thumbs }) {
  const pic = thumbs.has(c.clip_id)
    ? `<img src="/a/${esc(token)}/thumb/${esc(c.clip_id)}" alt="">`
    : `<div class="ph" aria-hidden="true">&#9654;</div>`;
  const posted = (c.published || []).map((p) => LABELS[p]).filter(Boolean);
  const already = posted.length ? `<br><span class="small muted">Already posted to ${esc(posted.join(", "))}, so it is not posted there again.</span>` : "";
  const texts = c.texts || {};
  const preview = `<details><summary>What will be posted</summary>
<pre><b>Instagram</b>\n${esc(texts.instagram)}\n\n<b>Facebook</b>\n${esc(texts.facebook)}\n\n<b>Threads</b>\n${esc(texts.threads)}</pre>
<p class="small muted">${c.text_source === "muse_spark" ? "Written by Muse Spark from what Clips Kitty found." : "From the clip's own title and hashtags."}</p></details>`;
  const head = `<div><b>${esc(c.title || c.label || "Highlight")}</b><br>
<span class="pill">${esc(c.label || c.category || "highlight")}</span><span class="small muted">${esc(Math.round(c.duration || 0))} s</span>${already}${preview}</div>`;
  if (!selectable) return `<div class="card clip">${pic}${head}</div>`;
  return `<label class="card row clip"><input type="checkbox" name="clip" value="${esc(c.clip_id)}" checked>${pic}${head}</label>`;
}

/** The page for an offer, in whatever state it is in. */
export function approvalPage({ approval, payload, token, csrf, accounts, thumbs, nowS, error = "" }) {
  const title = payload.title || "Your video";
  const head = `<h1>${esc(title)}</h1><p class="muted">${esc(payload.summary_text || "")}</p>${payload.muse_line ? `<p class="small">${esc(payload.muse_line)} <span class="pill">Muse Spark</span></p>` : ""}`;
  const clips = payload.clips || [];
  const state = approval.state;

  if (state === "expired" || (["open", "deferred"].includes(state) && approval.expires_at <= nowS)) {
    return shell(title, `${head}<div class="card notice">This link has expired, so nothing can be approved from it. Open Clips Kitty on your PC to send a new one.</div>`);
  }
  if (state === "cancelled") {
    return shell(title, `${head}<div class="card notice">Cancelled. Nothing was posted.</div>`);
  }
  if (state === "approved" || state === "rejected") {
    const pc = approval.pc_answer || {};
    let line = "Approved. Waiting for your PC to pick this up. If it is off, it will start when it is back on, as long as that is within 48 hours.";
    let tone = "";
    if (pc.outcome === "accepted") line = `Your PC accepted it: ${pc.reason || "publishing is queued"}. This page shows the links once the posts are confirmed.`;
    if (pc.outcome === "rejected" || state === "rejected") { line = `Your PC did not publish this: ${pc.reason || "it refused the approval"}. Nothing was posted.`; tone = "bad"; }
    return shell(title, `${head}<div class="card notice"><p class="${tone}">${esc(line)}</p></div>`, pc.outcome === "accepted" || !pc.outcome ? 20 : 0);
  }
  if (state === "done") {
    return shell(title, `${head}${resultsHtml(approval.results || {})}`);
  }

  const remoteOk = Boolean(approval.remote_ok);
  const listed = clips.map((c) => clipCard(c, token, { selectable: remoteOk, thumbs })).join("");
  const expires = `<p class="small muted">This link works for ${esc(left(approval.expires_at, nowS))} more.${state === "deferred" ? " You asked to be reminded; you can still decide now." : ""}</p>`;
  if (!remoteOk) {
    return shell(title, `${head}${expires}<div class="card notice">Approving from your phone is switched off. Open Clips Kitty on your PC to publish these.</div>${listed}`);
  }
  const preferred = new Set(payload.preferred || []);
  const dest = Object.keys(LABELS).map((p) => {
    const a = accounts[p] || {};
    if (!a.connected) return `<div class="card row muted"><input type="checkbox" disabled> ${esc(LABELS[p])} <span class="small">(not connected)</span></div>`;
    return `<label class="card row"><input type="checkbox" name="platform" value="${p}"${preferred.has(p) ? " checked" : ""}><span>${esc(LABELS[p])}<br><span class="small muted">${esc(a.name || "")}${p === "threads" ? " · a post linking to the Reel" : ""}</span></span></label>`;
  }).join("");
  const form = `<form method="post" action="/a/${esc(token)}">
<input type="hidden" name="csrf" value="${esc(csrf)}">
${error ? `<div class="card notice bad">${esc(error)}</div>` : ""}
<h2>Clips</h2>${listed}
<h2>Publish to</h2>${dest}
<h2>Confirm</h2>
<label class="card row"><input type="checkbox" name="confirm" value="yes"><span>Post the clips I ticked, publicly, to the places I ticked.</span></label>
<div class="card"><label for="pin" class="small muted">Approval PIN (set in Clips Kitty)</label>
<input id="pin" type="password" name="pin" inputmode="numeric" autocomplete="off" maxlength="8"></div>
<div class="buttons">
<button class="go" name="action" value="approve">Approve and publish</button>
<button name="action" value="defer">Remind me later</button>
<button name="action" value="cancel">Cancel, post nothing</button>
</div></form>`;
  return shell(title, `${head}${expires}${form}`);
}

export function resultsHtml(results) {
  const clips = results.clips || [];
  if (!clips.length) return `<div class="card notice">Nothing was confirmed as posted.</div>`;
  const t = results.totals || {};
  const top = `<div class="card"><b>${esc(t.published || 0)}</b> published${t.failed ? `, <span class="bad">${esc(t.failed)} failed</span>` : ""}${t.unconfirmed ? `, ${esc(t.unconfirmed)} not confirmed yet` : ""}${t.skipped ? `, ${esc(t.skipped)} skipped` : ""}.</div>`;
  const rows = clips.map((c) => {
    const posts = (c.posts || []).map((p) => {
      const link = safeLink(p.url);
      if (p.state === "published") {
        return `<li class="ok">${esc(LABELS[p.platform] || p.platform)}: published${link ? ` · <a href="${esc(link)}" rel="noopener noreferrer">open</a>` : " (no link returned yet)"}</li>`;
      }
      return `<li class="${p.state === "failed" ? "bad" : "muted"}">${esc(LABELS[p.platform] || p.platform)}: ${esc(p.state)}${p.error ? ` · ${esc(p.error)}` : ""}</li>`;
    }).join("");
    return `<div class="card"><b>${esc(c.title || `Clip ${c.clip_id}`)}</b><ul>${posts}</ul></div>`;
  }).join("");
  return `${top}${rows}<p class="small muted">Only posts the platforms confirmed are shown as published, with the links they returned.</p>`;
}

export function connectedPage(text, ok = true) {
  return message(ok ? "Connected" : "Not connected", text, { tone: ok ? "ok" : "bad" });
}

export function choosePageHtml(state, pages) {
  const items = pages.map((p) => `<label class="card row"><input type="radio" name="page" value="${esc(p.id)}" required><span><b>${esc(p.name)}</b><br><span class="small muted">${p.instagram ? `Instagram: ${esc(p.instagram)}` : "No Instagram account linked"}</span></span></label>`).join("");
  return shell("Choose a Page", `<h1>Which Facebook Page?</h1><p class="muted">Clips Kitty publishes Reels to this Page, and to the Instagram account linked to it.</p>
<form method="post" action="/oauth/facebook/choose"><input type="hidden" name="state" value="${esc(state)}">${items}
<div class="buttons"><button class="go">Use this Page</button></div></form>`);
}
