"""League catalog and resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass

from .utils import slugify


@dataclass(frozen=True, slots=True)
class LeagueDefinition:
    index: int
    tournament_id: int
    name: str

    @property
    def slug(self) -> str:
        return slugify(self.name)


LEAGUES: tuple[LeagueDefinition, ...] = (
    LeagueDefinition(1, 17, "Premier League"),
    LeagueDefinition(2, 8, "La Liga"),
    LeagueDefinition(3, 23, "Serie A"),
    LeagueDefinition(4, 35, "Bundesliga"),
    LeagueDefinition(5, 34, "Ligue 1"),
    LeagueDefinition(6, 37, "Eredivisie"),
    LeagueDefinition(7, 242, "MLS"),
    LeagueDefinition(8, 325, "Brasileirao Serie A"),
    LeagueDefinition(9, 155, "Liga Profesional de Futbol"),
    LeagueDefinition(10, 54, "La Liga 2"),
    LeagueDefinition(11, 18, "Championship"),
    LeagueDefinition(12, 24, "League One"),
    LeagueDefinition(13, 44, "2. Bundesliga"),
    LeagueDefinition(14, 53, "Serie B"),
    LeagueDefinition(15, 390, "Brasileirao Serie B"),
    LeagueDefinition(16, 703, "Primera Nacional"),
    LeagueDefinition(17, 203, "Russian Premier League"),
    LeagueDefinition(18, 1127, "Liga F"),
    LeagueDefinition(19, 23608, "Serie B Femminile"),
    LeagueDefinition(20, 2288, "2. Frauen-Bundesliga"),
    LeagueDefinition(21, 13363, "USL Championship"),
    LeagueDefinition(22, 18641, "MLS Next Pro"),
)


LEAGUES_BY_INDEX = {league.index: league for league in LEAGUES}
LEAGUES_BY_ID = {league.tournament_id: league for league in LEAGUES}
LEAGUES_BY_SLUG = {league.slug: league for league in LEAGUES}


def list_leagues() -> list[LeagueDefinition]:
    """Return all built-in league definitions."""
    return list(LEAGUES)


def resolve_league_identifier(identifier: str | int | None) -> LeagueDefinition | None:
    """Resolve a built-in league from an index, id or slug."""
    if identifier is None:
        return None

    if isinstance(identifier, int):
        return LEAGUES_BY_INDEX.get(identifier) or LEAGUES_BY_ID.get(identifier)

    text = str(identifier).strip()
    if not text:
        return None

    if text.isdigit():
        numeric = int(text)
        return LEAGUES_BY_INDEX.get(numeric) or LEAGUES_BY_ID.get(numeric)

    return LEAGUES_BY_SLUG.get(slugify(text))
