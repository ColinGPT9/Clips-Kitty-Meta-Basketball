/** D1 helpers. Every query is a prepared statement with bound values. */

export function now() {
  return Math.floor(Date.now() / 1000);
}

export async function one(env, sql, ...args) {
  return (await env.DB.prepare(sql).bind(...args).first()) || null;
}

export async function all(env, sql, ...args) {
  const out = await env.DB.prepare(sql).bind(...args).all();
  return out.results || [];
}

/** Run a write; returns how many rows it changed. */
export async function run(env, sql, ...args) {
  const out = await env.DB.prepare(sql).bind(...args).run();
  return (out.meta && out.meta.changes) || 0;
}

export async function audit(env, { device = "", approval = "", actor, action, detail = "" }) {
  await run(env, "INSERT INTO audit (device_id, approval_id, actor, action, detail, at) VALUES (?, ?, ?, ?, ?, ?)",
    device, approval, actor, action, String(detail).slice(0, 300), now());
}

export function parse(text, fallback) {
  try {
    const v = JSON.parse(text);
    return v ?? fallback;
  } catch {
    return fallback;
  }
}
