"""Mezipaměť detailů položek z adaptérů (RSS/HTML).

Hodinový sběr stovek obecních webů by jinak pokaždé stahoval detail každého oznámení.
Detail se proto stahuje jen u položek, které v předchozím stavu nejsou (nebo u nich chybí přílohy).
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

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
    live = [it for it in items if it.get("url") and not it.get("removed")]
    # položky, jejichž přílohy má zároveň jiná položka téže desky, se načtou znovu (detail mohl být chybně
    # stažen jako stránka s cizími přílohami – v mezipaměti by chyba zůstala navždy)
    seen: dict[tuple, set[str]] = {}
    for it in live:
        for d in it.get("docs") or []:
            seen.setdefault((it.get("board"), d.get("url")), set()).add(it["url"])
    for it in live:
        if any(len(seen[(it.get("board"), d.get("url"))]) > 1 for d in it.get("docs") or []):
            continue
        if it.get("docs") or it.get("until"):
            DETAILS[it["url"]] = {k: it[k] for k in ("docs", "until", "cj") if it.get(k)}


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


def drop_shared_docs(infos: list[dict], board: str = "") -> int:
    """Pojistka: soubor, který je u víc položek desky, patří k té s nejmenším počtem příloh (typicky vlastní
    oznámení); u položky s víc přílohami je to zavlečený seznam z cizí stránky. Vrátí počet odebraných příloh."""
    owners: dict[str, list[dict]] = {}
    for info in infos:
        for d in info.get("dokument") or []:
            owners.setdefault(d["url"], []).append(info)
    removed = 0
    for url, its in owners.items():
        if len({id(i) for i in its}) < 2:
            continue
        keep = min(len(i["dokument"]) for i in its)
        for info in its:
            if len(info["dokument"]) > keep:
                info["dokument"] = [d for d in info["dokument"] if d["url"] != url]
                removed += 1
    if removed:
        log.warning("%s: %d příloh bylo u víc oznámení zároveň – odebrány u oznámení s cizími přílohami", board, removed)
    return removed
