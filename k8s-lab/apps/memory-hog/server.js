// memory-hog: a lab app that uses memory on purpose.
//   MODE=steady  -> hold WORKING_SET_MB of memory (too big = "memory limit too low")
//   MODE=leak    -> after LEAK_DELAY_SEC, grow by LEAK_MB_PER_SEC every second ("memory leak")
const http = require('http');

const MODE = process.env.MODE || 'steady';
const WORKING_SET_MB = Number(process.env.WORKING_SET_MB || 30);
const LEAK_DELAY_SEC = Number(process.env.LEAK_DELAY_SEC || 20);
const LEAK_MB_PER_SEC = Number(process.env.LEAK_MB_PER_SEC || 10);
const PORT = Number(process.env.PORT || 8080);
const MB = 1024 * 1024;

// Keep references in an array so the garbage collector can never free them.
const held = [];

function allocate(mb) {
  // Fill with a NON-zero byte. Large blocks come from the kernel as fresh pages that
  // are already zero, so a plain Buffer.alloc(MB) skips writing them and they never
  // count as used memory (we measured rss=52 MB while "holding" 160 MB). Writing 1s
  // touches every page, so the memory really counts against the container limit.
  for (let i = 0; i < mb; i++) held.push(Buffer.alloc(MB, 1));
}

function rssMb() {
  return Math.round(process.memoryUsage().rss / MB);
}

console.log(`[memory-hog] starting MODE=${MODE} WORKING_SET_MB=${WORKING_SET_MB}`);
allocate(WORKING_SET_MB);
console.log(`[memory-hog] holding ${held.length} MB, rss=${rssMb()} MB`);

if (MODE === 'leak') {
  console.log(`[memory-hog] leak starts in ${LEAK_DELAY_SEC}s at ${LEAK_MB_PER_SEC} MB/s`);
  setTimeout(() => {
    setInterval(() => allocate(LEAK_MB_PER_SEC), 1000);
  }, LEAK_DELAY_SEC * 1000);
}

// Regular memory report so logs show the growth before an OOM kill.
setInterval(() => {
  console.log(`[memory-hog] mode=${MODE} held=${held.length} MB rss=${rssMb()} MB`);
}, 5000);

const server = http.createServer((req, res) => {
  if (req.url === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'text/plain' });
    return res.end('ok');
  }
  if (req.url === '/') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    return res.end(JSON.stringify({ app: 'memory-hog', mode: MODE, heldMb: held.length, rssMb: rssMb() }));
  }
  res.writeHead(404);
  res.end();
});

server.listen(PORT, () => console.log(`[memory-hog] listening on ${PORT}`));

// Node as PID 1 in a container ignores SIGTERM by default, so Kubernetes would
// wait the full 30 s grace period before killing the pod. Exit promptly instead.
process.on('SIGTERM', () => {
  console.log('[memory-hog] received SIGTERM, shutting down');
  process.exit(0);
});
