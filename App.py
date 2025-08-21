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
            id=h["id"], area=h["area"], activity=h["activity"], hazard=h["hazard"],
            sources=h.get("sources", []), existing_controls=h.get("existing_controls", []),
            prob=h.get("prob", 3), sev=h.get("sev", 3), risk_value=h.get("risk_value", 9),
            risk_level=h.get("risk_level", "mittel"), additional_measures=measures,
            last_review=h.get("last_review"), reviewer=h.get("reviewer", ""),
            documentation_note=h.get("documentation_note", "")
        ))
    return Assessment(
        company=data["company"], location=data["location"], created_at=data["created_at"],
        created_by=data["created_by"], scope_note=data.get("scope_note", ""),
        risk_matrix_thresholds=data.get("risk_matrix_thresholds", {"thresholds":[6,12,16]}),
        hazards=hazards, measures_plan_note=data.get("measures_plan_note",""),
        documentation_note=data.get("documentation_note",""), next_review_hint=data.get("next_review_hint","")
    )

# =========================
# Vorlagen – Großhotel (fein & mit Starter-Maßnahmen)
# =========================

TEMPLATES: Dict[str, List[Dict[str, Any]]] = {
    "Küche": [
        {
            "activity": "Kochen (Töpfe/Kessel)",
            "hazard": "Hitze, heiße Flüssigkeiten, Verbrühungen/Verbrennungen",
            "sources": ["Herde", "Kessel", "Töpfe", "Heißwasser"],
            "existing": ["Hitzeschutzhandschuhe/-schürzen", "Sichere Griffe/Ablagen", "Unterweisung"],
            "measures": [
                {"title": "Topfdeckel-/Spritzschutz konsequent nutzen", "stop_level": "T (Technisch)", "notes": "Spritzschutz reduziert Dampf/Hitzebelastung"},
                {"title": "Arbeitswege freihalten & ‚Heiß!‘ kommunizieren", "stop_level": "O (Organisatorisch)", "notes": "Teamkommunikation in Stoßzeiten"},
                {"title": "Hitzeschutzhandschuhe bereitstellen/prüfen", "stop_level": "P (PSA)", "notes": "Kennzeichnung der Größen"},
                {"title": "Unterweisung Verbrühungs-/Verbrennungsgefahren", "stop_level": "Q (Qualifikation/Unterweisung)", "notes": "Jährlich + bei Neuzugang"}
            ]
        },
        {
            "activity": "Braten (Pfanne/Grillplatte)",
            "hazard": "Fettspritzer, Verbrennungen, Rauch/Dämpfe",
            "sources": ["Bratpfannen", "Grillplatten"],
            "existing": ["Spritzschutz/Abdeckungen", "Abzugshaube funktionsfähig", "Hitzeschutz"],
            "measures": [
                {"title": "Spritzschutz an Grillplatten nachrüsten/verwenden", "stop_level": "T (Technisch)"},
                {"title": "Abzugshauben reinigen & Luftvolumen prüfen", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutz am Arbeitsplatz bereithalten", "stop_level": "P (PSA)"},
                {"title": "Gefahrstoffe aus Dämpfen thematisieren", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Frittieren",
            "hazard": "Fettbrand, Verbrennungen, Spritzer",
            "sources": ["Fritteusen"],
            "existing": ["Fettbrandlöscher/Löschdecke", "Kein Wasser!", "Unterweisung Fettbrand"],
            "measures": [
                {"title": "Fritteusenhauben/Deckel verwenden", "stop_level": "T (Technisch)"},
                {"title": "Ölwechsel- & Reinigungsplan einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutzhandschuhe & Schürzen verpflichtend", "stop_level": "P (PSA)"},
                {"title": "Fettbrand-Training (Brandklassen, Verhalten)", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Kombidämpfer/Dampfgarer öffnen",
            "hazard": "Dampf/Heißluft – Verbrühung beim Öffnen",
            "sources": ["Kombidämpfer", "Dampfgarer"],
            "existing": ["Tür vorsichtig öffnen", "Hitzeschutz", "Abkühlzeit beachten"],
            "measures": [
                {"title": "Türöffnungsroutine (Kipp/Spalt) standardisieren", "stop_level": "O (Organisatorisch)"},
                {"title": "Hitzeschutzhandschuhe verpflichtend", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: sichere Öffnung/Abdampfen", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Schneiden – Arbeiten mit Messern",
            "hazard": "Schnitt-/Stichverletzungen",
            "sources": ["Messer", "Schneidbretter"],
            "existing": ["Scharfe Messer", "Schnittschutzhandschuhe nach Bedarf", "Messerschulung"],
            "measures": [
                {"title": "Messerschärfservice/Schleifplan einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Schnittschutz-Handschuhe für riskante Schnitte", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Schnitttechnik/Abstellregeln", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Maschinen: Aufschnittmaschine",
            "hazard": "Schnittverletzungen an rotierenden Klingen",
            "sources": ["Aufschnittmaschine"],
            "existing": ["Schutzhauben", "Nur Befugte", "Strom trennen bei Reinigung"],
            "measures": [
                {"title": "Sicherheitsbauteile prüfen (Hauben/Not-Aus)", "stop_level": "T (Technisch)"},
                {"title": "Berechtigungssystem: nur Geschulte bedienen", "stop_level": "O (Organisatorisch)"},
                {"title": "Schnittfeste Handschuhe beim Reinigen", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Reinigung nur stromlos", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Maschinen: Fleischwolf/Gemüseschneider",
            "hazard": "Eingezogenwerden, Schnittverletzung",
            "sources": ["Fleischwolf", "Gemüseschneider"],
            "existing": ["Stopfer benutzen", "Nie mit der Hand nachschieben", "Not-Aus"],
            "measures": [
                {"title": "Stopfer/Schutzvorrichtungen verfügbar halten", "stop_level": "T (Technisch)"},
                {"title": "Betriebsanweisung aushängen/umsetzen", "stop_level": "O (Organisatorisch)"},
                {"title": "Schnittschutzhandschuhe für Reinigungsarbeiten", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Einziehen vermeiden/Not-Aus", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Spülbereich/Stewarding",
            "hazard": "Heißes Wasser/Dampf, Chemikalien, Rutschgefahr",
            "sources": ["Spülmaschine", "Klarspüler", "Nasse Böden"],
            "existing": ["Hand-/Augenschutz", "Rutschhemmende Schuhe", "Boden sofort trockenlegen"],
            "measures": [
                {"title": "Anti-Rutsch-Matten/Absaugung an Engstellen", "stop_level": "T (Technisch)"},
                {"title": "Sofort-Wisch-Regel & Warnschilder", "stop_level": "O (Organisatorisch)"},
                {"title": "Chemikalienschutzhandschuhe bereitstellen", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Dosierung & CLP-Symbole", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Gasbetriebene Geräte",
            "hazard": "Gasleck, CO-Bildung, Brand/Explosion",
            "sources": ["Gasherde", "Leitungen"],
            "existing": ["Dichtheitsprüfung", "Belüftung/Abzug", "Alarmplan/Löscher"],
            "measures": [
                {"title": "Gaswarnmelder installieren/warten", "stop_level": "T (Technisch)"},
                {"title": "Leckcheck-Plan & Freigabe vor Inbetriebnahme", "stop_level": "O (Organisatorisch)"},
                {"title": "CO-Erste-Hilfe-Hinweise aushängen", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Warenannahme/Hubwagen",
            "hazard": "Quetschungen, Heben/Tragen, Verkehrswege",
            "sources": ["Rollcontainer", "Kisten", "Handhubwagen"],
            "existing": ["Rollwagen/Hubhilfe", "Hebetechnik", "Wege frei"],
            "measures": [
                {"title": "Wege kennzeichnen & freihalten", "stop_level": "O (Organisatorisch)"},
                {"title": "Kurzunterweisung ‚Heben/Tragen & Hubwagen‘", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Altöl/Müll entsorgen",
            "hazard": "Verbrennung bei heißem Öl, Schnitt/Infektion",
            "sources": ["Altölbehälter", "Müllsack"],
            "existing": ["Abkühlen lassen", "Dichte Behälter"],
            "measures": [
                {"title": "Altöl-Transportbehälter mit Deckel", "stop_level": "T (Technisch)"},
                {"title": "Entsorgungsanweisung (Wege/Zeiten)", "stop_level": "O (Organisatorisch)"},
                {"title": "Handschutz verpflichtend", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: sichere Entsorgung", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        }
    ],

    "Housekeeping": [
        {
            "activity": "Betten machen",
            "hazard": "Rücken-/Schulterbelastung, Verdrehungen",
            "sources": ["Schwere Matratzen", "Beengte Bereiche"],
            "existing": ["Arbeitstechnik", "Höhenverstellbare Betten/Wagen", "Job-Rotation"],
            "measures": [
                {"title": "Stecklaken-/Ecken-Technik schulen", "stop_level": "Q (Qualifikation/Unterweisung)"},
                {"title": "Leichtere Bettwaren beschaffen", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Arbeitsplatzwechsel im Team planen", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Sanitärreinigung",
            "hazard": "Chemikalienreizungen, Aerosole",
            "sources": ["Reiniger/Desinfektion", "Sprühflaschen"],
            "existing": ["Hautschutzplan", "Hand-/Augenschutz", "Lüften"],
            "measures": [
                {"title": "Vordosierte Kartuschen/Schäume statt Sprühnebel", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Dosierstation & Piktogramme am Waschbecken", "stop_level": "T (Technisch)"},
                {"title": "PSA-Check (Größen, Verfügbarkeit)", "stop_level": "P (PSA)"}
            ]
        },
        {
            "activity": "Fensterreinigung innen",
            "hazard": "Sturz, Schnitt an Glas",
            "sources": ["Fensterfronten", "Tritte/Leitern"],
            "existing": ["Leiterprüfung", "Standflächen sichern"],
            "measures": [
                {"title": "Teleskopstiele statt Leiter (wo möglich)", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Leiterlogin (Prüf- & Benutzregeln) aushängen", "stop_level": "O (Organisatorisch)"},
                {"title": "Schnittfeste Handschuhe bei Bruchgefahr", "stop_level": "P (PSA)"}
            ]
        },
        {
            "activity": "Abfallentsorgung",
            "hazard": "Stich-/Schnittverletzungen, Infektionsgefahr",
            "sources": ["Nadeln", "Scherben"],
            "existing": ["Stichfeste Handschuhe", "Feste Behälter"],
            "measures": [
                {"title": "Sharps-Boxen auf Etagenwagen", "stop_level": "T (Technisch)"},
                {"title": "Wege/Zeiten für Entsorgung festlegen", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung: Nadel-/Scherbenfund", "stop_level": "Q (Qualifikation/Unterweisung)"}
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
                {"title": "Becher-/Tassenablagen gegen Umkippen sichern", "stop_level": "T (Technisch)"},
                {"title": "Dampflanzen-Routine (Ablassen vor Nutzung)", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung ‚Verbrühungen vermeiden‘", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "CO₂/Zapfanlage & Flaschenwechsel",
            "hazard": "Erstickungsgefahr, Hochdruck",
            "sources": ["CO₂-Flaschen", "Keller"],
            "existing": ["CO₂-Warner/Lüftung", "Flaschen sichern"],
            "measures": [
                {"title": "CO₂-Sensoren mit Alarm testen & dokumentieren", "stop_level": "T (Technisch)"},
                {"title": "Wechsel nur zu zweit, Freigabe nach Belüftung", "stop_level": "O (Organisatorisch)"},
                {"title": "Hand-/Augenschutz beim Flaschenwechsel", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Hochdruck & Notfallplan", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Flambieren/Offene Flamme",
            "hazard": "Brand, Alkoholdämpfe",
            "sources": ["Brenner", "Spirituosen"],
            "existing": ["Abstand zu Gästen", "Löschmittel bereit"],
            "measures": [
                {"title": "Brennpasten-/Flambiergeräte mit Rückschlagstopp", "stop_level": "T (Technisch)"},
                {"title": "Freigabe nur für Geschulte, keine Alleinarbeit", "stop_level": "O (Organisatorisch)"},
                {"title": "Brandverhalten/Einsatz Feuerlöscher trainieren", "stop_level": "Q (Qualifikation/Unterweisung)"}
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
                {"title": "Spannungsprüfer/PSA-Klasse bereitstellen", "stop_level": "T (Technisch)"},
                {"title": "LOTO-Verfahren verpflichtend dokumentieren", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung: Arbeiten unter Spannung – verboten", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Heißarbeiten (Schweißen/Trennen)",
            "hazard": "Brand/Explosion, Rauch",
            "sources": ["Schweißgerät", "Schneidbrenner"],
            "existing": ["Heißarbeitsgenehmigung", "Feuerwache/Nachkontrolle"],
            "measures": [
                {"title": "Funkenschutz/Abschirmungen bereitstellen", "stop_level": "T (Technisch)"},
                {"title": "Löschmittel/Feuerlöscher in Griffweite", "stop_level": "O (Organisatorisch)"},
                {"title": "PSA: Schweißerhelm, Handschutz, Kleidung", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Gasführung/Ex-Schutz", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Dach-/Höhenarbeit",
            "hazard": "Absturz",
            "sources": ["Dachkanten", "Gerüste"],
            "existing": ["Absperren", "PSAgA"],
            "measures": [
                {"title": "Anschlagpunkte & Rettungsplan prüfen", "stop_level": "T (Technisch)"},
                {"title": "Zwei-Personen-Regel & Wettercheck", "stop_level": "O (Organisatorisch)"},
                {"title": "Höhensicherungs-PSA prüfen und zuordnen", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Absturzsicherung/Rettung", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        }
    ],

    "Lager/Wareneingang": [
        {
            "activity": "Auspacken/Öffnen",
            "hazard": "Schnittverletzungen, Stolpern",
            "sources": ["Cuttermesser", "Folien/Umreifungen"],
            "existing": ["Sichere Messer", "Müll sofort entsorgen"],
            "measures": [
                {"title": "Sicherheitsmesser (autom. Klingenrückzug)", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Ablageflächen für Kartonagen", "stop_level": "T (Technisch)"},
                {"title": "Müll-Station nahe Rampe definieren", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung: Messerführung/Grifftechnik", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Palettieren/Bewegen",
            "hazard": "Quetschungen, Anfahren",
            "sources": ["Rollcontainer", "Hubwagen"],
            "existing": ["Wege markieren", "Langsam fahren"],
            "measures": [
                {"title": "Anschläge/Stopper an Rampen", "stop_level": "T (Technisch)"},
                {"title": "Verkehrsordnung (Vorfahrt/Signal) aushängen", "stop_level": "O (Organisatorisch)"}
            ]
        },
        {
            "activity": "Einlagern/Greifhöhen",
            "hazard": "Überlastung Rücken/Schulter",
            "sources": ["Hohes Regal", "Schwere Kisten"],
            "existing": ["Leitern/Tritte geprüft", "Rollwagen"],
            "measures": [
                {"title": "Schwere Ware zwischen Knie-/Schulterhöhe", "stop_level": "O (Organisatorisch)"},
                {"title": "Hebehilfe (Lifter) prüfen/anwenden", "stop_level": "T (Technisch)"},
                {"title": "Ergonomie-Kurztraining", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Kohlenstoffdioxid/Kälte TK",
            "hazard": "Kälte, Rutschgefahr",
            "sources": ["Eis/Kondenswasser", "TK-Zonen"],
            "existing": ["Kälteschutz", "Rutschhemmung"],
            "measures": [
                {"title": "Antirutsch-Matten & Eis entfernen", "stop_level": "T (Technisch)"},
                {"title": "Aufenthaltsdauer in TK begrenzen", "stop_level": "O (Organisatorisch)"},
                {"title": "Kälteschutzkleidung Pflicht", "stop_level": "P (PSA)"}
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
                {"title": "Aufguss nur nach festen Regeln/Zeiten", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung: Notfall/Überhitzung", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Pooltechnik/Chemie",
            "hazard": "Gefahrstoffe (Chlor, pH-Regulatoren), Gasfreisetzung",
            "sources": ["Dosier-/Lagerräume"],
            "existing": ["Lüftung/Absaugung", "Augendusche"],
            "measures": [
                {"title": "Chemikalienlager: Auffangwannen/Trennung", "stop_level": "T (Technisch)"},
                {"title": "Freigabe erst nach Gaswarner-Check", "stop_level": "O (Organisatorisch)"},
                {"title": "PSA: Atem-/Hand-/Augenschutz vorhalten", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: TRGS/CLP & Notfallkarten", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Nassbereiche",
            "hazard": "Rutsch-/Sturzgefahr",
            "sources": ["Fliesen", "Wasser"],
            "existing": ["Rutschhemmung", "Reinigungskonzept"],
            "measures": [
                {"title": "Rutschhemmende Matten/Beläge prüfen", "stop_level": "T (Technisch)"},
                {"title": "Sofort-Wisch-Regel & Sperrung bei Nässe", "stop_level": "O (Organisatorisch)"}
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
                {"title": "Rückzugsplatz/Backoffice definieren", "stop_level": "T (Technisch)"},
                {"title": "Stoßzeiten doppelt besetzen", "stop_level": "O (Organisatorisch)"},
                {"title": "Training: Konflikt/Deeskalation", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Nacht-/Alleinarbeit",
            "hazard": "Überfall/Bedrohung, Ermüdung",
            "sources": ["Späte Schichten"],
            "existing": ["Alarmtaster/Video nach Risiko"],
            "measures": [
                {"title": "Stillen Alarm testen & dokumentieren", "stop_level": "T (Technisch)"},
                {"title": "Zwei-Personen-Regel nach Gefährdung", "stop_level": "O (Organisatorisch)"},
                {"title": "Schicht-/Pausenmanagement optimieren", "stop_level": "O (Organisatorisch)"},
                {"title": "Unterweisung: Verhalten bei Überfall", "stop_level": "Q (Qualifikation/Unterweisung)"}
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
                {"title": "Monitore ergonomisch anordnen (Höhe/Abstand)", "stop_level": "T (Technisch)"},
                {"title": "20-20-20-Regel & Mikropausen einführen", "stop_level": "O (Organisatorisch)"},
                {"title": "Sehtest/Bildschirmbrille anbieten", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Drucker/Tonerwechsel",
            "hazard": "Feinstaub, Hautkontakt",
            "sources": ["Toner", "Drucker"],
            "existing": ["Lüftung", "Handschutz"],
            "measures": [
                {"title": "Wechselhandschuhe/Abfallbeutel bereitlegen", "stop_level": "T (Technisch)"},
                {"title": "Wechsel nur in gut belüftetem Raum", "stop_level": "O (Organisatorisch)"}
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
        },
        {
            "activity": "Hecken-/Baumschnitt",
            "hazard": "Schnittverletzung, Absturz",
            "sources": ["Heckenschere", "Leiter"],
            "existing": ["Leiter sichern", "Zwei-Personen-Regel"],
            "measures": [
                {"title": "Teleskopgeräte statt Leiter (wo möglich)", "stop_level": "S (Substitution/Quelle entfernen)"},
                {"title": "Anseilschutz/PSAgA bei Höhe einsetzen", "stop_level": "P (PSA)"},
                {"title": "Unterweisung: Motorsense/Heckenschere", "stop_level": "Q (Qualifikation/Unterweisung)"}
            ]
        },
        {
            "activity": "Winterdienst",
            "hazard": "Rutschen, Kälte",
            "sources": ["Eis/Schnee"],
            "existing": ["Räum-/Streuplan"],
            "measures": [
                {"title": "Rutschhemmende Spikes/Schuhe", "stop_level": "P (PSA)"},
                {"title": "Frühstartplan & Prioritätswege", "stop_level": "O (Organisatorisch)"}
            ]
        }
    ]
}

# =========================
# Vorladen inkl. Starter-Maßnahmen
# =========================

def preload_template(assess: Assessment):
    # Lädt alle TEMPLATES inkl. optionaler Start-Maßnahmen (item["measures"])
    for area, items in TEMPLATES.items():
        for item in items:
            hz = Hazard(
                id=new_id(),
                area=area,
                activity=item["activity"],
                hazard=item["hazard"],
                sources=item.get("sources", []),
                existing_controls=item.get("existing", [])
            )
            # Optionale initiale Maßnahmen hinzufügen
            for m in item.get("measures", []):
                hz.additional_measures.append(Measure(
                    title=m["title"],
                    stop_level=m["stop_level"],
                    notes=m.get("notes", "")
                ))
            assess.hazards.append(hz)

# =========================
# Streamlit App
# =========================

st.set_page_config(page_title="Gefährdungsbeurteilung Großhotel", layout="wide")

if "assessment" not in st.session_state:
    st.session_state.assessment = Assessment(
        company="Musterhotel GmbH",
        location="Beispielstadt",
        created_at=date.today().isoformat(),
        created_by="HSE/SiFa",
    )
    preload_template(st.session_state.assessment)

assess: Assessment = st.session_state.assessment

st.title("Gefährdungsbeurteilung – Großhotel")
st.caption("Struktur gemäß BAuA: Vorbereiten → Ermitteln → Beurteilen → Maßnahmen → Umsetzen → Wirksamkeit → Dokumentieren → Fortschreiben")

# Seitenleiste: Meta & Konfiguration
with st.sidebar:
    st.header("Stammdaten")
    assess.company = st.text_input("Unternehmen", assess.company, key="meta_company")
    assess.location = st.text_input("Standort", assess.location, key="meta_location")
    assess.created_by = st.text_input("Erstellt von", assess.created_by, key="meta_created_by")
    assess.created_at = st.text_input("Erstellt am (ISO)", assess.created_at, key="meta_created_at")

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
    st.info("Tipp: Bereiche und Tätigkeiten definieren; vorhandene Unterlagen (BA, BAuA-Infos, Betriebsanweisungen, Betriebsanleitungen) sammeln.")

# 2 Ermitteln
with tabs[1]:
    st.subheader("2) Gefährdungen ermitteln")
    colL, colR = st.columns([2,1])

    with colL:
        st.markdown("**Gefährdungen (Bearbeiten)**")
        df = pd.DataFrame([hazard_to_row(h) for h in assess.hazards])
        st.dataframe(df, use_container_width=True, hide_index=True, key="df_hazards")

        with st.expander("➕ Gefährdung hinzufügen"):
            col1, col2 = st.columns(2)
            area = col1.selectbox("Bereich", sorted(list(TEMPLATES.keys()) + ["Sonstiges"]), key="add_area")
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
            hz.area = st.selectbox(
                "Bereich",
                options=list(TEMPLATES.keys()) + ["Sonstiges"],
                index=(list(TEMPLATES.keys()) + ["Sonstiges"]).index(hz.area) if hz.area in (list(TEMPLATES.keys()) + ["Sonstiges"]) else 0,
                key=f"edit_area_{hz.id}"
            )
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
        top = sorted(assess.hazards, key=lambda x: x.risk_value, reverse=True)[:10]
        top_df = pd.DataFrame([{"ID":h.id, "Bereich":h.area, "Gefährdung":h.hazard, "Risiko":h.risk_value, "Stufe":h.risk_level} for h in top])
        st.dataframe(top_df, hide_index=True, use_container_width=True, key="df_top_risks")

# 4 Maßnahmen
with tabs[3]:
    st.subheader("4) Maßnahmen festlegen (STOP + Q)")
    st.caption("Zuerst an der Quelle vermeiden/vermindern, dann technisch, organisatorisch, PSA – ggf. Qualifikation/Unterweisung ergänzen.")

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
    by_area = pd.DataFrame(pd.Series([h.area for h in assess.hazards]).value_counts(), columns=["Anzahl"])
    st.markdown("**Gefährdungen je Bereich**")
    st.dataframe(by_area, use_container_width=True, key="df_by_area")

    st.markdown("**Hinweise**")
    assess.measures_plan_note = st.text_area("Projekt-/Maßnahmenplan (kurz)", value=assess.measures_plan_note, key="measures_plan_note")
