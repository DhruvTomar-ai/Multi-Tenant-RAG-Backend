"""Measures BGE similarity scores for answerable and unrelated questions, to choose RAG_MIN_SIMILARITY.

Scores use the production definition: 1 - cosine distance, with the BGE query prefix on questions.
The threshold is only a first filter that avoids calling the LLM on hopeless context, so missing a
real answer is worse than letting a weak match through, which the LLM can still decline.

    python -m benchmarks.relevance_threshold

No database is used. Re-run this with text from your own documents before trusting the value there.
"""
from __future__ import annotations

import json
import os
import statistics
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1:1/unused_test")
os.environ.setdefault("JWT_SECRET_KEY", "x" * 40)
os.environ.setdefault("JWT_REFRESH_SECRET_KEY", "y" * 40)

import numpy as np  # noqa: E402

from app.services.embeddings import model as embedding_model  # noqa: E402

PASSAGES = [
    "Employees are entitled to 24 days of paid annual leave each year. Up to five unused days may be carried forward to the next calendar year with manager approval.",
    "Sick leave must be reported to your manager before 10am. A doctor's certificate is required for absences longer than three consecutive working days.",
    "Parental leave is 16 weeks at full pay for the primary caregiver and 4 weeks for the secondary caregiver, available after one year of service.",
    "Business travel costs are reimbursed after an expense report is submitted with receipts within 30 days. Flights must be booked in economy class on trips under six hours.",
    "Corporate credit cards may only be used for approved business purchases. Purchases above 5,000 dollars require written approval from the finance director.",
    "All laptops must use full disk encryption. Report a suspected phishing email to the security team immediately using the report button in your mail client.",
    "Multi-factor authentication is mandatory for all company systems. Passwords must be at least 14 characters long and must not be reused across services.",
    "Deployments are rolled out to the Kubernetes cluster using Helm charts. Roll back a failed release with the helm rollback command and notify the on-call engineer.",
    "The on-call engineer must acknowledge a production incident within 15 minutes. Severity one incidents require a written postmortem within five working days.",
    "Customer data is retained for seven years after the end of a contract and then permanently deleted. Backups are encrypted and stored in two separate regions.",
    "New employees complete onboarding in their first two weeks, including security training and a meeting with their assigned mentor. Equipment is issued on the first day.",
    "Annual performance reviews take place every December. Salary adjustments are based on review outcomes and take effect from the first of February.",
    "The office cafeteria serves vegetarian and vegan lunch options from noon. Coffee and snacks are free in the kitchen on every floor of the building.",
    "Employees may work remotely up to three days per week with manager approval. Remote workers must use the company VPN when accessing internal systems.",
    "Vendor contracts above 50,000 dollars must be reviewed by the legal team. All vendors must sign a data processing agreement before receiving customer data.",
    "The company pension plan matches employee contributions up to six percent of salary. Employees become fully vested after three years of service.",
]
ANSWERABLE = [
    "How many days of annual leave do I get?",
    "Can employees carry forward unused vacation days?",
    "When do I need a doctor's note for being sick?",
    "How long is parental leave?",
    "How do I get reimbursed for a business trip?",
    "What class of flight can I book for work travel?",
    "Who needs to approve a large purchase on a company card?",
    "What should I do with a phishing email?",
    "How long do passwords need to be?",
    "Is MFA required?",
    "How do I undo a bad deployment?",
    "How quickly must on-call respond to an incident?",
    "When is a postmortem needed?",
    "How long do we keep customer data?",
    "What happens during the first two weeks at the company?",
    "When are performance reviews held?",
    "When do raises take effect?",
    "Where can I get lunch at the office?",
    "How many days can I work from home?",
    "Do I need a VPN when working remotely?",
    "Which contracts does legal need to review?",
    "What does a vendor have to sign before getting customer data?",
    "How much does the company match for retirement?",
    "When am I vested in the pension?",
]
UNRELATED = [
    "What is the capital of Australia?",
    "How do I bake sourdough bread?",
    "Who won the football world cup in 2018?",
    "What is the boiling point of water at high altitude?",
    "How far is the moon from the earth?",
    "Can you recommend a good science fiction novel?",
    "What is the best way to train for a marathon?",
    "How do bees make honey?",
    "What are the rules of chess?",
    "Which planet has the most moons?",
    "How do I repair a flat bicycle tire?",
    "What is photosynthesis?",
    "Who painted the Mona Lisa?",
    "How do you grow tomatoes at home?",
    "What is the tallest mountain in South America?",
    "How does a jet engine work?",
    "What should I feed a pet rabbit?",
    "What is the population of Canada?",
    "How do I tune a guitar?",
    "When did the Roman Empire fall?",
    "What is the stock price of Apple today?",
    "How long do you boil an egg?",
    "What are the symptoms of the common cold?",
    "How much does a new car cost in Germany?",
]


def top_scores(questions: list[str], passages: np.ndarray) -> list[float]:
    return [float(np.max(passages @ embedding_model.encode_query(question))) for question in questions]


def main() -> None:
    passages = embedding_model.encode_documents(PASSAGES)
    answerable = top_scores(ANSWERABLE, passages)
    unrelated = top_scores(UNRELATED, passages)
    rows = []
    for threshold in np.arange(0.30, 0.76, 0.05):
        kept = sum(score >= threshold for score in answerable)
        blocked = sum(score < threshold for score in unrelated)
        rows.append({
            "threshold": round(float(threshold), 2),
            "answerable_kept": f"{kept}/{len(answerable)}",
            "unrelated_blocked": f"{blocked}/{len(unrelated)}",
        })
    report = {
        "answerable": {"min": round(min(answerable), 3), "median": round(statistics.median(answerable), 3), "max": round(max(answerable), 3)},
        "unrelated": {"min": round(min(unrelated), 3), "median": round(statistics.median(unrelated), 3), "max": round(max(unrelated), 3)},
        "thresholds": rows,
        "lowest_answerable": sorted(zip((round(s, 3) for s in answerable), ANSWERABLE))[:4],
        "highest_unrelated": sorted(zip((round(s, 3) for s in unrelated), UNRELATED), reverse=True)[:4],
    }
    folder = Path(__file__).parent / "reports"
    folder.mkdir(exist_ok=True)
    (folder / "relevance_threshold.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
