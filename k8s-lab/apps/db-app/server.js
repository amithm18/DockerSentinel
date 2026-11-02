// db-app: checks PostgreSQL every 5 s. After 3 failures in a row it logs the
// error and exits, so a "database down" problem becomes a visible pod crash.
const http = require('http');
const { Client } = require('pg');

const DATABASE_URL = process.env.DATABASE_URL;
const PORT = Number(process.env.PORT || 8080);
const CHECK_EVERY_MS = 5000;
const MAX_FAILURES = 3;

if (!DATABASE_URL) {
  console.error('[db-app] DATABASE_URL is not set');
  process.exit(1);
}

let lastCheckOk = false;
let failures = 0;

async function checkDb() {
  // A fresh connection per check keeps the logic simple and surfaces
  // connection errors (ECONNREFUSED, ENOTFOUND) directly.
  const client = new Client({ connectionString: DATABASE_URL, connectionTimeoutMillis: 3000 });
  try {
    await client.connect();
    await client.query('SELECT 1');
    if (!lastCheckOk) console.log('[db-app] database check OK');
    lastCheckOk = true;
    failures = 0;
  } catch (err) {
    lastCheckOk = false;
    failures++;
    console.error(`[db-app] database check failed (${failures}/${MAX_FAILURES}): ${err.code || ''} ${err.message}`);
    if (failures >= MAX_FAILURES) {
      // Full error (with stack) so the log contains everything a diagnoser needs.
      console.error('[db-app] giving up, last error:', err);
      process.exit(1);
    }
  } finally {
    client.end().catch(() => {});
  }
}

const server = http.createServer((req, res) => {
  if (req.url === '/healthz') {
    // Ready only while the database is reachable.
    res.writeHead(lastCheckOk ? 200 : 503, { 'Content-Type': 'text/plain' });
    return res.end(lastCheckOk ? 'ok' : 'database unreachable');
  }
  if (req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ app: 'db-app', dbOk: lastCheckOk, failures }));
  }
  res.writeHead(404);
  res.end();
});

server.listen(PORT, () => console.log(`[db-app] listening on ${PORT}`));
checkDb();
setInterval(checkDb, CHECK_EVERY_MS);
