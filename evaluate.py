"""
Retrieval evaluation (BRD section 3.1 / 13.2).

For each test question we list URL fragments of pages that contain the answer.
A question is a "hit" if any of the top-5 retrieved sources matches.
Metrics: Hit@5 (target >= 80%) and MRR (mean reciprocal rank of first relevant source).

Usage:  python evaluate.py
"""
from rag import RAG

TEST_SET = [
    ("How do I apply to study at DIT?", ["/apply", "application"]),
    ("Which programmes are taught in English?", ["studying-in-english", "english", "study-programmes"]),
    ("What do international students need to know about visas and health insurance?",
     ["practical-information", "int-students", "visa", "insurance"]),
    ("Tell me about the Campus Cham", ["cham"]),
    ("How can I contact the Welcome Centre?", ["welcome"]),
    ("What options are there for refugees?", ["refugee"]),
    ("Where can I find information about exchange semesters?", ["exchange"]),
    ("What postgraduate master's courses are offered?", ["postgraduate", "master"]),
    ("How do I get to the Deggendorf campus?", ["directions", "deggendorf"]),
    ("Are there job vacancies at the university?", ["jobs"]),
    ("What support is there for starting a business?", ["business", "entrepreneur", "startup"]),
    ("What is the European Campus Rottal-Inn?", ["ecri", "rottal"]),
    ("How does DIT handle sustainability?", ["sustainab"]),
    ("Information about the alumni network", ["alumni"]),
    ("What study support services are available?", ["study-support", "support"]),
]


def main():
    rag = RAG()
    hits, rr = 0, 0.0
    for q, expected in TEST_SET:
        res = rag.retrieve(q, k=5)
        rank = next((i + 1 for i, r in enumerate(res)
                     if any(e in r["url"].lower() for e in expected)), None)
        hits += rank is not None
        rr += 1 / rank if rank else 0
        top = res[0]["url"] if res else "-"
        print(f"{'HIT ' if rank else 'MISS'} rank={rank or '-':<2} {q[:55]:<55} top: {top}")
    n = len(TEST_SET)
    print(f"\nHit@5 = {hits / n:.0%}   MRR = {rr / n:.2f}   ({n} questions)")


if __name__ == "__main__":
    main()
