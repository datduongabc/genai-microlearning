// Closed-loop read traffic through the proxy: what a logged-in page does while people wait.
// There is no think time, so the result is the capacity of the API, not a count of users.
import http from "k6/http";
import { check } from "k6";
import { SharedArray } from "k6/data";

const users = new SharedArray("users", () => JSON.parse(open(__ENV.USERS || "/results/users.json")));
export const options = {
  vus: Number(__ENV.VUS || 100),
  duration: __ENV.DURATION || "60s",
  summaryTrendStats: ["avg", "med", "p(95)", "p(99)", "max"],
};

export default function () {
  const user = users[(__VU - 1) % users.length];
  const params = { headers: { Authorization: `Bearer ${user.token}` } };
  const base = __ENV.BASE || "http://proxy/api";
  const responses = http.batch([
    ["GET", `${base}/courses`, null, params],
    ["GET", `${base}/documents?course_id=${user.course_id}`, null, params],
    ["GET", `${base}/documents/${user.document_id}/status`, null, params],
  ]);
  check(responses[0], { "courses 200": (r) => r.status === 200 });
  check(responses[1], { "documents 200": (r) => r.status === 200 });
  check(responses[2], { "status 200": (r) => r.status === 200 });
}
