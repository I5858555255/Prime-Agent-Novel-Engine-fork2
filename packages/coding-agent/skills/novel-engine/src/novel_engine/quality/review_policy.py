"""One literary review for clear cases; bounded consensus for borderline scores."""
import math
from novel_engine.quality.review_votes import aggregate
from novel_engine.quality.review_hybrid import is_valid_vote, dim_sum_mismatch


def review_on_demand(cached, review_once, grade, policy, config):
    margin = float(config.get("review_borderline_margin", 2.0))
    if not math.isfinite(margin) or margin < 0:
        raise ValueError("review_borderline_margin must be finite and nonnegative")
    votes = []

    def add(review):
        if ("raw_total" in review or "_llm_raw_total" in review) and (not is_valid_vote(review) or dim_sum_mismatch(review)):
            raise ValueError("review contains an invalid raw vote")
        score = float(review.get("normalized_score", review.get("total_score", 0)))
        if not math.isfinite(score) or not 0 < score <= 100:
            raise ValueError("review has no valid normalized score")
        votes.append((review, score))

    add(cached if cached is not None else review_once())
    boundaries = [float(policy["publication_line"]),
                  float(policy.get("soft_publication_line", policy["publication_line"]))]
    if cached is None and any(abs(votes[0][1] - line) <= margin for line in boundaries):
        add(review_once())
        add(review_once())
    stats = aggregate([score for _, score in votes])
    review, score = min(votes, key=lambda pair: abs(pair[1] - stats["median"]))
    review = dict(review, normalized_score=stats["median"])
    # Persist instability along with cached reviews; a cache hit must never erase it.
    unstable = bool(stats["highly_unstable"] or review.get("review_unstable"))
    review["review_unstable"] = unstable
    if unstable:
        issues = []
        for vote, _ in votes:
            for issue in vote.get("issues") or []:
                if issue not in issues:
                    issues.append(dict(issue))
        review["issues"] = issues
    review["review_votes"] = [s for _, s in votes] if cached is None else cached.get("review_votes", [score])
    return {"review": review, "score": stats["median"], "verdict": grade(review),
            "review_unstable": unstable}
