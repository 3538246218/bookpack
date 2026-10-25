"""Dependency-free title similarity and candidate ranking.

Scores measure spelling similarity, not the probability that two works are the same.
"""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher

CANDIDATE_THRESHOLD = 0.60
AUTO_THRESHOLD = 0.90
AUTO_MARGIN = 0.08
MAX_CANDIDATES = 8


def levenshtein(left, right):
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for index, character in enumerate(left, 1):
        current = [index]
        for offset, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, previous[offset] + 1,
                               previous[offset - 1] + (character != other)))
        previous = current
    return previous[-1]


class TitleMatcher:
    def __init__(self, normalize):
        self.normalize = normalize

    def match(self, query, title, aliases=()):
        wanted = self.normalize(query)
        if not wanted:
            return None
        best = None
        for index, name in enumerate((title, *aliases)):
            key = self.normalize(name)
            if not key:
                continue
            if wanted == key:
                return {"match_score": 1.0, "match_kind": "exact" if index == 0 else "alias",
                        "matched_on": str(name), "edit_distance": 0}
            # Two common characters are insufficient evidence for a fuzzy title.
            if min(len(wanted), len(key)) < 3:
                continue
            distance = levenshtein(wanted, key)
            edit_ratio = 1 - distance / max(len(wanted), len(key))
            sequence_ratio = SequenceMatcher(None, wanted, key, autojunk=False).ratio()
            score = 0.6 * sequence_ratio + 0.4 * edit_ratio
            if score >= CANDIDATE_THRESHOLD and (best is None or score > best["match_score"]):
                best = {"match_score": round(score, 4), "match_kind": "fuzzy",
                        "matched_on": str(name), "edit_distance": distance}
        return best

    def suggest(self, query, title, aliases=()):
        """Score completion preference separately from download-match confidence."""
        wanted = self.normalize(query)
        if len(wanted) < 2:
            return None
        choices = []
        priority = {"exact": 0, "prefix": 1, "contains": 2, "fuzzy": 3}
        for name in (title, *aliases):
            key = self.normalize(name)
            if not key:
                continue
            if wanted == key:
                kind, score = "exact", 1.0
            elif key.startswith(wanted):
                # Within prefix completions, prefer a longer continuation.
                kind, score = "prefix", 1 - len(wanted) / len(key)
            elif len(wanted) >= 3 and wanted in key:
                kind, score = "contains", len(wanted) / len(key)
            else:
                match = self.match(query, name)
                if not match:
                    continue
                kind, score = "fuzzy", match["match_score"]
            choices.append({"suggestion_kind": kind, "suggestion_score": round(score, 4), "matched_on": str(name)})
        return min(choices, key=lambda row: (priority[row["suggestion_kind"]],
                   -len(self.normalize(row["matched_on"])) if row["suggestion_kind"] == "prefix" else 0,
                   -row["suggestion_score"])) if choices else None

    def rank(self, candidates):
        # Keep one usable source per title and author so mirrors do not create ties.
        ordered = sorted(candidates, key=lambda row: (
            -row["match_score"], row.get("download_kind") == "online_only"))
        result, seen = [], set()
        for candidate in ordered:
            identity = (self.normalize(candidate["matched_title"]),
                        self.normalize(candidate.get("matched_author", "")))
            if identity in seen:
                continue
            seen.add(identity)
            key = "\0".join((*identity, candidate.get("source_id", ""),
                             candidate.get("local_path", ""), candidate.get("source_page") or candidate.get("url", "")))
            result.append({**candidate, "candidate_id": hashlib.sha256(key.encode()).hexdigest()[:16]})
        return result[:MAX_CANDIDATES]

    def candidate(self, query, title, aliases=()):
        match = self.match(query, title, aliases)
        if match and match["match_kind"] in {"exact", "alias"}:
            return match
        suggestion = self.suggest(query, title, aliases)
        if suggestion and suggestion["suggestion_kind"] == "prefix":
            return {"match_kind": "prefix", "match_score": suggestion["suggestion_score"],
                    "matched_on": suggestion["matched_on"],
                    "edit_distance": len(self.normalize(suggestion["matched_on"])) - len(self.normalize(query))}
        return match

    def automatic(self, query, candidates, author=""):
        if not candidates:
            return None
        first = candidates[0]
        if first["match_kind"] in {"prefix", "same_title"}:
            return None
        if first.get("download_kind") == "online_only":
            return None
        if author and self.normalize(author) != self.normalize(first.get("matched_author", "")):
            return None
        if len(candidates) > 1 and first["match_score"] - candidates[1]["match_score"] < AUTO_MARGIN:
            return None
        if first["match_kind"] in {"exact", "alias"}:
            return first
        wanted, found = self.normalize(query), self.normalize(first["matched_on"])
        series = r"(?:第[零〇一二三四五六七八九十百千万两0-9]+[部卷册季]|[0-9]+)$"
        wanted_series, found_series = re.search(series, wanted), re.search(series, found)
        if (wanted_series or found_series) and (wanted_series[0] if wanted_series else "") != (found_series[0] if found_series else ""):
            return None
        # Short titles and possible sequels/subtitles stay in the selectable list.
        if (first["match_score"] >= AUTO_THRESHOLD and min(len(wanted), len(found)) >= 6
                and wanted not in found and found not in wanted):
            return first
        return None

    def queries(self, title):
        key = self.normalize(title)
        if len(key) < 4:
            return []
        size = (len(key) + 1) // 2
        return list(dict.fromkeys((key[:size], key[-size:])))
