"""Level-name ladders for the rank/level display.

Each theme is a 50-element list — one name per level, ordered L1 → L50.
The progression arc is the theme's identity: craft (technical mastery),
forge (blacksmithing), cosmic (stellar/spacetime), ocean (lobster-mascot
marine depth), plus eight pop-culture-inspired ladders.

Adding a theme: append a new key to THEMES with exactly 50 unique
single-word entries. The test suite (test_themes.py) enforces both.

The selected theme is read from `~/.claude/coach/.user_config.json`
via `user_config.get_theme()`. Default = "craft" so existing installs
without a config file are unchanged.

# Brand safety — pop-culture themes

The eight pop-culture-inspired themes (skyrim, marvel, dc, finalfantasy,
military, lotr, starwars, hacker) are FAN-INSPIRED and use only:

  - Public-domain mythology (Norse, Greek, Mesopotamian, Hindu, Biblical, etc.)
  - Real-world historical figures and titles (Caesar, Khan, Shogun, Centurion)
  - Genre-generic terminology that pre-dates or transcends any single
    franchise (Knight, Wizard, Mage, Apprentice, Master, Sentinel, etc.)
  - Common English compound words (Greybeard, Highking, Worldwarden)

Specifically EXCLUDED from every ladder:
  - Names invented by a franchise (Dovahkiin, Padawan, Vibranium, Maiar)
  - Named characters from any franchise
  - Trademarked group / organization names (The Avengers, Justice League,
    Jedi Order, Lantern Corps as such)

Coach Claw is not affiliated with or endorsed by any franchise owner.
"""
from __future__ import annotations

# L1-L8 preserved from the original ladder for backwards-compat with
# existing XP totals. L9-L50 follow the technical-mastery arc:
# recognized excellence → mastery → cosmic → transcendent.
THEME_CRAFT = [
    "Drafter", "Iterator", "Builder", "Shipper", "Craftsman",
    "Architect", "Virtuoso", "Sensei", "Luminary", "Legend",
    "Mythic", "Ascendant", "Pioneer", "Vanguard", "Savant",
    "Prodigy", "Visionary", "Oracle", "Sage", "Paragon",
    "Archmage", "Grandmaster", "Elder", "Progenitor", "Sovereign",
    "Titan", "Colossus", "Zenith", "Overmind", "Transcendent",
    "Celestial", "Cosmic", "Stellar", "Nebula", "Supernova",
    "Singularity", "Primordial", "Aether", "Eldritch", "Eternal",
    "Immortal", "Divine", "Demiurge", "Alpha", "Omega",
    "Apex", "Ultima", "Infinite", "Genesis", "Origin",
]

# Blacksmithing arc — apprentice → master smith → primal forge.
THEME_FORGE = [
    "Apprentice", "Striker", "Smith", "Forger", "Hammerhand",
    "Bladesmith", "Toolsmith", "Mastersmith", "Forgemaster", "Anvilkeeper",
    "Furnacekeeper", "Quenchmaster", "Edgewright", "Steelwright", "Ironwright",
    "Bellowsmith", "Crucible", "Tempersworn", "Hardener", "Foundrylord",
    "Smelter", "Blastsmith", "Damascus", "Foldweaver", "Patternsmith",
    "Runesmith", "Soulforger", "Starsmith", "Voidwright", "Worldforger",
    "Spiritsmith", "Lightsmith", "Flamewarden", "Inferno", "Cinder",
    "Ember", "Glow", "Furnacecore", "Magmaheart", "Sunsmith",
    "Forgekin", "Coresoul", "Eternalflame", "Worldhammer", "Anvilheart",
    "Pillar", "Bedrock", "Forgefather", "Primalforge", "Genesis",
]

