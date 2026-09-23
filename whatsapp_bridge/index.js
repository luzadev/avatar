// Ponte WhatsApp per AvatarPy: si collega come "dispositivo collegato" (Baileys),
// scrive i messaggi nel database dell'archivio e offre una piccola API HTTP locale.
import http from 'node:http';
import path from 'node:path';
import fs from 'node:fs';
import { DatabaseSync } from 'node:sqlite';
import pino from 'pino';
import { Boom } from '@hapi/boom';
import makeWASocket, { useMultiFileAuthState, fetchLatestBaileysVersion, DisconnectReason, isJidGroup, jidNormalizedUser } from '@whiskeysockets/baileys';

const args = Object.fromEntries(process.argv.slice(2).map((a, i, arr) => a.startsWith('--') ? [a.slice(2), arr[i + 1]] : []).filter(Boolean));
const DATA = args.data || path.join(process.cwd(), 'data', 'whatsapp');
const PORT = Number(args.port || 8790);
const ME_NAME = args.me || 'io';
const AUTH_DIR = path.join(DATA, 'auth');
const DB_PATH = path.join(DATA, 'index.sqlite');
fs.mkdirSync(AUTH_DIR, { recursive: true });

// ── Database (stesso dell'archivio delle chat esportate) ─────────────────────
const db = new DatabaseSync(DB_PATH);
db.exec(`PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS chats (name TEXT PRIMARY KEY, file TEXT, mtime REAL, count INTEGER, first TEXT, last TEXT, participants TEXT);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, chat TEXT, ts TEXT, sender TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS idx_chat_ts ON messages(chat, ts);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(text, content='messages', content_rowid='id', tokenize='unicode61 remove_diacritics 2');
CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN INSERT INTO fts(rowid, text) VALUES (new.id, new.text); END;
CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN INSERT INTO fts(fts, rowid, text) VALUES ('delete', old.id, old.text); END;
CREATE TABLE IF NOT EXISTS wa_contacts (jid TEXT PRIMARY KEY, name TEXT, notify TEXT);
CREATE TABLE IF NOT EXISTS wa_seen (id TEXT PRIMARY KEY);`);
for (const col of ['source TEXT', 'jid TEXT', 'from_me INTEGER DEFAULT 0']) {
  try { db.exec(`ALTER TABLE messages ADD COLUMN ${col}`); } catch { /* già presente */ }
}
const stInsert = db.prepare('INSERT INTO messages (chat, ts, sender, text, source, jid, from_me) VALUES (?, ?, ?, ?, ?, ?, ?)');
const stSeen = db.prepare('INSERT OR IGNORE INTO wa_seen (id) VALUES (?)');
const stContact = db.prepare('INSERT INTO wa_contacts (jid, name, notify) VALUES (?, ?, ?) ON CONFLICT(jid) DO UPDATE SET name=COALESCE(excluded.name, name), notify=COALESCE(excluded.notify, notify)');
const stChatTouch = db.prepare(`INSERT INTO chats (name, file, mtime, count, first, last, participants) VALUES (?, 'live', 0, 1, ?, ?, ?)
  ON CONFLICT(name) DO UPDATE SET count = count + 1, last = excluded.last, first = COALESCE(first, excluded.first)`);

const groupNames = new Map();
let sock = null, state = { connection: 'closed', qr: null, me: null, error: null, received: 0 };

function fmtTs(sec) { const d = new Date(Number(sec) * 1000); const p = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; }
function textOf(m) {
  const x = m.message || {};
  const inner = x.ephemeralMessage?.message || x.viewOnceMessage?.message || x;
  return inner.conversation || inner.extendedTextMessage?.text || inner.imageMessage?.caption || (inner.imageMessage && '[immagine]')
    || inner.videoMessage?.caption || (inner.videoMessage && '[video]') || (inner.audioMessage && '[audio]') || (inner.documentMessage && `[documento ${inner.documentMessage.fileName || ''}]`)
    || (inner.stickerMessage && '[sticker]') || (inner.locationMessage && '[posizione]') || (inner.contactMessage && '[contatto]') || inner.reactionMessage?.text && `[reazione ${inner.reactionMessage.text}]` || '';
}
function contactName(jid) {
  if (!jid) return '?';
  const row = db.prepare('SELECT name, notify FROM wa_contacts WHERE jid = ?').get(jid);
  return row?.name || row?.notify || jid.split('@')[0];
}
async function chatName(jid) {
  if (isJidGroup(jid)) {
    if (!groupNames.has(jid)) {
      try { const md = await sock.groupMetadata(jid); groupNames.set(jid, md.subject); } catch { groupNames.set(jid, 'Gruppo ' + jid.split('@')[0]); }
    }
    return groupNames.get(jid);
  }
  const mine = jidNormalizedUser(sock?.user?.id || '');
  if (mine && jidNormalizedUser(jid) === mine) return 'Io (note personali)';
  return contactName(jid);
}
async function store(m, source) {
  const id = m.key?.id, jid = m.key?.remoteJid;
  if (!id || !jid || jid === 'status@broadcast') return false;
  if (stSeen.run(id).changes === 0) return false;
  const text = textOf(m);
  if (!text) return false;
  if (m.pushName && !m.key.fromMe) stContact.run(isJidGroup(jid) ? (m.key.participant || jid) : jid, null, m.pushName);
  const chat = await chatName(jid);
  const sender = m.key.fromMe ? (state.me?.name || ME_NAME) : (isJidGroup(jid) ? contactName(m.key.participant) : chat);
  const ts = fmtTs(m.messageTimestamp);
  stInsert.run(chat, ts, sender, text, source, jid, m.key.fromMe ? 1 : 0);
  stChatTouch.run(chat, ts.slice(0, 10), ts.slice(0, 10), '');
  state.received++;
  return true;
}

