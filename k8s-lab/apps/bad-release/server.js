// bad-release: v1 works; v2 is missing routes.js and crashes on start.
const http = require('http');

console.log(`[bad-release] starting version ${process.env.APP_VERSION || 'unknown'}`);

// In the broken image this line throws: Error: Cannot find module './routes'
const handle = require('./routes');

const PORT = Number(process.env.PORT || 8080);
http.createServer(handle).listen(PORT, () => {
  console.log(`[bad-release] listening on ${PORT}`);
});