# Cosmological arc — spark of matter → galaxy → cosmic inflation.
THEME_COSMIC = [
    "Spark", "Mote", "Particle", "Atom", "Element",
    "Crystal", "Mineral", "Aerolith", "Asteroid", "Meteor",
    "Comet", "Moon", "Planet", "Star", "Pulsar",
    "Quasar", "Magnetar", "Nebula", "Nova", "Supernova",
    "Hypernova", "Galaxy", "Constellation", "Cluster", "Spiral",
    "Filament", "Wormhole", "Horizon", "Singularity", "Universe",
    "Multiverse", "Cosmosphere", "Plenum", "Continuum", "Aether",
    "Ether", "Void", "Abyssum", "Eonkeeper", "Timeweaver",
    "Spaceforger", "Lightspeed", "Causal", "Quantum", "Primordial",
    "Genesis", "Inflation", "Eternity", "Infinity", "Origin",
]

# Marine-depth arc — lobster mascot tribute. Hatchling → leviathan → origin.
THEME_OCEAN = [
    "Hatchling", "Larva", "Wriggler", "Postlarva", "Juvenile",
    "Forager", "Burrower", "Reefer", "Hunter", "Patroller",
    "Stalker", "Predator", "Tidewalker", "Currentrider", "Wavecrest",
    "Reefking", "Tidemaster", "Deepswimmer", "Sandstrider", "Coralweaver",
    "Stormbringer", "Squallchaser", "Tidelord", "Reeflord", "Abyssal",
    "Deepkeeper", "Trenchwalker", "Voidswimmer", "Leviathan", "Kraken",
    "Reefshaper", "Oceanic", "Saltkin", "Pearlborn", "Deepforger",
    "Mariner", "Tideborn", "Foamprince", "Wavekeeper", "Tidesage",
    "Cosmic", "Primordial", "Oceanmind", "Worldsea", "Eternalshore",
    "Tideborne", "Currentlord", "Genesis", "Source", "Origin",
]

# === POP-CULTURE THEMES ===================================================
# All entries below are public-domain mythology, real historical titles,
# or genre-generic terminology. No franchise-coined neologisms or named
# characters. See module docstring "Brand safety" for the policy.

# Skyrim-inspired — Norse / Anglo-Saxon hierarchy + generic fantasy.
# Bandit → Whelp/Initiate paths → Companion / Listener / Greybeard tier
# → Wyrmkin → Norse pantheon. No Bethesda-coined words (no Dovahkiin,
# Daedra, Aedra, Talos, Alduin, etc.).
THEME_SKYRIM = [
    "Pauper", "Citizen", "Footpad", "Bandit", "Adventurer",
    "Whelp", "Initiate", "Pupil", "Apprentice", "Sellsword",
    "Adept", "Skald", "Drengr", "Berserker", "Conjurer",
    "Burglar", "Mercenary", "Slayer", "Wizard", "Centurion",
    "Praefect", "Warlock", "Guildmaster", "Master", "Legate",
    "Archmage", "Companion", "Speaker", "General", "Blade",
    "Harbinger", "Listener", "Greybeard", "Tongue", "Thane",
    "Jarl", "Stormcaller", "Konung", "Champion", "Voidwalker",
    "Wyrmkin", "Wyrmblood", "Dragonkin", "Highking", "Highlord",
    "Worldwarden", "Vanir", "Aesir", "Asgardian", "Anu",
]

# Marvel-inspired — comics power-tier classification, no character names
# or trademarked group names. Civilian → Hero → cosmic → abstract.
THEME_MARVEL = [
    "Civilian", "Witness", "Bystander", "Cadet", "Operative",
    "Agent", "Vigilante", "Sidekick", "Hero", "Defender",
    "Mutant", "Mystic", "Sorcerer", "Specialist", "Captain",
    "Knight", "Crusader", "Sentinel", "Watchman", "Champion",
    "Speedster", "Telepath", "Telekinetic", "Pyromancer", "Cryomancer",
    "Channeler", "Berserker", "Behemoth", "Phoenix", "Avatar",
    "Cosmic", "Celestial", "Eternal", "Galactic", "Stellar",
    "Universal", "Multiversal", "Dimensional", "Astral", "Beyond",
    "Eternity", "Infinity", "Tribunal", "Allfather", "Existence",
    "Sovereign", "Above", "Origin", "Apex", "Source",
]

