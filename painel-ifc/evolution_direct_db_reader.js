"use strict";

const fs = require("node:fs");
const http = require("node:http");
const crypto = require("node:crypto");
const databaseEnvPath = process.env.IFC_DATABASE_ENV_FILE;
if (databaseEnvPath) {
  for (const line of fs.readFileSync(databaseEnvPath, "utf8").split(/\r?\n/)) {
    const index = line.indexOf("=");
    if (index > 0) process.env[line.slice(0, index)] = line.slice(index + 1);
  }
}
const { PrismaClient } = require("@prisma/client");

const TOKEN = fs.readFileSync(process.env.IFC_READER_TOKEN_FILE || "/run/secrets/reader.token", "utf8").trim();
const CONTACTS = JSON.parse(fs.readFileSync(process.env.IFC_CONTACTS_FILE || "/run/secrets/contacts.json", "utf8"));
const GROUPS = JSON.parse(fs.readFileSync(process.env.IFC_GROUPS_FILE || "/run/secrets/groups.json", "utf8"));
const INSTANCES = ["Filipe_2", "Filipe", "Pastor Filipe"];
const EXPECTED_GROUPS = {
  "IFC": 44,
  "COBIM": 6,
  "Família": 3,
  "CONPASJ": 1,
  "Amigos": 3,
  "Pastores": 8,
  "Condomínio": 1,
};
const MAX_BODY = 4096;
const MAX_MESSAGES = 20000;
const prisma = new PrismaClient();

const jids = new Set();
for (const person of (CONTACTS && Array.isArray(CONTACTS.pessoas) ? CONTACTS.pessoas : [])) {
  for (const field of ["jids", "lids"]) {
    for (const jid of (Array.isArray(person[field]) ? person[field] : [])) {
      if (typeof jid !== "string" || !(jid.endsWith("@s.whatsapp.net") || jid.endsWith("@lid"))) {
        throw new Error("invalid_contact_allowlist");
      }
      jids.add(jid);
    }
  }
}
if (!jids.size || jids.size > 500 || !TOKEN) throw new Error("invalid_reader_configuration");
const groupJids = new Set();
const groupCounts = Object.fromEntries(Object.keys(EXPECTED_GROUPS).map(category => [category, 0]));
for (const group of (GROUPS && Array.isArray(GROUPS.groups) ? GROUPS.groups : [])) {
  if (!group || typeof group.remote_jid !== "string" || !/^[0-9]{5,}(?:-[0-9]{5,})?@g\.us$/.test(group.remote_jid)) {
    throw new Error("invalid_group_allowlist");
  }
  if (!Object.prototype.hasOwnProperty.call(EXPECTED_GROUPS, group.category) || groupJids.has(group.remote_jid)) {
    throw new Error("invalid_group_allowlist");
  }
  groupJids.add(group.remote_jid);
  groupCounts[group.category] += 1;
}
const groupCount = Object.values(EXPECTED_GROUPS).reduce((sum, count) => sum + count, 0);
const declaredCounts = GROUPS && GROUPS.category_counts;
if (groupJids.size !== groupCount || GROUPS.instance !== "Filipe" || GROUPS.expected_count !== groupCount ||
    Object.keys(EXPECTED_GROUPS).some(category => groupCounts[category] !== EXPECTED_GROUPS[category] ||
      !declaredCounts || declaredCounts[category] !== EXPECTED_GROUPS[category]) ||
    !declaredCounts || Object.keys(declaredCounts).length !== Object.keys(EXPECTED_GROUPS).length) {
  throw new Error("invalid_group_allowlist");
}
const ifcGroupJids = GROUPS.groups.filter(group => group.category === "IFC").map(group => group.remote_jid);
if (ifcGroupJids.length !== EXPECTED_GROUPS.IFC) throw new Error("invalid_ifc_group_allowlist");
const MAX_PARTICIPANT_MESSAGES = 100000;

function respond(res, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  res.writeHead(status, { "content-type": "application/json", "content-length": data.length, "cache-control": "no-store" });
  res.end(data);
}

