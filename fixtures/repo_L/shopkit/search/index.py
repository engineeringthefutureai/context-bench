from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from shopkit.catalog.models import Product

STOPWORDS = {"a", "an", "and", "the", "for", "of", "with", "in", "to"}
SYNONYMS = {"mug": {"cup"}, "cup": {"mug"}, "tee": {"tshirt", "t-shirt"}, "tshirt": {"tee"}, "sofa": {"couch"},
            "couch": {"sofa"}, "kettle": {"teakettle"}}
FIELD_WEIGHTS = {"title": 3.0, "category": 1.5, "tags": 1.0}


def tokens(text: str) -> list[str]:
    """Lower-case ASCII word tokens, accents folded ('Café' -> 'cafe'), stopwords dropped, a crude
    plural strip ('mugs' -> 'mug', but not 'glass' -> 'glas')."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    out = []
    for w in re.findall(r"[a-z0-9]+", text):
        if w in STOPWORDS:
            continue
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


@dataclass
class Doc:
    sku: str
    fields: dict[str, list[str]]
    popularity: float = 0.0
    active: bool = True


@dataclass
class SearchIndex:
    docs: dict[str, Doc] = field(default_factory=dict)
    postings: dict[str, dict[str, Counter]] = field(default_factory=lambda: defaultdict(dict))   # term -> sku -> field counts

    def add(self, product: Product, *, category: str = "", tags: list[str] | None = None, popularity: float = 0.0) -> None:
        self.remove(product.sku)
        doc = Doc(product.sku, {"title": tokens(product.title), "category": tokens(category),
                                "tags": [t for tag in (tags or []) for t in tokens(tag)]},
                  popularity, product.active)
        self.docs[product.sku] = doc
        for fname, toks in doc.fields.items():
            for t in toks:
                self.postings[t].setdefault(product.sku, Counter())[fname] += 1

    def remove(self, sku: str) -> None:
        doc = self.docs.pop(sku, None)
        if doc is None:
            return
        for toks in doc.fields.values():
            for t in set(toks):
                self.postings.get(t, {}).pop(sku, None)

    def _idf(self, term: str) -> float:
        n = len(self.postings.get(term, {}))
        return math.log(1 + len(self.docs) / (1 + n))

    def search(self, query: str, limit: int = 20, *, include_inactive: bool = False) -> list[tuple[str, float]]:
        """(sku, score) best first. Every query term (or a synonym) must match; the last term also
        matches as a prefix so "blue mu" finds "blue mug". Popularity breaks near-ties."""
        terms = tokens(query)
        if not terms:
            return []
        candidate_sets = []
        expanded: list[set[str]] = []
        for i, term in enumerate(terms):
            alts = {term} | SYNONYMS.get(term, set())
            if i == len(terms) - 1:
                alts |= {t for t in self.postings if t.startswith(term) and len(term) >= 2}
            expanded.append(alts)
            skus = set()
            for a in alts:
                skus |= set(self.postings.get(a, {}))
            candidate_sets.append(skus)
        matches = set.intersection(*candidate_sets) if candidate_sets else set()
        scored = []
        for sku in matches:
            doc = self.docs[sku]
            if not doc.active and not include_inactive:
                continue
            score = 0.0
            for alts in expanded:
                best = 0.0
                for a in alts:
                    counts = self.postings.get(a, {}).get(sku)
                    if counts:
                        exact = 1.0 if a in terms else 0.7
                        best = max(best, exact * self._idf(a) * sum(FIELD_WEIGHTS[f] * c for f, c in counts.items()))
                score += best
            score += 0.1 * math.log1p(doc.popularity)
            scored.append((sku, round(score, 4)))
        return sorted(scored, key=lambda x: (-x[1], x[0]))[:limit]

    def suggest(self, prefix: str, limit: int = 8) -> list[str]:
        """Search-box completions: index terms starting with the prefix, most frequent first."""
        p = (tokens(prefix) or [""])[-1]
        if len(p) < 2:
            return []
        terms = [(t, len(s)) for t, s in self.postings.items() if t.startswith(p) and s]
        return [t for t, _ in sorted(terms, key=lambda x: (-x[1], x[0]))[:limit]]