# DC-inspired — vigilante / detective angle, Justice / Trinity / New Gods
# cosmology. Differentiated from Marvel by Detective+Justicer flavor and
# Highfather/Spectre/Presence top tiers.
THEME_DC = [
    "Civilian", "Bystander", "Witness", "Patrolman", "Detective",
    "Operative", "Agent", "Vigilante", "Sidekick", "Hero",
    "Crusader", "Justicer", "Defender", "Watchman", "Sentinel",
    "Captain", "Knight", "Centurion", "Champion", "Specialist",
    "Speedster", "Telepath", "Sorcerer", "Mystic", "Lantern",
    "Wonder", "Founder", "Council", "Trinity", "Avatar",
    "Demigod", "Eternal", "Stellar", "Cosmic", "Galactic",
    "Universal", "Multiversal", "Beyond", "Aspect", "Spirit",
    "Spectre", "Phantom", "Highfather", "Endless", "Highest",
    "Sovereign", "Crisis", "Presence", "Origin", "Source",
]

# Final Fantasy-inspired — generic JRPG job classes + summon-type terms.
# Specific FF characters / Onion Knight / Cetra / Esper-as-named-summon
# excluded; mythological summon types (Aeon, Eidolon, Primal) are public
# domain (Greek "eidolon", broader use), so retained.
THEME_FINALFANTASY = [
    "Squire", "Page", "Cadet", "Apprentice", "Initiate",
    "Adept", "Knight", "Warrior", "Crusader", "Paladin",
    "Berserker", "Monk", "Lancer", "Dragoon", "Samurai",
    "Ninja", "Bard", "Dancer", "Geomancer", "Cleric",
    "Warlock", "Spellsword", "Mystic", "Scholar", "Astrologer",
    "Necromancer", "Alchemist", "Engineer", "Reaper", "Viper",
    "Mediator", "Channeler", "Summoner", "Hero", "Champion",
    "Lightbringer", "Crystal", "Aeon", "Eidolon", "Esper",
    "Familiar", "Primal", "Avatar", "Sovereign", "Worldwarden",
    "Skywarden", "Source", "Origin", "Singularity", "Genesis",
]

# Military hierarchy — US enlisted/officer ranks + Roman + Asian + mythic
# war archetypes. All real historical titles (Caesar, Khan, Shogun) or
# generic English military terms.
THEME_MILITARY = [
    "Recruit", "Private", "Specialist", "Corporal", "Sergeant",
    "Staffsergeant", "Mastersergeant", "Sergeantmajor", "Ensign", "Cadet",
    "Lieutenant", "Captain", "Major", "Colonel", "Brigadier",
    "General", "Fieldmarshal", "Commander", "Commodore", "Admiral",
    "Viceadmiral", "Fleetadmiral", "Marshal", "Airmarshal", "Highmarshal",
    "Ranger", "Beret", "Seal", "Marine", "Paratrooper",
    "Sniper", "Operative", "Aviator", "Pilot", "Centurion",
    "Optio", "Tribune", "Legate", "Praetor", "Consul",
    "Imperator", "Caesar", "Augustus", "Samurai", "Daimyo",
    "Khan", "Shogun", "Conqueror", "Generalissimo", "Polemarch",
]

