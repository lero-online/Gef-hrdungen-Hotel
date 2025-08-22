import json
from dataclasses import dataclass, asdict, field
from datetime import date, datetime
from typing import List, Optional, Dict, Any
from dateutil.relativedelta import relativedelta
from io import BytesIO

import pandas as pd
import streamlit as st

# =========================
# Datenmodelle
# =========================

STOP_LEVELS = ["S (Substitution/Quelle entfernen)", "T (Technisch)", "O (Organisatorisch)", "P (PSA)", "Q (Qualifikation/Unterweisung)"]
STATUS_LIST = ["offen", "in Umsetzung", "wirksam", "nicht wirksam", "entfallen"]

RISK_COLOR = {
    "sehr hoch": "#8b0000",
    "hoch": "#d9534f",
    "mittel": "#f0ad4e",
    "niedrig": "#5cb85c"
}

@dataclass
class Measure:
    title: str
    stop_level: str
    responsible: str = ""
    due_date: Optional[str] = None  # ISO
    status: str = "offen"
    notes: str = ""

@dataclass
class Hazard:
    id: str
    area: str                # Arbeitsbereich
    activity: str            # Tätigkeit
    hazard: str              # Gefährdungsbeschreibung
    sources: List[str]       # Quellen/Einwirkungen
    existing_controls: List[str]  # bereits vorhandene Maßnahmen
    prob: int = 3            # Eintrittswahrscheinlichkeit 1..5
    sev: int = 3             # Schadensschwere 1..5
    risk_value: int = 9
    risk_level: str = "mittel"
    additional_measures: List[Measure] = field(default_factory=list)
    last_review: Optional[str] = None
    reviewer: str = ""
    documentation_note: str = ""

@dataclass
class Assessment:
    company: str
    location: str
    created_at: str          # ISO
    created_by: str
    industry: str = "Hotel/Gastgewerbe"
    scope_note: str = ""
    risk_matrix_thresholds: Dict[str, List[int]] = field(default_factory=lambda: {
        # Grenzwerte für 5x5 Matrix (Summe = prob*sev)
        # [low_max, mid_max, high_max] -> Rest = sehr hoch
        "thresholds": [6, 12, 16]  # 2..6 niedrig, 7..12 mittel, 13..16 hoch, 17..25 sehr hoch
    })
    hazards: List[Hazard] = field(default_factory=list)
    measures_plan_note: str = ""
    documentation_note: str = ""
    next_review_hint: str = ""

# =========================
# Utility
# =========================

def compute_risk(prob: int, sev: int, thresholds: List[int]) -> (int, str):
    v = prob * sev
    if v <= thresholds[0]:
        return v, "niedrig"
    elif v <= thresholds[1]:
        return v, "mittel"
    elif v <= thresholds[2]:
        return v, "hoch"
    else:
        return v, "sehr hoch"

def hazard_to_row(h: Hazard) -> Dict[str, Any]:
    return {
        "ID": h.id,
        "Bereich": h.area,
        "Tätigkeit": h.activity,
        "Gefährdung": h.hazard,
        "Quellen/Einwirkungen": "; ".join(h.sources),
        "Bestehende Maßnahmen": "; ".join(h.existing_controls),
        "Eintrittswahrscheinlichkeit (1-5)": h.prob,
        "Schadensschwere (1-5)": h.sev,
        "Risikosumme": h.risk_value,
        "Risikostufe": h.risk_level,
        "Letzte Prüfung": h.last_review or "",
        "Prüfer/in": h.reviewer,
        "Dokumentationshinweis": h.documentation_note
    }

def measures_to_rows(h: Hazard) -> List[Dict[str, Any]]:
    rows = []
    for m in h.additional_measures:
        rows.append({
            "Gefährdungs-ID": h.id,
            "Bereich": h.area,
            "Gefährdung": h.hazard,
            "Maßnahme": m.title,
            "STOP(+Q)": m.stop_level,
            "Verantwortlich": m.responsible,
            "Fällig am": m.due_date or "",
            "Status": m.status,
            "Hinweis": m.notes
        })
    return rows

def new_id(prefix="HZ", n=4) -> str:
    ts = datetime.now().strftime("%y%m%d%H%M%S%f")[-n:]
    return f"{prefix}-{int(datetime.now().timestamp())}-{ts}"

def dump_excel(assess: Assessment) -> bytes:
    """Excel-Export in-memory (keine Dateischreibrechte nötig)."""
    hazards_df = pd.DataFrame([hazard_to_row(h) for h in assess.hazards])
    measures_df = pd.DataFrame([r for h in assess.hazards for r in measures_to_rows(h)])

    meta = {
        "Unternehmen": assess.company,
        "Standort": assess.location,
        "Erstellt am": assess.created_at,
        "Erstellt von": assess.created_by,
        "Branche": assess.industry,
        "Umfang/Scope": assess.scope_note,
        "Maßnahmenplan-Hinweis": assess.measures_plan_note,
        "Dokumentationshinweis": assess.documentation_note,
        "Fortschreibung/Nächster Anlass": assess.next_review_hint
    }
    meta_df = pd.DataFrame(list(meta.items()), columns=["Feld", "Wert"])

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        meta_df.to_excel(writer, sheet_name="00_Meta", index=False)
        hazards_df.to_excel(writer, sheet_name="10_Gefaehrdungen", index=False)
        measures_df.to_excel(writer, sheet_name="20_Massnahmen", index=False)
        thresholds = assess.risk_matrix_thresholds["thresholds"]
        conf_df = pd.DataFrame({"Grenzen (Risikosumme)": ["niedrig ≤", "mittel ≤", "hoch ≤", "sehr hoch >"],
                                "Wert": [thresholds[0], thresholds[1], thresholds[2], thresholds[2]]})
        conf_df.to_excel(writer, sheet_name="90_Konfiguration", index=False)
    bio.seek(0)
    return bio.read()

