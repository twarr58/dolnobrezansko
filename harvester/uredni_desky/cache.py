"""Mezipaměť detailů položek z adaptérů (RSS/HTML).

Hodinový sběr stovek obecních webů by jinak pokaždé stahoval detail každého oznámení.
Detail se proto stahuje jen u položek, které v předchozím stavu nejsou (nebo u nich chybí přílohy).
"""

from __future__ import annotations

DETAILS: dict[str, dict] = {}

# Rozpočet stažení detailů na jeden běh sběru. Při prvním průchodu stovek nových desek by jinak
# sběr stahoval desítky tisíc stránek a nevešel se do limitu; zbytek se doplní v dalších bězích.
DETAIL_BUDGET = 6000
_used = 0


def take_detail_slot() -> bool:
    global _used
    if _used >= DETAIL_BUDGET:
        return False
    _used += 1
    return True


def reset_budget(budget: int = DETAIL_BUDGET) -> None:
    global _used, DETAIL_BUDGET
    _used, DETAIL_BUDGET = 0, budget


def load_from_state(items: list[dict]) -> None:
    DETAILS.clear()
    for it in items:
        url = it.get("url")
        if url and not it.get("removed") and (it.get("docs") or it.get("until")):
            DETAILS[url] = {k: it[k] for k in ("docs", "until", "cj") if it.get(k)}


def apply(info: dict) -> bool:
    """Doplní do položky (tvar OFN) přílohy/termín z mezipaměti. Vrátí True, pokud byla v mezipaměti."""
    cached = DETAILS.get(info.get("url", ""))
    if not cached:
        return False
    if cached.get("docs"):
        info["dokument"] = [{"název": {"cs": d.get("name") or ""}, "url": d["url"]} for d in cached["docs"]]
    if cached.get("until") and "relevantní_do" not in info:
        info["relevantní_do"] = {"datum": cached["until"]}
    if cached.get("cj") and "číslo_jednací" not in info:
        info["číslo_jednací"] = cached["cj"]
    return True