async function start() {
  const { state: auth, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
  const { version } = await fetchLatestBaileysVersion();
  sock = makeWASocket({ version, auth, logger: pino({ level: 'silent' }), browser: ['AvatarPy', 'Desktop', '1.0.0'], syncFullHistory: false, markOnlineOnConnect: false });
  sock.ev.on('creds.update', saveCreds);
  sock.ev.on('connection.update', (u) => {
    if (u.qr) { state.qr = u.qr; state.connection = 'qr'; }
    if (u.connection === 'open') { state.connection = 'open'; state.qr = null; state.error = null; state.me = { id: sock.user?.id, name: sock.user?.name }; }
    if (u.connection === 'close') {
      const code = new Boom(u.lastDisconnect?.error)?.output?.statusCode;
      state.connection = 'closed';
      if (code === DisconnectReason.loggedOut) { state.error = 'Sessione chiusa da WhatsApp: ricollega con il QR.'; fs.rmSync(AUTH_DIR, { recursive: true, force: true }); fs.mkdirSync(AUTH_DIR, { recursive: true }); setTimeout(start, 2000); }
      else { state.error = `Disconnesso (${code}); riconnessione…`; setTimeout(start, 3000); }
    }
  });
  sock.ev.on('contacts.upsert', (cs) => { for (const c of cs) stContact.run(c.id, c.name || null, c.notify || null); });
  sock.ev.on('contacts.update', (cs) => { for (const c of cs) if (c.id) stContact.run(c.id, c.name || null, c.notify || null); });
  sock.ev.on('messaging-history.set', async ({ contacts, messages }) => {
    for (const c of contacts || []) stContact.run(c.id, c.name || null, c.notify || null);
    for (const m of messages || []) { try { await store(m, 'history'); } catch { /* ignora */ } }
  });
  sock.ev.on('messages.upsert', async ({ messages }) => {
    for (const m of messages) { try { await store(m, 'live'); } catch (e) { console.error('store', e.message); } }
  });
}

// ── API HTTP locale ───────────────────────────────────────────────────────────
async function resolveJid(target) {
  const t = String(target || '').trim();
  const digits = t.replace(/[^\d]/g, '');
  if (/^\+?[\d\s]{8,}$/.test(t)) return `${digits}@s.whatsapp.net`;
  const like = `%${t.toLowerCase()}%`;
  const row = db.prepare('SELECT jid, name, notify FROM wa_contacts WHERE lower(name) = ? OR lower(notify) = ? OR lower(name) LIKE ? OR lower(notify) LIKE ? ORDER BY CASE WHEN lower(name) = ? THEN 0 ELSE 1 END LIMIT 1').get(t.toLowerCase(), t.toLowerCase(), like, like, t.toLowerCase());
  if (row) return row.jid;
  for (const [jid, name] of groupNames) if (name.toLowerCase().includes(t.toLowerCase())) return jid;
  const msg = db.prepare("SELECT jid FROM messages WHERE jid IS NOT NULL AND lower(chat) LIKE ? ORDER BY ts DESC LIMIT 1").get(like);
  return msg?.jid || null;
}
const server = http.createServer(async (req, res) => {
  const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(obj)); };
  try {
    const url = new URL(req.url, 'http://x');
    if (url.pathname === '/status') return send(200, state);
    if (url.pathname === '/logout') { try { await sock?.logout(); } catch { } fs.rmSync(AUTH_DIR, { recursive: true, force: true }); state = { connection: 'closed', qr: null, me: null, error: 'Scollegato.', received: 0 }; setTimeout(start, 1000); return send(200, { ok: true }); }
    if (url.pathname === '/resolve') { const jid = await resolveJid(url.searchParams.get('to')); return send(200, { jid, name: jid ? (isJidGroup(jid) ? groupNames.get(jid) : contactName(jid)) : null }); }
    if (url.pathname === '/send' && req.method === 'POST') {
      let body = ''; for await (const ch of req) body += ch;
      const { to, text } = JSON.parse(body || '{}');
      if (state.connection !== 'open') return send(409, { error: 'WhatsApp non collegato' });
      const jid = await resolveJid(to);
      if (!jid) return send(404, { error: 'Destinatario non trovato' });
      const sent = await sock.sendMessage(jid, { text: String(text) });
      try { await store(sent, 'live'); } catch { }
      return send(200, { ok: true, jid, name: isJidGroup(jid) ? groupNames.get(jid) : contactName(jid) });
    }
    send(404, { error: 'not found' });
  } catch (e) { send(500, { error: e.message }); }
});
server.listen(PORT, '127.0.0.1', () => console.log(`[bridge] in ascolto su 127.0.0.1:${PORT}`));
start().catch((e) => { state.error = e.message; console.error(e); });