def as_json(assess: Assessment) -> str:
    return json.dumps(asdict(assess), ensure_ascii=False, indent=2)

def from_json(s: str) -> Assessment:
    data = json.loads(s)
    hazards = []
    for h in data.get("hazards", []):
        measures = [Measure(**m) for m in h.get("additional_measures", [])]
        hazards.append(Hazard(
            id=h["id"],
            area=h["area"],
            activity=h["activity"],
            hazard=h["hazard"],
            sources=h.get("sources", []),
            # rückwärtskompatibel: "existing" oder "existing_controls"
            existing_controls=h.get("existing_controls", h.get("existing", [])),
            prob=h.get("prob", 3),
            sev=h.get("sev", 3),
            risk_value=h.get("risk_value", 9),
            risk_level=h.get("risk_level", "mittel"),
            additional_measures=measures,
            last_review=h.get("last_review"),
            reviewer=h.get("reviewer", ""),
            documentation_note=h.get("documentation_note", "")
        ))
    return Assessment(
        company=data.get("company",""),
        location=data.get("location",""),
        created_at=data.get("created_at",""),
        created_by=data.get("created_by",""),
        industry=data.get("industry","Hotel/Gastgewerbe"),
        scope_note=data.get("scope_note", ""),
        risk_matrix_thresholds=data.get("risk_matrix_thresholds", {"thresholds":[6,12,16]}),
        hazards=hazards,
        measures_plan_note=data.get("measures_plan_note",""),
        documentation_note=data.get("documentation_note",""),
        next_review_hint=data.get("next_review_hint","")
    )

# =========================
# Branchen-Bibliothek (erweiterbar)
# Struktur: { Branche: { Bereich: [ {activity, hazard, sources, existing, measures[]} ] } }
# =========================

