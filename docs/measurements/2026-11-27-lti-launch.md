# LTI launch under load, 2026-11-27

The question: with about 30,000 users and 3,000 to 6,000 online at once, can learners opening the tool from Moodle (LTI 1.3) get in at the start of a class?
Measured locally, at no cost; not on AWS.

## Method

The load stack of `devops/loadtest`, reduced to Postgres, Redis, one api container limited to 1 CPU (`docker update --cpus 1`, as one Fargate task has 1 vCPU) and the runner.
No embedder, worker or model: a launch uses none of them.

`drive_generation.py lti-launch` plays a fake LMS that signs id_tokens with a real RSA key and serves its key set to the api, as Moodle does.
Each learner does what the browser does: `GET /lti/login`, `POST /lti/launch` with the signed id_token, then `POST /lti/session` to trade the code for tokens.
The api's own checks run unchanged: state, signature, claims, account, course membership and launch code.
Learners arrive at a fixed rate whatever the api does, each from an address of its own (through `X-Forwarded-For`, which the api trusts from the load network), unless stated.
A lecturer launches first, since learners of a course nobody has opened are refused.
Each step ran 300 launches, after the api had gone idle and a minute had passed so no rate limit window carried over.

"First time" learners have no account yet: the launch creates the account and the membership.
"Returning" learners launched before.

Raw rows: `2026-11-27-lti-launch/lti.json`.
`lti-polluted.json` holds an earlier attempt whose warm-up overran the api, so its backlog spilled into the next steps; it is kept only as the record of that mistake.

## Finding 1: a first launch spent a quarter second hashing a password nobody uses

An account made by a launch signs in through the LMS only, yet it was stored with the bcrypt hash of a random password.
That hash cost about 0.2 s of CPU per new learner and held one api task to about 4 new learners a second.

| Arrivals per second | Before: launches/s | Before: p50 | Before: p95 | After: launches/s | After: p50 | After: p95 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 3.0 | 0.224 s | 0.239 s | 3.0 | 0.026 s | 0.032 s |
| 8 | 3.9 | 18.9 s | 25.9 s | | | |
| 10 | | | | 10.0 | 0.025 s | 0.029 s |
| 20 | | | | 20.0 | 0.023 s | 0.027 s |
| 40 | | | | 40.0 | 0.019 s | 0.024 s |
| 80 | | | | 52.8 | 1.46 s | 2.23 s |

The fix stores a marker that is not a bcrypt hash (`NO_PASSWORD` in `app/auth/security.py`), so no password matches it.
Sign-in checks such an account against the dummy hash, as it does an unknown email, so timing does not tell the two apart.
Test: `test_an_lms_account_has_no_password_to_sign_in_with`.

The "before" row at 8 a second comes from the first attempt (`lti-polluted.json`), the only run at that rate before the fix.

## Returning learners

| Arrivals per second | Launches/s | p50 | p95 | Failed |
| ---: | ---: | ---: | ---: | ---: |
| 20 | 20.0 | 0.021 s | 0.026 s | 0 |
| 40 | 40.0 | 0.017 s | 0.022 s | 0 |
| 80 | 61.1 | 0.82 s | 1.38 s | 0 |
| 160 | 44.1 | 2.83 s | 5.03 s | 0 |

One api task on one laptop core serves about 50 to 60 launches a second, first time or returning, with no failures.
Past that, launches wait instead of failing.

## Finding 2: a campus behind one address is cut off by the rate limits

The LTI steps are limited per address: login 60 a minute, launch 60 a minute, the code trade 30 a minute (`app/api/lti.py`).
120 learners from one address at 10 a second: 29 got in, 61 were refused at login and 30 at the code trade (HTTP 429).
A university that reaches the internet through one or a few addresses would see most of a class refused at the start of a lesson.

The same holds after sign-in, by calculation and not measured: `/auth/refresh` allows 30 a minute per address and an access token lasts 15 minutes, so one address can keep at most about 450 learners signed in.

The fix: the three LTI steps, `/auth/refresh` and `/auth/logout` now share one per address limit, `SHARED_ADDRESS_REQUESTS_PER_MINUTE`, 1000 a minute by default (`app/config.py`).
Password sign-in keeps 10 a minute per address, since that is where passwords are guessed.
Test: `test_a_campus_behind_one_address_keeps_its_sessions_until_the_shared_limit`.

After the fix, 300 learners from one address at 10 a second: all 300 got in, p50 0.026 s, p95 0.030 s.

## What it means for 3,000 to 6,000 online

Worst case taken: every online learner opens the tool within the same minute, from addresses of their own.

| Learners opening | Within | Launches/s needed | api tasks at 50/s on this laptop | api tasks at about 28/s on Fargate (estimate) |
| ---: | --- | ---: | ---: | ---: |
| 3,000 | 1 minute | 50 | 1 | 2 |
| 6,000 | 1 minute | 100 | 2 | 4 |
| 6,000 | 5 minutes | 20 | 1 | 1 |

The Fargate column scales the laptop figure by the ratio already measured for API reads: 220 requests a second here against 124 on one Fargate task (`2026-11-18-nfr.md`, `2026-11-19-aws.md`).
It is an estimate, not a measurement.
Production allows 2 to 12 api tasks (`infra/environments/production.tfvars`), so the launch itself is not the limit.
With the shared limit at 1000 a minute, one address can launch about 16 learners a second and keep about 15,000 sessions renewed every 15 minutes.

## Limits of this measurement

- Local, one api process at 1 CPU; not AWS, so no CloudFront, load balancer or network latency.
- The fake LMS stands in for Moodle: the load Moodle itself carries when thousands open its course pages is not measured.
- Launches only; the api tasks would serve chat and reads at the same time.
- Each step ran once.