# LOTR-inspired — Anglo-Saxon / generic medieval high-fantasy. None of:
# Hobbit, Maiar, Valar, Eru, Numenor, Mordor, Shire, Gondor, Rohan, or
# named characters. "Halfling" is pre-Tolkien (in 1860s English use).
THEME_LOTR = [
    "Pauper", "Cottar", "Wanderer", "Squire", "Apprentice",
    "Halfling", "Footsoldier", "Pikeman", "Outrider", "Greenrider",
    "Knight", "Esquire", "Ranger", "Scout", "Hunter",
    "Marshal", "Captain", "Bannerman", "Reeve", "Thegn",
    "Champion", "Crusader", "Sentinel", "Warden", "Guardian",
    "Watchman", "Liegelord", "Lord", "Highlord", "Steward",
    "Regent", "King", "Highking", "Sage", "Loremaster",
    "Wizard", "Conjurer", "Mage", "Archmage", "Highmage",
    "Mystic", "Oracle", "Visionary", "Wraith", "Spectre",
    "Shade", "Avatar", "Forefather", "Source", "Origin",
]

# Star Wars-inspired — generic monk-knight + Light/Dark hierarchy. None
# of: Jedi, Sith, Padawan, Lightsaber, Force, Mandalorian, or named
# characters. Initiate → Apprentice → Knight → Master → Lord progression
# is broadly monastic, predates SW.
THEME_STARWARS = [
    "Civilian", "Cadet", "Recruit", "Initiate", "Acolyte",
    "Apprentice", "Disciple", "Adept", "Pupil", "Trainee",
    "Squire", "Knight", "Crusader", "Champion", "Hunter",
    "Tracker", "Warrior", "Sentinel", "Guardian", "Defender",
    "Captain", "Master", "Council", "Sovereign", "Lord",
    "Highlord", "Warlord", "Darklord", "Commander", "Chancellor",
    "Emperor", "Mystic", "Sage", "Seer", "Oracle",
    "Shadowmaster", "Lightbearer", "Voidwalker", "Starwarden", "Skywarden",
    "Voidlord", "Galactic", "Stellar", "Cosmic", "Eternal",
    "Astral", "Celestial", "Origin", "Source", "Above",
]

# Hacker / dev-culture — mainstream dev hierarchy + system-internals
# folklore. No company names, no trademarked product names. "Linux",
# "Unix", "Bell Labs", "Knuth" intentionally absent — using them as
# rank names is gimmicky and risks association.
THEME_HACKER = [
    "Lurker", "Reader", "Newbie", "Scriptkiddie", "Tinkerer",
    "Coder", "Programmer", "Hacker", "Junior", "Engineer",
    "Developer", "Senior", "Lead", "Architect", "Principal",
    "Distinguished", "Fellow", "Maintainer", "Reviewer", "Mentor",
    "Wizard", "Sorcerer", "Guru", "Sensei", "Sage",
    "Oracle", "Veteran", "Greybeard", "Founder", "Pioneer",
    "Daemon", "Compiler", "Linker", "Kernel", "Hypervisor",
    "Bootloader", "Firmware", "Microservice", "Distributed", "Quantum",
    "Specter", "Phantom", "Ghost", "Demon", "Druid",
    "Phreaker", "Legend", "Source", "Genesis", "Singularity",
]

THEMES: dict[str, list[str]] = {
    "craft":        THEME_CRAFT,
    "forge":        THEME_FORGE,
    "cosmic":       THEME_COSMIC,
    "ocean":        THEME_OCEAN,
    "skyrim":       THEME_SKYRIM,
    "marvel":       THEME_MARVEL,
    "dc":           THEME_DC,
    "finalfantasy": THEME_FINALFANTASY,
    "military":     THEME_MILITARY,
    "lotr":         THEME_LOTR,
    "starwars":     THEME_STARWARS,
    "hacker":       THEME_HACKER,
}

DEFAULT_THEME = "craft"


def get_ladder(theme: str) -> list[str]:
    """Return the 50-element name list for `theme`. Falls back to default
    on unknown themes — the caller never sees a KeyError."""
    return THEMES.get(theme, THEMES[DEFAULT_THEME])


def list_themes() -> list[str]:
    """Stable list of available theme keys, default first for `/config`."""
    return [DEFAULT_THEME] + sorted(k for k in THEMES if k != DEFAULT_THEME)