const server = http.createServer((req, res) => {
  if (req.method === "GET" && req.url === "/health") return respond(res, 200, { status: "ok" });
  if (req.method !== "POST" || !["/v1/direct-messages", "/v1/group-messages", "/v1/group-participant-names"].includes(req.url)) return respond(res, 404, { error: "not_found" });
  const header = typeof req.headers.authorization === "string" ? req.headers.authorization : "";
  const supplied = header.startsWith("Bearer ") ? header.slice(7) : "";
  const a = Buffer.from(supplied);
  const b = Buffer.from(TOKEN);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return respond(res, 401, { error: "unauthorized" });

  let body = "";
  req.on("data", chunk => {
    body += chunk.toString("utf8");
    if (Buffer.byteLength(body) > (req.url === "/v1/group-participant-names" ? 512 * 1024 : MAX_BODY)) req.destroy();
  });
  req.on("end", async () => {
    try {
      const range = JSON.parse(body);
      if (req.url === "/v1/group-participant-names") {
        const participants = range && range.participants;
        const validId = value => typeof value === "string" && value.length <= 96 &&
          (/^[0-9]{7,20}(?:-[0-9]{5,20})?@(?:s\.whatsapp\.net|lid)$/i.test(value) || /^[0-9]{7,15}$/.test(value));
        const validPhone = value => value == null || (typeof value === "string" && /^[0-9]{7,15}$/.test(value));
        if (!Array.isArray(participants) || participants.length > 1000 || participants.some(item =>
            !item || !validId(item.id) || !validPhone(item.phone))) {
          return respond(res, 400, { error: "invalid_participant_list" });
        }
        const resolved = await prisma.$transaction(async tx => {
          await tx.$executeRawUnsafe("SET TRANSACTION READ ONLY");
          const instance = await tx.instance.findFirst({ where: { name: "Filipe" }, select: { id: true } });
          if (!instance) throw new Error("participant_instance_unavailable");
          const contacts = await tx.contact.findMany({
            where: { instanceId: instance.id }, select: { remoteJid: true, pushName: true },
          });
          const since = Math.floor(Date.now() / 1000) - 365 * 24 * 3600;
          const messages = await tx.message.findMany({
            where: { instanceId: instance.id, pushName: { not: null }, messageTimestamp: { gte: since },
              OR: ifcGroupJids.map(jid => ({ key: { path: ["remoteJid"], equals: jid } })) },
            select: { key: true, participant: true, pushName: true, messageTimestamp: true },
            orderBy: { messageTimestamp: "desc" }, take: MAX_PARTICIPANT_MESSAGES + 1,
          });
          if (messages.length > MAX_PARTICIPANT_MESSAGES) throw new Error("participant_name_limit_exceeded");
          const digits = value => String(value || "").replace(/[^0-9]/g, "");
          const aliases = new Map();
          const add = (identity, name, source, observedAt) => {
            const normalized = String(identity || "").trim().toLowerCase();
            const label = String(name || "").trim().slice(0, 100);
            if (!normalized || !label) return;
            if (!aliases.has(normalized)) aliases.set(normalized, { contacts: new Set(), messages: [] });
            const record = aliases.get(normalized);
            if (source === "group_message") {
              if (!record.messages.length || Number(observedAt) > Number(record.messages[0].observedAt)) {
                record.messages = [{ name: label, observedAt }];
              }
            } else record.contacts.add(label);
          };
          for (const contact of contacts) {
            const jid = String(contact.remoteJid || "").toLowerCase();
            const phone = digits(jid.split("@")[0]);
            add(jid, contact.pushName, "contact", 0);
            if (phone) add(phone, contact.pushName, "contact", 0);
          }
          for (const message of messages) {
            const key = message.key && typeof message.key === "object" ? message.key : {};
            const timestamp = Number(message.messageTimestamp || 0);
            const ids = [message.participant, key.participant, key.participantAlt].filter(value => typeof value === "string");
            for (const id of ids) {
              add(id, message.pushName, "group_message", timestamp);
              const phone = digits(id.split("@")[0]);
              if (phone) add(phone, message.pushName, "group_message", timestamp);
            }
          }
          return participants.map(item => {
            const id = item.id.toLowerCase();
            const phone = item.phone || digits(id.split("@")[0]);
            const candidateKeys = [...new Set([id, phone, phone ? `${phone}@s.whatsapp.net` : ""].filter(Boolean))];
            const found = candidateKeys.map(key => aliases.get(key)).filter(Boolean);
            const messageNames = found.flatMap(value => value.messages);
            if (messageNames.length) {
              messageNames.sort((a, b) => Number(b.observedAt) - Number(a.observedAt));
              const best = messageNames[0];
              return { id: item.id, name: best.name, name_source: "recent_ifc_group_message",
                name_observed_at: Number.isFinite(Number(best.observedAt)) && Number(best.observedAt) > 0
                  ? new Date(Number(best.observedAt) * 1000).toISOString() : null };
            }
            const contactNames = new Set(found.flatMap(value => [...value.contacts]));
            const name = contactNames.size === 1 ? [...contactNames][0] : null;
            return { id: item.id, name, name_source: name ? "evolution_saved_contact" : "unresolved", name_observed_at: null };
          });
        }, { maxWait: 10000, timeout: 180000 });
        return respond(res, 200, { status: "ok", scope: "IFC", participants: resolved });
      }
      const since = Number(range.window_start_ts);
      const until = Number(range.window_end_ts);
      const now = Math.floor(Date.now() / 1000);
      if (!Number.isInteger(since) || !Number.isInteger(until) || until > now + 300 || since >= until || until - since > 48 * 3600) {
        return respond(res, 400, { error: "invalid_time_window" });
      }
      const groupRequest = req.url === "/v1/group-messages";
      const allowedInstances = groupRequest ? ["Filipe"] : INSTANCES;
      const allowedJids = groupRequest ? groupJids : jids;
      const instances = await prisma.instance.findMany({ where: { name: { in: allowedInstances } }, select: { id: true, name: true } });
      if (instances.length !== allowedInstances.length) return respond(res, 503, { error: "evolution_instances_unavailable" });
      const records = await prisma.$transaction(async tx => {
        await tx.$executeRawUnsafe("SET TRANSACTION READ ONLY");
        const all = [];
        for (const instance of instances) {
          const rows = await tx.message.findMany({
            where: {
              instanceId: instance.id,
              OR: [...allowedJids].map(jid => ({ key: { path: ["remoteJid"], equals: jid } })),
              messageTimestamp: { gte: since, lte: until },
            },
            select: { key: true, messageType: true, message: true, messageTimestamp: true },
            orderBy: { messageTimestamp: "asc" },
            take: MAX_MESSAGES + 1,
          });
          if (rows.length > MAX_MESSAGES || all.length + rows.length > MAX_MESSAGES) throw new Error("direct_message_limit_exceeded");
          for (const row of rows) {
            const key = row.key && typeof row.key === "object" ? row.key : {};
            const jid = key.remoteJid;
            if (!allowedJids.has(jid) || (groupRequest ? !jid.endsWith("@g.us") : (jid.endsWith("@g.us") || jid.endsWith("@broadcast")))) throw new Error(groupRequest ? "group_message_scope_violation" : "direct_message_scope_violation");
            all.push({ instance: instance.name, key, messageType: row.messageType, message: row.message,
                       messageTimestamp: row.messageTimestamp });
          }
        }
        return all;
      }, { maxWait: 10000, timeout: 180000 });
      return respond(res, 200, { status: "ok", messages: records });
    } catch (error) {
      const reason = error && typeof error.message === "string" ? error.message : "reader_query_failed";
      const code = ["direct_message_limit_exceeded", "direct_message_scope_violation", "group_message_scope_violation", "participant_name_limit_exceeded"].includes(reason) ? 422 : 503;
      return respond(res, code, { error: code === 422 ? reason : "reader_query_failed" });
    }
  });
});

server.listen(8790, "0.0.0.0");
process.on("SIGTERM", async () => { server.close(); await prisma.$disconnect(); process.exit(0); });
