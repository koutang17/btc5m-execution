// Forces every outbound request of the wallet CLI through a local proxy.
// Resolution order: BTC5M_PROXY_URL env var -> ../config.json runtime.proxy_url -> local default.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { setGlobalDispatcher, ProxyAgent } from 'undici';

function resolveProxy() {
  if (process.env.BTC5M_PROXY_URL) return process.env.BTC5M_PROXY_URL;
  try {
    const here = dirname(fileURLToPath(import.meta.url));
    const cfg = JSON.parse(readFileSync(join(here, '..', 'config.json'), 'utf8'));
    if (cfg?.runtime?.proxy_url) return cfg.runtime.proxy_url;
  } catch { /* config not present: fall back */ }
  return 'http://127.0.0.1:7897';
}

setGlobalDispatcher(new ProxyAgent(resolveProxy()));
console.error('[proxy] global dispatcher set');
