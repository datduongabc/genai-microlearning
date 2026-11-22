import { describe, expect, it } from "vitest";
import { ratingSchema, emptyRating, quizQualityScore, raterEmailsSchema } from "../../../lib/ratingSchema";

describe("blind rubric validation", () => {
  it("requires all nine integer scores and preserves findings and overall notes", () => {
    const body = emptyRating("M01");
    expect(ratingSchema.safeParse(body).success).toBe(false);
    for (const key of [
      "accuracy",
      "completeness",
      "consistency",
      "hallucination_score",
      "slide_quality",
      "quiz_quality",
      "bloom_fidelity",
      "query_alignment",
      "examples_feedback",
    ] as const) {
      body[key] = { score: 4, justification: "Có căn cứ" };
    }
    body.hallucination_findings = [
      { claim: "Một câu", field: "content", reason: "Không có trong nguồn" },
    ];
    body.overall_notes = "Ghi chú";
    expect(ratingSchema.parse(body)).toEqual(body);
    for (const score of [0, 6, 2.5, NaN])
      expect(
        ratingSchema.safeParse({ ...body, accuracy: { score, justification: "x" } }).success,
      ).toBe(false);
  });
  it("scores the quiz like the server: the share of questions with at most one flaw", () => {
    const flawed = (n: number) => Array.from({ length: 6 }, (_, i) =>
      ({ question: i + 1, flaws: i < n ? ["vague_terms", "lost_sequence"] : ["vague_terms"] }));
    // The same values backend/app/generation/evaluation.py gives for six questions.
    expect([0, 1, 2, 3, 4, 5, 6].map((n) => quizQualityScore(flawed(n)))).toEqual([5, 4, 4, 3, 2, 2, 1]);
    expect(quizQualityScore([{ question: 1, flaws: ["vague_terms", "vague_terms"] }])).toBe(5);
  });
  it("accepts an empty invitation list and rejects malformed emails", () => {
    expect(raterEmailsSchema.parse({ emails: [] })).toEqual({ emails: [] });
    expect(raterEmailsSchema.safeParse({ emails: ["bad"] }).success).toBe(false);
  });
});