LIB_HOTEL = {
    "Küche": [
        {
            "activity": "Kochen (Töpfe/Kessel)",
            "hazard": "Hitze, heiße Flüssigkeiten, Verbrühungen/Verbrennungen",
            "sources": ["Herde", "Kessel", "Töpfe", "Heißwasser"],
            "existing": ["Hitzeschutzhandschuhe/-schürzen", "Sichere Griffe/Ablagen", "Unterweisung"],
            "measures": [
                {"title": "Topfdeckel-/Spritzschutz konsequent nutzen", "stop_level": "T (Technisch)"},
                {"title": "Arbeitswege freihalten & ‚Heiß!‘ rufen", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutzhandschuhe bereitstellen/prüfen", "stop_level": "P (PSA)"},
                {"title": "Unterweisung Verbrühungen/Verbrennungen", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Braten (Pfanne/Grillplatte)",
            "hazard": "Fettspritzer, Verbrennungen, Rauch/Dämpfe",
            "sources": ["Bratpfannen", "Grillplatten"],
            "existing": ["Spritzschutz/Abdeckungen", "Abzugshaube funktionsfähig"],
            "measures": [
                {"title": "Spritzschutz an Grillplatten verwenden", "stop_level": "T (Technisch)"},
                {"title": "Abluftleistung prüfen/reinigen", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Frittieren",
            "hazard": "Fettbrand, Verbrennungen, Spritzer",
            "sources": ["Fritteusen"],
            "existing": ["Fettbrandlöscher/Löschdecke", "Kein Wasser!", "Unterweisung Fettbrand"],
            "measures": [
                {"title": "Ölwechsel- & Reinigungsplan festlegen", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutzschürze und -handschuhe Pflicht", "stop_level": "P (PSA)"}
            ]
        },
        {
            "activity": "Kombidämpfer/Dampfgarer öffnen",
            "hazard": "Dampf/Heißluft – Verbrühung beim Öffnen",
            "sources": ["Kombidämpfer", "Dampfgarer"],
            "existing": ["Tür vorsichtig öffnen", "Abkühlzeit beachten"],
            "measures": [
                {"title": "Türöffnungsroutine (Spalt) einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutzhandschuhe verpflichtend", "stop_level": "P (PSA)"}
            ]
        },
        {
            "activity": "Schneiden – Arbeiten mit Messern",
            "hazard": "Schnitt-/Stichverletzungen",
            "sources": ["Messer", "Schneidbretter"],
            "existing": ["Scharfe Messer", "Schnittschutzhandschuhe nach Bedarf"],
            "measures": [
                {"title": "Schleifplan/Messerservice einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Schnittschutz bei Risikoschnitten", "stop_level": "P (PSA)"}
            ]
        },
        {
            "activity": "Maschinen: Aufschnittmaschine",
            "hazard": "Schnittverletzungen an rotierenden Klingen",
            "sources": ["Aufschnittmaschine"],
            "existing": ["Schutzhauben", "Nur Befugte", "Strom trennen bei Reinigung"],
            "measures": [
                {"title": "Sicherheitsbauteile prüfen (Haube/Not-Aus)", "stop_level": "T (Technisch)"},
                {"title": "Berechtigungssystem für Bediener", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Maschinen: Fleischwolf/Gemüseschneider",
            "hazard": "Eingezogenwerden, Schnittverletzung",
            "sources": ["Fleischwolf", "Gemüseschneider"],
            "existing": ["Stopfer benutzen", "Nie mit der Hand nachschieben"],
            "measures": [
                {"title": "Stopfer/Schutzeinrichtungen bereitstellen", "stop_level": "T (Technisch)"},
                {"title": "Unterweisung: Einziehen vermeiden/Not-Aus", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Spülbereich/Stewarding",
            "hazard": "Heißes Wasser/Dampf, Chemikalien, Rutschgefahr",
            "sources": ["Spülmaschine", "Klarspüler", "Nasse Böden"],
            "existing": ["Hand-/Augenschutz", "Rutschhemmende Schuhe"],
            "measures": [
                {"title": "Sofort-Wisch-Regel & Warnschilder", "stop_level": "O (Organisatorisch)"},
                {"title": "Anti-Rutsch-Matten an Engstellen", "stop_level": "T (Technisch)"}
            ]
        },
        {
            "activity": "Gasgeräte",
            "hazard": "Gasleck, CO-Bildung, Brand/Explosion",
            "sources": ["Gasherde", "Leitungen"],
            "existing": ["Dichtheitsprüfung", "Gute Belüftung"],
            "measures": [
                {"title": "Gaswarnmelder installieren/warten", "stop_level": "T (Technisch)"},
                {"title": "Leckcheck-Freigabe vor Inbetriebnahme", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Warenannahme/Hubwagen",
            "hazard": "Quetschungen, Heben/Tragen, Verkehrswege",
            "sources": ["Rollcontainer", "Kisten", "Handhubwagen"],
            "existing": ["Rollwagen/Hubhilfe", "Hebetechnik"],
            "measures": [
                {"title": "Wege kennzeichnen & freihalten", "stop_level": "O (Organisatorisch)"},
                {"title": "Kurzunterweisung Heben/Tragen & Hubwagen", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
    ],
    "Housekeeping": [
        {
            "activity": "Betten machen",
            "hazard": "Rücken-/Schulterbelastung, Verdrehungen",
            "sources": ["Schwere Matratzen", "Beengte Bereiche"],
            "existing": ["Arbeitstechnik", "Höhenverstellbare Wagen"],
            "measures": [
                {"title": "Stecklaken-/Ecken-Technik schulen", "stop_level": "Q (Qualifikation/Unterweisung)"},
                {"title": "Leichtere Bettwaren beschaffen", "stop_level": "S (Substitution/Quelle entfernen)"}
            ]
        },
        {
            "activity": "Sanitärreinigung",
            "hazard": "Chemikalienreizungen, Aerosole",
            "sources": ["Reiniger/Desinfektion", "Sprühflaschen"],
            "existing": ["Hautschutzplan", "Hand-/Augenschutz"],
            "measures": [
                {"title": "Vordosierte Kartuschen statt Sprühnebel", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Dosierstation & Piktogramme", "stop_level": "T (Technisch)"}
            ]
        },
        {
            "activity": "Fensterreinigung innen",
            "hazard": "Sturz, Schnitt an Glas",
            "sources": ["Fensterfronten", "Tritte/Leitern"],
            "existing": ["Leiterprüfung", "Standflächen sichern"],
            "measures": [
                {"title": "Teleskopstiele statt Leiter (wo möglich)", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Schnittfeste Handschuhe bei Bruchgefahr", "stop_level": "P (PSA)"}
            ]
        }
    ],
    "Service/Bar": [
        {
            "activity": "Heißgetränke zubereiten",
            "hazard": "Verbrühungen/Verbrennungen",
            "sources": ["Kaffeemaschine", "Wasserkocher", "Dampflanze"],
            "existing": ["Hitzeschutz", "Sichere Ablagen"],
            "measures": [
                {"title": "Dampflanzen-Routine (Ablassen vor Nutzung)", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "CO₂/Zapfanlage & Flaschenwechsel",
            "hazard": "Erstickungsgefahr, Hochdruck",
            "sources": ["CO₂-Flaschen", "Keller"],
            "existing": ["CO₂-Warner/Lüftung", "Flaschen sichern"],
            "measures": [
                {"title": "CO₂-Sensoren testen & dokumentieren", "stop_level": "T (Technisch)"},
                {"title": "Wechsel nur zu zweit, nach Belüftung", "stop_level": "O (Organisatorisch)"}
            ]
        }
    ],
    "Technik/Haustechnik": [
        {
            "activity": "Elektroarbeiten (E-Fachkräfte/EUP)",
            "hazard": "Elektrischer Schlag, Lichtbogen",
            "sources": ["Verteilungen", "Feuchte Bereiche"],
            "existing": ["Freischalten/Sperren/Kennzeichnen (LOTO)"],
            "measures": [
                {"title": "LOTO-Verfahren dokumentieren", "stop_level": "O (Organisatorisch)"},
                {"title": "Spannungsprüfer/geeignete PSA", "stop_level": "T (Technisch)"}
            ]
        },
        {
            "activity": "Heißarbeiten (Schweißen/Trennen)",
            "hazard": "Brand/Explosion, Rauch",
            "sources": ["Schweißgerät", "Schneidbrenner"],
            "existing": ["Heißarbeitsgenehmigung", "Feuerwache/Nachkontrolle"],
            "measures": [
                {"title": "Funkenschutz/Abschirmungen bereitstellen", "stop_level": "T (Technisch)"},
                {"title": "Löschmittel/Feuerlöscher bereit halten", "stop_level": "O (Organisatorisch)"}
            ]
        },
    ],
    "Lager/Wareneingang": [
        {
            "activity": "Auspacken/Öffnen",
            "hazard": "Schnittverletzungen, Stolpern",
            "sources": ["Cuttermesser", "Folien/Umreifungen"],
            "existing": ["Sichere Messer", "Müll sofort entsorgen"],
            "measures": [
                {"title": "Sicherheitsmesser (Klingenrückzug)", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Müll-Station nahe Rampe definieren", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Palettieren/Bewegen",
            "hazard": "Quetschungen, Anfahren",
            "sources": ["Rollcontainer", "Hubwagen"],
            "existing": ["Wege markieren", "Langsam fahren"],
            "measures": [
                {"title": "Anschläge/Stopper an Rampen", "stop_level": "T (Technisch)"},
                {"title": "Verkehrsordnung aushängen", "stop_level": "O (Organisatorisch)"}
            ]
        }
    ],
    "Spa/Wellness": [
        {
            "activity": "Sauna/Ofen & Aufguss",
            "hazard": "Verbrennungen, Brand, Heißdampf",
            "sources": ["Saunaöfen", "Aufguss"],
            "existing": ["Abschirmungen", "Nur Befugte"],
            "measures": [
                {"title": "Ofenschutzgitter/Temperaturwächter prüfen", "stop_level": "T (Technisch)"},
                {"title": "Aufgussregeln verbindlich festlegen", "stop_level": "O (Organisatorisch)"}
            ]
        }
    ],
    "Rezeption": [
        {
            "activity": "Front Office/Gästekommunikation",
            "hazard": "Psychische Belastung, Konflikte",
            "sources": ["Beschwerden", "Stoßzeiten"],
            "existing": ["Deeskalationstraining", "Pausenplanung"],
            "measures": [
                {"title": "Stoßzeiten doppelt besetzen", "stop_level": "O (Organisatorisch)"}
            ]
        }
    ],
    "Verwaltung": [
        {
            "activity": "Bildschirmarbeit",
            "hazard": "Haltungs-/Augenbelastung",
            "sources": ["Sitzplätze", "Monitore"],
            "existing": ["Angepasster Arbeitsplatz", "Höhenverstellbarer Tisch/Stuhl"],
            "measures": [
                {"title": "20-20-20-Regel & Mikropausen einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Sehtest/Bildschirmbrille anbieten", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        }
    ],
    "Außenbereiche": [
        {
            "activity": "Gartenpflege/Mähen",
            "hazard": "Projektilwurf, Lärm",
            "sources": ["Rasenmäher/Trimmer"],
            "existing": ["Schutzbrille", "Gehörschutz"],
            "measures": [
                {"title": "Stein-/Fremdkörperkontrolle vor Start", "stop_level": "O (Organisatorisch)"},
                {"title": "Schutzvisier/Gehörschutz bereitstellen", "stop_level": "P (PSA)"}
            ]
        }
    ]
}

LIB_BAECKEREI = {
    "Produktion": [
        {"activity": "Backen am Etagen-/Stikkenofen", "hazard": "Hitze/Verbrennung, Dampf", "sources": ["Öfen", "Backwagen"], "existing": ["Hitzeschutz"], "measures":[
            {"title":"Backwagen fixieren & Handschutz nutzen","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Knetmaschine/Spiral-/Hubkneter", "hazard": "Eingezogenwerden/Quetschen", "sources": ["Knetmaschine"], "existing": ["Schutzhaube", "Not-Aus"], "measures":[
            {"title":"Hauben-/Not-Aus-Prüfplan","stop_level":"T (Technisch)"}]},
        {"activity": "Teigteiler/Rundwirker", "hazard": "Quetschen/Schnitt", "sources": ["Teigteiler", "Rundwirker"], "existing": ["Schutzvorrichtungen"], "measures":[
            {"title":"Nur mit Werkzeug reinigen (stromlos)","stop_level":"Q (Qualifikation/Unterweisung)"}]},
        {"activity": "Fritteuse/Schmalzbacken", "hazard": "Fettbrand, Verbrennung", "sources": ["Fritteuse"], "existing": ["Fettbrandlöscher"], "measures":[
            {"title":"Ölwechselplan/Temperaturgrenzen","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Mehlstaub/Abwiegen", "hazard": "Staubexposition, pot. Explosion", "sources": ["Mehlstaub"], "existing": ["Absaugung/Lüftung"], "measures":[
            {"title":"Staubarme Dosierung/geschl. Systeme","stop_level":"S (Substitution/Quelle entfernen)"}]},
        {"activity": "Schockfrosten/Kühlräume", "hazard": "Kälte/Rutschgefahr", "sources": ["TK", "Kühlräume"], "existing": ["Kälteschutz"], "measures":[
            {"title":"Aufenthaltsdauer begrenzen","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Reinigung/Desinfektion", "hazard": "Chemikalien/Ätzwirkung", "sources": ["Reiniger/Desinfektion"], "existing": ["Haut-/Augenschutz"], "measures":[
            {"title":"Dosierstationen & Betriebsanweisungen","stop_level":"T (Technisch)"}]},
    ],
    "Verkauf": [
        {"activity": "Brotschneiden/Brotschneidemaschine", "hazard": "Schnittverletzung", "sources": ["Brotschneider"], "existing": ["Schutzhaube"], "measures":[
            {"title":"Nur befugte Bedienung","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Heißgetränke", "hazard": "Verbrühung", "sources": ["Kaffeemaschine"], "existing": ["Hitzeschutz"], "measures":[
            {"title":"Dampflanze vorher abblasen","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Kassentätigkeit", "hazard": "Ergonomie, Überfallrisiko (einzelfallabh.)", "sources": ["Kasse"], "existing": ["Schulung"], "measures":[
            {"title":"Kassenrichtlinie/Deeskalation","stop_level":"O (Organisatorisch)"}]},
    ],
    "Logistik": [
        {"activity": "Lieferung/Backwagen", "hazard": "Quetschungen/Sturz", "sources": ["Backwagen", "Rampe"], "existing": ["Sichern/Stopper"], "measures":[
            {"title":"Rampe sichern/Stopper nutzen","stop_level":"T (Technisch)"}]},
    ]
}

LIB_FLEISCHEREI = {
    "Produktion": [
        {"activity": "Bandsäge", "hazard": "Schnitt/Amputation", "sources": ["Bandsäge"], "existing": ["Schutzhaube", "Not-Aus"], "measures":[
            {"title":"Nur befugte Bedienung, Reinigung stromlos","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Fleischwolf", "hazard": "Eingezogenwerden", "sources": ["Fleischwolf"], "existing": ["Stopfer", "Schutz"], "measures":[
            {"title":"Stopfer konsequent nutzen","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Kutter", "hazard": "Schnitt/Schlag", "sources": ["Kutter"], "existing": ["Haube", "Verriegelung"], "measures":[
            {"title":"Verriegelung prüfen, nur stromlos reinigen","stop_level":"T (Technisch)"}]},
        {"activity": "Vakuumierer/Schrumpfer", "hazard": "Verbrennung/Quetschung", "sources": ["Heißsiegel"], "existing": ["Hitzeschutz"], "measures":[
            {"title":"Heißsiegelzonen markieren","stop_level":"T (Technisch)"}]},
        {"activity": "Kühl-/TK-Lager", "hazard": "Kälte/Rutsch", "sources": ["Kühl/TK"], "existing": ["Kälteschutz"], "measures":[
            {"title":"Zeitbegrenzung/Matten","stop_level":"O (Organisatorisch)"}]},
        {"activity": "Reinigung/Desinfektion", "hazard": "Chemische Belastung", "sources": ["Reiniger"], "existing": ["PSA"], "measures":[
            {"title":"Dosier-/Sicherheitsdatenblatt an Station","stop_level":"T (Technisch)"}]},
    ],
    "Verkauf": [
        {"activity": "Aufschnitt/Bedienung", "hazard": "Schnittverletzung", "sources": ["Aufschnitt"], "existing": ["Schutzhaube"], "measures":[
            {"title":"Messerschulung/Handschutz bei Bedarf","stop_level":"Q (Qualifikation/Unterweisung)"}]},
        {"activity": "Heißtheke", "hazard": "Verbrennung", "sources": ["Heiße Theken"], "existing": ["Hitzeschutz"], "measures":[
            {"title":"Abdeckung/Abstellen sichern","stop_level":"T (Technisch)"}]},
    ]
}

LIB_KANTINE = {
    "Küche": [
        {"activity":"Großkochgeräte/Kippkessel","hazard":"Verbrühung, Quetschung beim Kippen","sources":["Kippkessel"],"existing":["Hitzeschutz","2-Hand-Bed. je nach Modell"],"measures":[
            {"title":"Kipp-Prozess standardisieren","stop_level":"O (Organisatorisch)"}]},
        {"activity":"Tablettförderband/Spülstraße","hazard":"Einklemm-/Scherstellen, Heißwasser/Dampf","sources":["Bandspülmaschine"],"existing":["Abdeckungen","Not-Aus"],"measures":[
            {"title":"Nur befugte Bedienung, Hauben zu","stop_level":"O (Organisatorisch)"}]},
        {"activity":"Ausgabe/Frontcooking","hazard":"Verbrennung, Kontakt mit Gästen","sources":["Wärmebrücken","Pfannen"],"existing":["Abschirmung","Greifzonen"],"measures":[
            {"title":"Abstand/Abschirmung zu Gastbereichen","stop_level":"T (Technisch)"}]},
    ],
    "Logistik": [
        {"activity":"Transportwagen/Tablettwagen","hazard":"Quetschen/Stolpern","sources":["Rollwagen","Aufzüge"],"existing":["Wege frei"],"measures":[
            {"title":"Lastbegrenzung/Wegepriorität","stop_level":"O (Organisatorisch)"}]},
    ]
}

LIB_KONDITOREI = {
    "Produktion": [
        {"activity":"Zucker kochen/Karamell","hazard":"Heißsirup/Verbrennung","sources":["Kocher"],"existing":["Hitzeschutz"],"measures":[
            {"title":"Schutzbrille, langsames Aufgießen","stop_level":"P (PSA)"}]},
        {"activity":"Kuvertüre/Temperieren","hazard":"Hitze, Spritzer","sources":["Bad/Tempering"],"existing":["Hitzeschutz"],"measures":[
            {"title":"Deckel/Spritzschutz nutzen","stop_level":"T (Technisch)"}]},
        {"activity":"Kleingeräte/Rührwerke","hazard":"Scher-/Einklemmstellen","sources":["Rührwerk"],"existing":["Schutz","Not-Aus"],"measures":[
            {"title":"Nur stromlos reinigen","stop_level":"O (Organisatorisch)"}]},
        {"activity":"Kühl-/TK","hazard":"Kälte/Rutsch","sources":["Kühl/TK"],"existing":["Kälteschutz"],"measures":[
            {"title":"Aufenthalt begrenzen/Eis entfernen","stop_level":"O (Organisatorisch)"}]},
        {"activity":"Reinigung","hazard":"Chemikalien","sources":["Reiniger"],"existing":["PSA"],"measures":[
            {"title":"Dosierhilfen/Betriebsanweisung","stop_level":"T (Technisch)"}]},
    ],
    "Verkauf/Café": [
        {"activity":"Kaffeemaschine/Heißgetränke","hazard":"Verbrühung","sources":["Dampflanze"],"existing":["Hitzeschutz"],"measures":[
            {"title":"Dampflanze abblasen vor Nutzung","stop_level":"O (Organisatorisch)"}]},
        {"activity":"Tortenmesser/Glasvitrine","hazard":"Schnitt/Glasschaden","sources":["Glas","Messer"],"existing":["Sichere Entsorgung"],"measures":[
            {"title":"Polier-/Schnittschutzhandschuhe nach Bedarf","stop_level":"P (PSA)"}]},
    ]
}

INDUSTRY_LIBRARY: Dict[str, Dict[str, List[Dict[str, Any]]]] = {
    "Hotel/Gastgewerbe": LIB_HOTEL,
    "Bäckerei": LIB_BAECKEREI,
    "Fleischerei/Metzgerei": LIB_FLEISCHEREI,
    "Gemeinschaftsverpflegung/Kantine": LIB_KANTINE,
    "Konditorei/Café": LIB_KONDITOREI,
}

# =========================
# Vorlagen laden (nach Branche)
# =========================

def add_template_items(assess: Assessment, template: Dict[str, List[Dict[str, Any]]]):
    """Fügt Bereiche/Tätigkeiten aus einer Branchenvorlage hinzu (anhängen)."""
    for area, items in template.items():
        for item in items:
            hz = Hazard(
                id=new_id(),
                area=area,
                activity=item["activity"],
                hazard=item["hazard"],
                sources=item.get("sources", []),
                existing_controls=item.get("existing", [])
            )
            for m in item.get("measures", []):
                hz.additional_measures.append(Measure(
                    title=m["title"],
                    stop_level=m["stop_level"],
                    notes=m.get("notes", "")
                ))
            assess.hazards.append(hz)

def preload_industry(assess: Assessment, industry_name: str, replace: bool = True):
    """Lädt Branchen-Vorlage; ersetzt vorhandene Gefährdungen oder hängt an."""
    assess.industry = industry_name
    if replace:
        assess.hazards = []
    template = INDUSTRY_LIBRARY.get(industry_name, {})
    add_template_items(assess, template)

# =========================
# Streamlit App
# =========================

st.set_page_config(page_title="Gefährdungsbeurteilung – Branchen (BGN)", layout="wide")

# Session initialisieren (robust)
if "assessment" not in st.session_state or st.session_state.get("assessment") is None:
    st.session_state.assessment = Assessment(
        company="Musterbetrieb GmbH",
        location="Beispielstadt",
        created_at=date.today().isoformat(),
        created_by="HSE/SiFa",
        industry="Hotel/Gastgewerbe",
    )
    # Standard: Hotel/Gastgewerbe laden
    preload_industry(st.session_state.assessment, "Hotel/Gastgewerbe", replace=True)

assess: Assessment = st.session_state.assessment

# Kopf mit Duplizieren-Button (optional)
col_head1, col_head2 = st.columns([0.8, 0.2])
with col_head1:
    st.title("Gefährdungsbeurteilung – Branchen (BGN)")
with col_head2:
    if st.button("📄 Duplizieren", key="btn_duplicate"):
        assess.created_at = date.today().isoformat()
        assess.company = f"{assess.company} (Kopie)"
        st.success("Kopie erstellt. Bitte speichern/exportieren.")

st.caption("Struktur: Vorbereiten → Ermitteln → Beurteilen → Maßnahmen → Umsetzen → Wirksamkeit → Dokumentieren → Fortschreiben")

# Seitenleiste: Meta & Konfiguration & Branchenwahl
with st.sidebar:
    st.header("Stammdaten")
    assess.company = st.text_input("Unternehmen", assess.company, key="meta_company")
    assess.location = st.text_input("Standort", assess.location, key="meta_location")
    assess.created_by = st.text_input("Erstellt von", assess.created_by, key="meta_created_by")
    assess.created_at = st.text_input("Erstellt am (ISO)", assess.created_at, key="meta_created_at")

    st.markdown("---")
    st.subheader("Branche wählen")

    # --- Branchenwahl robust ---
    options = list(INDUSTRY_LIBRARY.keys())
    # Assessment aus Session holen/absichern
    assess_tmp = st.session_state.get("assessment", None)
    if assess_tmp is None:
        st.session_state.assessment = Assessment(
            company="Musterbetrieb GmbH",
            location="Beispielstadt",
            created_at=date.today().isoformat(),
            created_by="HSE/SiFa",
            industry="Hotel/Gastgewerbe",
        )
        preload_industry(st.session_state.assessment, "Hotel/Gastgewerbe", replace=True)
        assess_tmp = st.session_state.assessment

    current_industry = getattr(assess_tmp, "industry", None) or "Hotel/Gastgewerbe"
    try:
        default_idx = options.index(current_industry) if current_industry in INDUSTRY_LIBRARY else 0
    except Exception:
        default_idx = 0

    sector = st.selectbox(
        "Branche",
        options=options,
        index=default_idx,
        key="sel_industry"
    )
    st.caption(f"Aktuell geladen: **{assess.industry}**")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("📚 Vorlage ERSETZEN", key="btn_load_replace"):
            preload_industry(assess, sector, replace=True)
            st.success(f"Vorlage '{sector}' geladen (ersetzt).")
            st.rerun()
    with c2:
        if st.button("➕ Vorlage ANHÄNGEN", key="btn_load_append"):
            preload_industry(assess, sector, replace=False)
            st.success(f"Vorlage '{sector}' hinzugefügt.")
            st.rerun()

    st.markdown("---")
    st.subheader("Risikomatrix (5×5)")
    thr = assess.risk_matrix_thresholds.get("thresholds", [6, 12, 16])
    low = st.number_input("Grenze niedrig (≤)", min_value=2, max_value=10, value=int(thr[0]), key="thr_low")
    mid = st.number_input("Grenze mittel (≤)", min_value=low+1, max_value=16, value=int(thr[1]), key="thr_mid")
    high = st.number_input("Grenze hoch (≤)", min_value=mid+1, max_value=24, value=int(thr[2]), key="thr_high")
    assess.risk_matrix_thresholds["thresholds"] = [low, mid, high]

    st.markdown("---")
    st.subheader("Export / Speicher")
    if st.button("📥 JSON sichern (Download unten aktualisieren)", key="btn_json_dump"):
        st.session_state["json_blob"] = as_json(assess)
    json_blob = st.session_state.get("json_blob", as_json(assess))
    st.download_button("⬇️ Download JSON", data=json_blob, file_name="gefaehrdungsbeurteilung.json", mime="application/json", key="btn_dl_json")

    excel_bytes = dump_excel(assess)
    st.download_button("⬇️ Download Excel", data=excel_bytes, file_name="Gefaehrdungsbeurteilung.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="btn_dl_excel")

    st.markdown("---")
    st.subheader("JSON laden")
    up = st.file_uploader("Bestehende Beurteilung (.json)", type=["json"], key="uploader_json")
    if up is not None:
        content = up.read().decode("utf-8")
        st.session_state.assessment = from_json(content)
        # Fallbacks für alte JSONs:
        if not getattr(st.session_state.assessment, "industry", None):
            st.session_state.assessment.industry = "Hotel/Gastgewerbe"
        st.success("Beurteilung geladen.")
        st.rerun()

# Tabs = Prozessschritte
tabs = st.tabs([
    "1 Vorbereiten", "2 Ermitteln", "3 Beurteilen", "4 Maßnahmen", "5 Umsetzen",
    "6 Wirksamkeit", "7 Dokumentation", "8 Fortschreiben", "Übersicht"
])

# 1 Vorbereiten
with tabs[0]:
    st.subheader("1) Vorbereiten")
    assess.scope_note = st.text_area(
        "Umfang / Arbeitsbereiche / Beteiligte (SiFa, Betriebsarzt, BR, Führungskräfte, Beschäftigte)",
        value=assess.scope_note, height=140, key="scope_note"
    )
    st.info("Tipp: Branche wählen/prüfen und relevante Bereiche festlegen; Unterlagen (Betriebsanweisungen, SDS, Wartungspläne) sammeln.")

# 2 Ermitteln
with tabs[1]:
    st.subheader("2) Gefährdungen ermitteln")
    colL, colR = st.columns([2,1])

    with colL:
        st.markdown("**Gefährdungen (Bearbeiten)**")
        if assess.hazards:
            df = pd.DataFrame([hazard_to_row(h) for h in assess.hazards])
            st.dataframe(df, use_container_width=True, hide_index=True, key="df_hazards")
        else:
            st.info("Keine Gefährdungen vorhanden. In der Sidebar eine Branchenvorlage laden.")

        with st.expander("➕ Gefährdung hinzufügen"):
            col1, col2 = st.columns(2)
            # Bereiche dynamisch aus aktueller Branche + vorhandenen Bereichen generieren
            known_areas = sorted({h.area for h in assess.hazards} | set(INDUSTRY_LIBRARY.get(assess.industry, {}).keys()) | {"Sonstiges"})
            area = col1.selectbox("Bereich", known_areas, key="add_area")
            activity = col2.text_input("Tätigkeit", key="add_activity")
            hazard_txt = st.text_input("Gefährdung", key="add_hazard")
            sources = st.text_input("Quellen/Einwirkungen (durch ; trennen)", key="add_sources")
            existing = st.text_input("Bestehende Maßnahmen (durch ; trennen)", key="add_existing")
            if st.button("Hinzufügen", key="btn_add_hazard"):
                assess.hazards.append(Hazard(
                    id=new_id(), area=area, activity=activity, hazard=hazard_txt,
                    sources=[s.strip() for s in sources.split(";") if s.strip()],
                    existing_controls=[e.strip() for e in existing.split(";") if e.strip()]
                ))
                st.success("Gefährdung hinzugefügt.")

    with colR:
        st.markdown("**Auswahl & Details**")
        ids = [h.id for h in assess.hazards]
        sel_id = st.selectbox(
            "Gefährdung auswählen (ID)",
            options=["--"] + ids,
            index=0,
            key="sel_hazard_edit"
        )
        if sel_id != "--":
            hz = next(h for h in assess.hazards if h.id == sel_id)
            all_areas = list(INDUSTRY_LIBRARY.get(assess.industry, {}).keys()) + ["Sonstiges"]
            idx = all_areas.index(hz.area) if hz.area in all_areas else len(all_areas)-1
            hz.area = st.selectbox("Bereich", options=all_areas, index=idx, key=f"edit_area_{hz.id}")
            hz.activity = st.text_input("Tätigkeit", value=hz.activity, key=f"edit_activity_{hz.id}")
            hz.hazard = st.text_input("Gefährdung", value=hz.hazard, key=f"edit_hazard_{hz.id}")
            src = st.text_area("Quellen/Einwirkungen", value="; ".join(hz.sources), key=f"edit_sources_{hz.id}")
            hz.sources = [s.strip() for s in src.split(";") if s.strip()]
            ex = st.text_area("Bestehende Maßnahmen", value="; ".join(hz.existing_controls), key=f"edit_existing_{hz.id}")
            hz.existing_controls = [e.strip() for e in ex.split(";") if e.strip()]
            if st.button("🗑️ Löschen", key=f"btn_delete_{hz.id}"):
                assess.hazards = [h for h in assess.hazards if h.id != sel_id]
                st.warning("Gefährdung gelöscht.")
                st.rerun()

# 3 Beurteilen
with tabs[2]:
    st.subheader("3) Gefährdungen beurteilen (5×5; NOHL-Logik: Wahrscheinlichkeit × Schwere)")
    thresholds = assess.risk_matrix_thresholds["thresholds"]
    colA, colB = st.columns([1,1])

    with colA:
        if not assess.hazards:
            st.info("Keine Gefährdungen vorhanden. Bitte Branchenvorlage laden.")
        else:
            sel = st.selectbox(
                "Gefährdung auswählen",
                options=[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards],
                key="sel_hazard_assess"
            )
            hz = assess.hazards[[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards].index(sel)]
            hz.prob = st.slider("Eintrittswahrscheinlichkeit (1 = sehr selten … 5 = häufig)", 1, 5, hz.prob, key=f"prob_{hz.id}")
            hz.sev = st.slider("Schadensschwere (1 = gering … 5 = katastrophal)", 1, 5, hz.sev, key=f"sev_{hz.id}")
            v, lvl = compute_risk(hz.prob, hz.sev, thresholds)
            hz.risk_value, hz.risk_level = v, lvl

            st.markdown(f"**Risikosumme:** {v}  —  **Stufe:** :{('green' if lvl=='niedrig' else 'orange' if lvl=='mittel' else 'red')}_circle: {lvl}")

            hz.documentation_note = st.text_area("Beurteilungs-/Dokumentationshinweis", value=hz.documentation_note, key=f"doc_note_{hz.id}")

    with colB:
        st.markdown("**Schnellübersicht (Top-Risiken)**")
        if assess.hazards:
            top = sorted(assess.hazards, key=lambda x: x.risk_value, reverse=True)[:10]
            top_df = pd.DataFrame([{"ID":h.id, "Bereich":h.area, "Gefährdung":h.hazard, "Risiko":h.risk_value, "Stufe":h.risk_level} for h in top])
            st.dataframe(top_df, hide_index=True, use_container_width=True, key="df_top_risks")
        else:
            st.caption("Noch keine Daten.")

# 4 Maßnahmen
with tabs[3]:
    st.subheader("4) Maßnahmen festlegen (STOP + Q)")
    st.caption("Zuerst an der Quelle vermeiden/vermindern, dann technisch, organisatorisch, PSA – ggf. Qualifikation/Unterweisung ergänzen.")

    if not assess.hazards:
        st.info("Keine Gefährdungen vorhanden. Bitte Branchenvorlage laden.")
    else:
        sel = st.selectbox(
            "Gefährdung auswählen",
            options=[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards],
            key="sel_hazard_measures"
        )
        hz = assess.hazards[[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards].index(sel)]

        with st.expander("➕ Maßnahme hinzufügen"):
            title = st.text_input("Maßnahme", key=f"m_title_{hz.id}")
            stop = st.selectbox("STOP(+Q)", STOP_LEVELS, index=0, key=f"m_stop_{hz.id}")
            responsible = st.text_input("Verantwortlich", key=f"m_resp_{hz.id}")
            due = st.date_input("Fällig am", value=date.today()+relativedelta(months=1), key=f"m_due_{hz.id}")
            notes = st.text_area("Hinweis", key=f"m_note_{hz.id}")
            if st.button("Hinzufügen ➕", key=f"btn_add_measure_{hz.id}"):
                hz.additional_measures.append(Measure(title=title, stop_level=stop, responsible=responsible, due_date=due.isoformat(), notes=notes))
                st.success("Maßnahme hinzugefügt.")

        if hz.additional_measures:
            mdf = pd.DataFrame([asdict(m) for m in hz.additional_measures])
            st.dataframe(mdf, use_container_width=True, hide_index=True, key=f"df_measures_{hz.id}")

# 5 Umsetzen
with tabs[4]:
    st.subheader("5) Maßnahmen umsetzen (Plan/Status)")
    st.caption("Priorisierung nach Risikosumme; Verantwortliche & Termine festlegen.")
    rows = []
    for h in assess.hazards:
        for m in h.additional_measures:
            rows.append({"ID": h.id, "Bereich": h.area, "Gefährdung": h.hazard, "Risiko": h.risk_value,
                         "Maßnahme": m.title, "STOP(+Q)": m.stop_level, "Fällig": m.due_date or "", "Status": m.status, "Verantwortlich": m.responsible})
    if rows:
        plan = pd.DataFrame(rows).sort_values(by=["Risiko"], ascending=False)
        st.dataframe(plan, use_container_width=True, hide_index=True, key="df_plan")
    else:
        st.info("Noch keine Maßnahmen geplant.")

# 6 Wirksamkeit
with tabs[5]:
    st.subheader("6) Wirksamkeit überprüfen")
    if not assess.hazards:
        st.info("Keine Gefährdungen vorhanden. Bitte Branchenvorlage laden.")
    else:
        sel = st.selectbox(
            "Gefährdung auswählen",
            options=[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards],
            key="sel_hazard_review"
        )
        hz = assess.hazards[[f"{h.id} – {h.area}: {h.hazard}" for h in assess.hazards].index(sel)]
        if hz.additional_measures:
            for i, m in enumerate(hz.additional_measures):
                st.markdown(f"**{i+1}. {m.title}**  ({m.stop_level})")
                m.status = st.selectbox("Status", STATUS_LIST, index=STATUS_LIST.index(m.status) if m.status in STATUS_LIST else 0, key=f"stat_{hz.id}_{i}")
                m.notes = st.text_area("Wirksamkeits-/Prüfhinweis", value=m.notes, key=f"notes_{hz.id}_{i}")
        else:
            st.info("Für diese Gefährdung sind noch keine Maßnahmen hinterlegt.")
        hz.last_review = st.date_input("Datum der Überprüfung", value=date.today(), key=f"rev_date_{hz.id}").isoformat()
        hz.reviewer = st.text_input("Prüfer/in", value=hz.reviewer, key=f"rev_reviewer_{hz.id}")

# 7 Dokumentation
with tabs[6]:
    st.subheader("7) Ergebnisse dokumentieren")
    assess.documentation_note = st.text_area("Dokumentationshinweis (welche Unterlagen, wo abgelegt, Versionierung)", value=assess.documentation_note, height=120, key="doc_note_global")
    st.markdown("**Nachweise/Beispiele (frei ergänzen):** Betriebsanweisungen, Unterweisungsnachweise, Prüfprotokolle (Leitern/Elektro), Wartungspläne (z. B. Lüftung/Legionellen), Gefahrstoffverzeichnis, Unfallstatistik, Beinahe-Unfälle.")

# 8 Fortschreiben
with tabs[7]:
    st.subheader("8) Fortschreiben")
    assess.next_review_hint = st.text_area("Anlässe/Fristen (z. B. jährliche Überprüfung, nach Unfällen/Beinaheunfällen, bei Änderungen von Verfahren/Organisation/Arbeitsmitteln)", value=assess.next_review_hint, height=100, key="next_review_hint")
    st.info("Hinweis: Änderungen dokumentieren und Datums-/Namensfeld bei Überprüfung ergänzen.")

# Übersicht
with tabs[8]:
    st.subheader("Übersicht & Kennzahlen")
    total = len(assess.hazards)
    high = len([h for h in assess.hazards if h.risk_level in ("hoch", "sehr hoch")])
    st.metric("Gefährdungen gesamt", total)
    st.metric("Davon hoch/sehr hoch", high)
    if total:
        by_area = pd.DataFrame(pd.Series([h.area for h in assess.hazards]).value_counts(), columns=["Anzahl"])
        st.markdown("**Gefährdungen je Bereich**")
        st.dataframe(by_area, use_container_width=True, key="df_by_area")
    st.markdown("**Hinweise**")
    assess.measures_plan_note = st.text_area("Projekt-/Maßnahmenplan (kurz)", value=assess.measures_plan_note, key="measures_plan_note")
