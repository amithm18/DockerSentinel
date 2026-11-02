// routes.js: the request handler. The v2 image deletes this file on purpose,
// so server.js fails at startup with "Cannot find module './routes'".
const VERSION = process.env.APP_VERSION || 'unknown';

module.exports = function handle(req, res) {
  if (req.url === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    return res.end('ok');
  }
  if (req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ version: VERSION }));
  }
  res.writeHead(404);
  res.end();
};
