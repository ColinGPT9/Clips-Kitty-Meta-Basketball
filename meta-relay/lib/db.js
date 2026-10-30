/** D1 helpers. Every query is a prepared statement with bound values. */

import { randomId } from "./crypto.js";

export function now() {
  return Math.floor(Date.now() / 1000);
}

/** Unix seconds as the PC reads a time: "2026-11-04T21:00:00Z". */
export function iso(seconds) {
  return new Date(seconds * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
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

/** A decision waiting for the PC to collect it (GET /v1/commands). */
export async function addCommand(env, deviceId, approvalId, kind, body) {
  const id = randomId("cmd");
  await run(env, "INSERT INTO commands (id, device_id, approval_id, kind, body, created_at) VALUES (?, ?, ?, ?, ?, ?)",
    id, deviceId, approvalId, kind, JSON.stringify(body), now());
  return id;
}

/**
 * A PC's preferences as it last sent them (POST /v1/preferences), with the
 * defaults for any it never sent. A PC from before whatsapp_enabled only
 * ever sent anything with WhatsApp on, so a missing one reads as on.
 */
export function prefsOf(device) {
  return {
    notify_on_finish: true, notify_on_fail: true, notify_on_publish: true, remote_approvals: false, whatsapp_enabled: true,
    preferred: [], remind_minutes: 120, ...parse(device.prefs || "{}", {}),
  };
}

export function parse(text, fallback) {
  try {
    const v = JSON.parse(text);
    return v ?? fallback;
  } catch {
    return fallback;
  }
}
