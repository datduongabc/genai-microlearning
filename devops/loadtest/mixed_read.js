// Open-ended read traffic with think time: each virtual user is a lecturer moving between pages,
// pausing 10 to 20 s on each, so the request rate follows the number of people online (Little's law)
// instead of the API's own speed as in api_read.js. Stages step the load up so each level's p95 can be
// read from the CSV output (`--out csv=...`, one row per request with its time).
//
//   docker run --rm -v $PWD:/w -w /w -e BASE=https://<cloudfront>/api -e USERS=/w/users.json \
//     grafana/k6:1.3.0 run --out csv=/w/mixed.csv mixed_read.js
//
// Sign-ins happen once in setup: the access token lasts 15 minutes, so the run must stay under that.
import http from "k6/http";
import { check, sleep } from "k6";

const BASE = __ENV.BASE;
const users = JSON.parse(open(__ENV.USERS));
const step = (target, ramp, hold) => [{ duration: ramp, target }, { duration: hold, target }];

export const options = {
  setupTimeout: "5m",
  scenarios: {
    pages: {
      executor: "ramping-vus",
      startVUs: 0,
      // 2.2 requests a page and 15 s a page: about 0.15 a second per user, so 300, 600 and 1200 users ask for
      // about 45, 90 and 175 a second.
      stages: [...step(300, "1m", "2m"), ...step(600, "1m", "2m"), ...step(1200, "2m", "3m"), { duration: "30s", target: 0 }],
      gracefulRampDown: "20s",
    },
  },
  summaryTrendStats: ["med", "p(95)", "p(99)", "max"],
};

export function setup() {
  const accounts = [];
  for (const u of users) {
    let r;
    // Sign-in allows 10 a minute from one address.
    while ((r = http.post(`${BASE}/auth/login`, JSON.stringify({ email: u.email, password: u.password }),
      { headers: { "Content-Type": "application/json" } })).status === 429) sleep(7);
    const auth = { headers: { Authorization: `Bearer ${r.json("access_token")}` } };
    const modules = http.get(`${BASE}/modules?document_id=${u.document_id}`, auth).json() || [];
    accounts.push({ auth, course: u.course_id, document: u.document_id, modules: modules.map((m) => m.id) });
  }
  return accounts;
}

const ok = (r) => r.status === 200;

export default function (accounts) {
  const a = accounts[(__VU - 1) % accounts.length];
  const get = (url, page) => {
    const r = http.get(`${BASE}${url}`, { ...a.auth, tags: { page } });
    check(r, { [`${page} 200`]: ok });
  };
  const page = Math.random();
  if (page < 0.3) {
    get("/courses", "courses");
  } else if (page < 0.7) {
    // A course page: its documents, modules, flashcard sets and videos.
    get(`/documents?course_id=${a.course}`, "course");
    get(`/modules?document_id=${a.document}`, "course");
    get(`/courses/${a.course}/flashcard-sets`, "course");
    get(`/courses/${a.course}/videos`, "course");
  } else if (a.modules.length) {
    get(`/modules/${a.modules[0]}`, "module");
  } else {
    get(`/documents/${a.document}/status`, "status");
  }
  sleep(10 + Math.random() * 10);
}
