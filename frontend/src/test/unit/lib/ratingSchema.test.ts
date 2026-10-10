import { describe, expect, it } from "vitest";
import { ratingSchema, emptyRating, raterEmailsSchema } from "../../../lib/ratingSchema";

describe("blind rubric validation", () => {
  it("requires all eight integer scores and preserves findings and overall notes", () => {
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
  it("accepts an empty invitation list and rejects malformed emails", () => {
    expect(raterEmailsSchema.parse({ emails: [] })).toEqual({ emails: [] });
    expect(raterEmailsSchema.safeParse({ emails: ["bad"] }).success).toBe(false);
  });
});
