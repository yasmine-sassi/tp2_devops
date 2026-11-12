// Tir de charge PayFlex API (k6).
//
// Les seuils k6 sont binaires : ils sont donc réglés sur les seuils CRITIQUES du sujet
// (p95 < 500 ms, erreurs < 2 %). La zone d'avertissement (p95 entre 200 et 500 ms,
// erreurs entre 1 et 2 %) est évaluée par scripts/quality_gate.py à partir du résumé
// exporté avec --summary-export.
import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  vus: parseInt(__ENV.K6_VUS || '10', 10),
  duration: __ENV.K6_DURATION || '30s',
  thresholds: {
    http_req_duration: ['p(95)<=500'],
    http_req_failed: ['rate<=0.02'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
};

// PAN de test généré à l'exécution (aucun numéro de carte en clair dans le dépôt).
function luhnComplete(body) {
  let sum = 0;
  body.split('').reverse().forEach((c, i) => {
    let d = parseInt(c, 10);
    if (i % 2 === 0) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
  });
  return body + ((10 - (sum % 10)) % 10);
}
const CARD = luhnComplete('400000' + '0'.repeat(9));

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function setup() {
  const login = http.post(
    `${BASE_URL}/auth/token`,
    JSON.stringify({ username: __ENV.PAYFLEX_USER || 'demo', password: __ENV.PAYFLEX_PASSWORD || '' }),
    { headers: JSON_HEADERS, tags: { name: 'POST /auth/token' } },
  );
  if (login.status !== 200) {
    throw new Error(`authentification impossible (HTTP ${login.status})`);
  }
  const token = login.json('access_token');
  const headers = { ...JSON_HEADERS, Authorization: `Bearer ${token}` };
  const account = http.post(
    `${BASE_URL}/accounts`,
    JSON.stringify({ holder_name: 'k6 load test', currency: 'EUR', initial_balance: '999999999.00' }),
    { headers, tags: { name: 'POST /accounts' } },
  );
  return { headers, accountId: account.json('id') };
}

export default function (data) {
  const { headers, accountId } = data;

  const health = http.get(`${BASE_URL}/health`, { tags: { name: 'GET /health' } });
  check(health, { 'health 200': (r) => r.status === 200 });

  const accounts = http.get(`${BASE_URL}/accounts`, { headers, tags: { name: 'GET /accounts' } });
  check(accounts, { 'accounts 200': (r) => r.status === 200 });

  const payment = http.post(
    `${BASE_URL}/payments`,
    JSON.stringify({ account_id: accountId, amount: '1.00', currency: 'EUR', merchant: 'k6 shop', card_number: CARD }),
    { headers, tags: { name: 'POST /payments' } },
  );
  check(payment, {
    'payment 201': (r) => r.status === 201,
    'card masked': (r) => r.status === 201 && r.json('card_number').startsWith('****'),
  });

  const txns = http.get(`${BASE_URL}/transactions?account_id=${accountId}&limit=20`, {
    headers,
    tags: { name: 'GET /transactions' },
  });
  check(txns, { 'transactions 200': (r) => r.status === 200 });

  sleep(0.1);
}
